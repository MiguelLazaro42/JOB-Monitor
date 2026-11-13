"""
Resolvedor de inteligencia IP/Dominio (ASN, WHOIS, país, favicon) con caché PostgreSQL + espejo SQLite.

- `get_or_enqueue_intel()` NUNCA bloquea: responde desde `_MEM_CACHE` (0 ms) y delega el enriquecimiento
  a hilos en segundo plano con límite estricto de peticiones HTTP externas (ventana deslizante).
- Almacenamiento: PostgreSQL (`RA_PG_DSN`) si psycopg2 está instalado y el servidor es alcanzable;
  SQLite (`ip_domain_intel` en remote_assist.db) siempre se mantiene como espejo/fallback.
"""
import ipaddress
import json
import logging
import os
import queue
import re
import socket
import struct
import sqlite3
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import config

try:
    from server.service_catalog import ADGUARD_DOMAIN_CATALOG, lookup_service_catalog, match_service_domain_or_ip
except ImportError:  # el servidor se ejecuta con server/ como directorio raíz
    from service_catalog import ADGUARD_DOMAIN_CATALOG, lookup_service_catalog, match_service_domain_or_ip

try:
    import psycopg2  # type: ignore
except Exception:  # psycopg2 es opcional (desarrollo local sin Postgres)
    psycopg2 = None

log = logging.getLogger("intel_resolver")

PG_INTEL_DSN = os.environ.get("RA_PG_DSN", "postgresql://jobmonitor:sitai_pg_intel_2026@127.0.0.1:5432/jobmonitor_intel")
SQLITE_PATH: Path = config.DATA_DIR / "remote_assist.db"

MAX_REQUESTS_PER_MINUTE = 30
MAX_API_REQUESTS_PER_MIN = MAX_REQUESTS_PER_MINUTE
MAX_API_REQUESTS_PER_HOUR = 400
MAX_CONCURRENT_WORKERS = 4
HTTP_TIMEOUT = 3.5
DNS_TIMEOUT = 2.5
PG_RETRY_SECONDS = 60
FAILED_RETRY_SECONDS = 600
MAX_ATTEMPTS = 3
ENRICH_HTTP_TIMEOUT = 1.5      # paso 2 opcional (ip-api.com)
HTTP_BREAKER_FAILS = 3         # fallos HTTP seguidos antes de pausar el enriquecimiento HTTP
HTTP_BREAKER_PAUSE = 600       # segundos (servidor sin salida HTTP, p. ej. .72)
SEED_INTERVAL = 120

PG_DDL = """
CREATE TABLE IF NOT EXISTS ip_domain_intel (
    lookup_key VARCHAR(255) PRIMARY KEY,
    is_ip BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_domain VARCHAR(255) NOT NULL DEFAULT '',
    org_name VARCHAR(255) NOT NULL DEFAULT '',
    asn VARCHAR(64) NOT NULL DEFAULT '',
    asn_name VARCHAR(255) NOT NULL DEFAULT '',
    whois_org VARCHAR(255) NOT NULL DEFAULT '',
    country_code VARCHAR(8) NOT NULL DEFAULT '',
    country_name VARCHAR(128) NOT NULL DEFAULT '',
    favicon_url TEXT NOT NULL DEFAULT '',
    category VARCHAR(32) NOT NULL DEFAULT 'OTHER',
    lookup_source VARCHAR(64) NOT NULL DEFAULT '',
    hit_count BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS ip_domain_intel (
    lookup_key TEXT PRIMARY KEY,
    is_ip INTEGER NOT NULL DEFAULT 0,
    resolved_domain TEXT NOT NULL DEFAULT '',
    org_name TEXT NOT NULL DEFAULT '',
    asn TEXT NOT NULL DEFAULT '',
    asn_name TEXT NOT NULL DEFAULT '',
    whois_org TEXT NOT NULL DEFAULT '',
    country_code TEXT NOT NULL DEFAULT '',
    country_name TEXT NOT NULL DEFAULT '',
    favicon_url TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'OTHER',
    lookup_source TEXT NOT NULL DEFAULT '',
    hit_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_COLS = ("lookup_key", "is_ip", "resolved_domain", "org_name", "asn", "asn_name", "whois_org", "country_code",
         "country_name", "favicon_url", "category", "lookup_source", "hit_count", "created_at", "updated_at")

# Fuentes que no son resultados finales de red (no se cuentan como "persistidos").
_TRANSIENT_SOURCES = ("pending", "failed", "catalog", "local")

_MEM_CACHE: Dict[str, dict] = {}
_IN_FLIGHT: set = set()
_LOCK = threading.Lock()
_QUEUE: "queue.Queue" = queue.Queue(maxsize=5000)
_API_CALLS: deque = deque()  # timestamps (time.monotonic) de peticiones HTTP externas
_ATTEMPTS: Dict[str, int] = {}
_started = False
_start_lock = threading.Lock()

_pg_lock = threading.Lock()
_pg_conn = None
_pg_connected = False
_pg_next_try = 0.0

# ASN -> (nombre comercial, dominio para favicon/servicio, categoría)
_ASN_BRANDS = {
    "AS54113": ("Fastly CDN", "fastly.com", "CLOUD"),
    "AS13335": ("Cloudflare, Inc.", "cloudflare.com", "CLOUD"),
    "AS209242": ("Cloudflare, Inc.", "cloudflare.com", "CLOUD"),
    "AS16509": ("Amazon AWS", "amazonaws.com", "CLOUD"),
    "AS14618": ("Amazon AWS", "amazonaws.com", "CLOUD"),
    "AS15169": ("Google LLC", "google.com", "WORK"),
    "AS396982": ("Google Cloud", "cloud.google.com", "CLOUD"),
    "AS36040": ("Google LLC", "google.com", "WORK"),
    "AS19527": ("Google LLC", "google.com", "WORK"),
    "AS8075": ("Microsoft Corporation", "microsoft.com", "WORK"),
    "AS8068": ("Microsoft Corporation", "microsoft.com", "WORK"),
    "AS714": ("Apple Inc.", "apple.com", "SYSTEM"),
    "AS6185": ("Apple Inc.", "apple.com", "SYSTEM"),
    "AS32934": ("Meta Platforms", "facebook.com", "SOCIAL"),
    "AS20940": ("Akamai CDN", "akamai.com", "CLOUD"),
    "AS16625": ("Akamai CDN", "akamai.com", "CLOUD"),
    "AS2906": ("Netflix", "netflix.com", "STREAMING"),
    "AS40027": ("Netflix", "netflix.com", "STREAMING"),
    "AS46489": ("Twitch", "twitch.tv", "STREAMING"),
    "AS32590": ("Valve / Steam", "steampowered.com", "GAMING"),
    "AS62041": ("Telegram", "telegram.org", "MESSAGING"),
    "AS14061": ("DigitalOcean", "digitalocean.com", "CLOUD"),
    "AS63949": ("Linode / Akamai", "linode.com", "CLOUD"),
    "AS24940": ("Hetzner Online", "hetzner.com", "CLOUD"),
    "AS16276": ("OVHcloud", "ovhcloud.com", "CLOUD"),
    "AS36459": ("GitHub, Inc.", "github.com", "WORK"),
    "AS8987": ("Amazon AWS", "amazonaws.com", "CLOUD"),
    "AS13414": ("X / Twitter", "x.com", "SOCIAL"),
    "AS8452": ("Telecom Egypt", "te.eg", "OTHER"),
}
# Tabla ampliada "ASN|Marca|dominio|CATEGORIA" (funciona 100% offline; el favicon usa el dominio).
_ASN_EXTRA = """
AS16509|Amazon AWS|aws.amazon.com|CLOUD
AS14618|Amazon AWS|aws.amazon.com|CLOUD
AS8987|Amazon AWS|aws.amazon.com|CLOUD
AS396982|Google LLC|google.com|WORK
AS15169|Google LLC|google.com|WORK
AS36384|Google LLC|google.com|WORK
AS36385|Google LLC|google.com|WORK
AS36492|Google LLC|google.com|WORK
AS41264|Google LLC|google.com|WORK
AS43515|YouTube (Google)|youtube.com|STREAMING
AS36561|YouTube (Google)|youtube.com|STREAMING
AS132892|Cloudflare, Inc.|cloudflare.com|CLOUD
AS12222|Akamai Technologies|akamai.com|CLOUD
AS21342|Akamai Technologies|akamai.com|CLOUD
AS21399|Akamai Technologies|akamai.com|CLOUD
AS34164|Akamai Technologies|akamai.com|CLOUD
AS35994|Akamai Technologies|akamai.com|CLOUD
AS32787|Akamai Technologies|akamai.com|CLOUD
AS20940|Akamai Technologies|akamai.com|CLOUD
AS16625|Akamai Technologies|akamai.com|CLOUD
AS8069|Microsoft Corporation|microsoft.com|WORK
AS8070|Microsoft Corporation|microsoft.com|WORK
AS12076|Microsoft Corporation|microsoft.com|WORK
AS3598|Microsoft Corporation|microsoft.com|WORK
AS6584|Microsoft Corporation|microsoft.com|WORK
AS14413|LinkedIn (Microsoft)|linkedin.com|SOCIAL
AS40793|LinkedIn (Microsoft)|linkedin.com|SOCIAL
AS32934|Meta / Facebook|facebook.com|SOCIAL
AS63293|Meta / Facebook|facebook.com|SOCIAL
AS55095|Netflix|netflix.com|STREAMING
AS2906|Netflix|netflix.com|STREAMING
AS40027|Netflix|netflix.com|STREAMING
AS62014|Telegram Messenger|telegram.org|MESSAGING
AS62041|Telegram Messenger|telegram.org|MESSAGING
AS59930|Telegram Messenger|telegram.org|MESSAGING
AS44907|Telegram Messenger|telegram.org|MESSAGING
AS211157|Telegram Messenger|telegram.org|MESSAGING
AS22697|Roblox|roblox.com|GAMING
AS6507|Riot Games|riotgames.com|GAMING
AS57976|Blizzard Entertainment|blizzard.com|GAMING
AS714|Apple Inc.|apple.com|CLOUD
AS6185|Apple Inc.|apple.com|CLOUD
AS138699|TikTok / ByteDance|tiktok.com|SOCIAL
AS396986|TikTok / ByteDance|tiktok.com|SOCIAL
AS63949|Linode / Akamai|linode.com|CLOUD
AS8403|Spotify|spotify.com|STREAMING
AS30103|Zoom Video Communications|zoom.us|WORK
AS19679|Dropbox|dropbox.com|WORK
AS14340|Salesforce|salesforce.com|WORK
AS15224|Adobe|adobe.com|WORK
AS17012|PayPal|paypal.com|WORK
AS62955|eBay|ebay.com|OTHER
AS2635|Automattic / WordPress|wordpress.com|CLOUD
AS14907|Wikimedia Foundation|wikimedia.org|OTHER
AS43821|Wikimedia Foundation|wikimedia.org|OTHER
AS10310|Yahoo|yahoo.com|OTHER
AS26101|Yahoo|yahoo.com|OTHER
AS109|Cisco Systems|cisco.com|WORK
AS31898|Oracle Cloud|oracle.com|CLOUD
AS792|Oracle|oracle.com|CLOUD
AS36351|IBM SoftLayer|ibm.com|CLOUD
AS19994|Rackspace|rackspace.com|CLOUD
AS20473|Vultr / Choopa|vultr.com|CLOUD
AS51167|Contabo|contabo.com|CLOUD
AS12876|Scaleway|scaleway.com|CLOUD
AS60068|CDN77 / Datacamp|cdn77.com|CLOUD
AS9009|M247|m247.com|CLOUD
AS26496|GoDaddy|godaddy.com|CLOUD
AS398101|GoDaddy|godaddy.com|CLOUD
AS46606|Unified Layer / Bluehost|bluehost.com|CLOUD
AS26347|DreamHost|dreamhost.com|CLOUD
AS32244|Liquid Web|liquidweb.com|CLOUD
AS54825|Equinix Metal|equinix.com|CLOUD
AS15830|Equinix|equinix.com|CLOUD
AS60781|Leaseweb|leaseweb.com|CLOUD
AS28753|Leaseweb|leaseweb.com|CLOUD
AS15133|Edgio / Edgecast|edg.io|CLOUD
AS20446|StackPath / Highwinds|stackpath.com|CLOUD
AS33438|StackPath / Highwinds|stackpath.com|CLOUD
AS19551|Imperva / Incapsula|imperva.com|CLOUD
AS30148|Sucuri|sucuri.net|CLOUD
AS54994|Quantil CDN|quantil.com|CLOUD
AS13238|Yandex|yandex.ru|OTHER
AS47541|VK|vk.com|SOCIAL
AS47764|Mail.ru / VK|mail.ru|OTHER
AS45090|Tencent|tencent.com|OTHER
AS132203|Tencent Cloud|tencent.com|CLOUD
AS37963|Alibaba Cloud|alibabacloud.com|CLOUD
AS45102|Alibaba Cloud|alibabacloud.com|CLOUD
AS38365|Baidu|baidu.com|OTHER
AS55967|Baidu|baidu.com|OTHER
AS4134|China Telecom|chinatelecom.com.cn|ISP
AS4837|China Unicom|chinaunicom.com|ISP
AS9808|China Mobile|chinamobile.com|ISP
AS17676|SoftBank (Japón)|softbank.jp|ISP
AS2516|KDDI (Japón)|kddi.com|ISP
AS4713|NTT Communications (OCN)|ntt.com|ISP
AS2914|NTT Global IP Network|ntt.com|ISP
AS3356|Lumen / Level 3|lumen.com|ISP
AS209|Lumen / CenturyLink|lumen.com|ISP
AS174|Cogent Communications|cogentco.com|ISP
AS1299|Telia Carrier|teliacompany.com|ISP
AS6762|Telecom Italia Sparkle|tisparkle.com|ISP
AS6453|Tata Communications|tatacommunications.com|ISP
AS3257|GTT Communications|gtt.net|ISP
AS6939|Hurricane Electric|he.net|ISP
AS7018|AT&T|att.com|ISP
AS20057|AT&T Mobility|att.com|ISP
AS701|Verizon|verizon.com|ISP
AS702|Verizon|verizon.com|ISP
AS22394|Verizon Wireless|verizon.com|ISP
AS7922|Comcast Xfinity|xfinity.com|ISP
AS20115|Charter Spectrum|spectrum.com|ISP
AS11427|Charter Spectrum|spectrum.com|ISP
AS22773|Cox Communications|cox.com|ISP
AS21928|T-Mobile USA|t-mobile.com|ISP
AS3320|Deutsche Telekom|telekom.de|ISP
AS3215|Orange (Francia)|orange.fr|ISP
AS12322|Free (Francia)|free.fr|ISP
AS5089|Virgin Media|virginmedia.com|ISP
AS2856|BT (Reino Unido)|bt.com|ISP
AS12956|Telefónica|telefonica.com|ISP
AS3352|Telefónica España|movistar.es|ISP
AS8151|Telmex / Uninet (México)|telmex.com|ISP
AS28548|Totalplay (México)|totalplay.com.mx|ISP
AS17072|Totalplay (México)|totalplay.com.mx|ISP
AS13999|Megacable (México)|megacable.com.mx|ISP
AS28403|Izzi Telecom (México)|izzi.mx|ISP
AS11888|Izzi / Televisa (México)|izzi.mx|ISP
AS11172|Alestra (México)|alestra.com.mx|ISP
AS28573|Claro Brasil|claro.com.br|ISP
AS18881|Vivo / Telefônica Brasil|vivo.com.br|ISP
AS27699|Vivo / Telefônica Brasil|vivo.com.br|ISP
AS7303|Telecom Argentina|telecom.com.ar|ISP
AS10318|Cablevisión Argentina|flow.com.ar|ISP
AS27747|Telecentro (Argentina)|telecentro.com.ar|ISP
AS10620|Claro / Telmex Colombia|claro.com.co|ISP
AS6147|Telefónica Perú|movistar.com.pe|ISP
AS7418|Telefónica Chile|movistar.cl|ISP
AS27651|Entel Chile|entel.cl|ISP
AS22047|VTR (Chile)|vtr.com|ISP
AS14061|DigitalOcean|digitalocean.com|CLOUD
AS24940|Hetzner|hetzner.com|CLOUD
AS16276|OVHcloud|ovhcloud.com|CLOUD
AS36459|GitHub|github.com|WORK
AS46489|Twitch|twitch.tv|STREAMING
AS32590|Valve / Steam|steampowered.com|GAMING
AS54113|Fastly CDN|fastly.com|CLOUD
AS13335|Cloudflare|cloudflare.com|CLOUD
AS8075|Microsoft Corporation|microsoft.com|WORK
"""
for _ln in _ASN_EXTRA.strip().splitlines():
    _a, _n, _d, _c = _ln.split("|")
    _ASN_BRANDS[_a] = (_n, _d, _c)
# Palabras clave en org/isp/PTR cuando el ASN no está en la tabla.
_ORG_KEYWORDS = (
    ("fastly", "Fastly CDN", "fastly.com", "CLOUD"),
    ("cloudflare", "Cloudflare, Inc.", "cloudflare.com", "CLOUD"),
    ("akamai", "Akamai CDN", "akamai.com", "CLOUD"),
    ("amazon", "Amazon AWS", "amazonaws.com", "CLOUD"),
    ("google", "Google LLC", "google.com", "WORK"),
    ("microsoft", "Microsoft Corporation", "microsoft.com", "WORK"),
    ("apple", "Apple Inc.", "apple.com", "SYSTEM"),
    ("facebook", "Meta Platforms", "facebook.com", "SOCIAL"),
    ("meta platforms", "Meta Platforms", "facebook.com", "SOCIAL"),
    ("github", "GitHub, Inc.", "github.com", "WORK"),
    ("bytedance", "TikTok / ByteDance", "tiktok.com", "SOCIAL"),
    ("tiktok", "TikTok / ByteDance", "tiktok.com", "SOCIAL"),
    ("netflix", "Netflix", "netflix.com", "STREAMING"),
    ("twitch", "Twitch", "twitch.tv", "STREAMING"),
    ("roblox", "Roblox", "roblox.com", "GAMING"),
    ("riot games", "Riot Games", "riotgames.com", "GAMING"),
    ("valve", "Valve / Steam", "steampowered.com", "GAMING"),
    ("telegram", "Telegram Messenger", "telegram.org", "MESSAGING"),
    ("whatsapp", "WhatsApp (Meta)", "whatsapp.com", "MESSAGING"),
    ("digitalocean", "DigitalOcean", "digitalocean.com", "CLOUD"),
    ("hetzner", "Hetzner", "hetzner.com", "CLOUD"),
    ("ovh", "OVHcloud", "ovhcloud.com", "CLOUD"),
    ("linode", "Linode / Akamai", "linode.com", "CLOUD"),
    ("vultr", "Vultr", "vultr.com", "CLOUD"),
    ("oracle", "Oracle Cloud", "oracle.com", "CLOUD"),
    ("alibaba", "Alibaba Cloud", "alibabacloud.com", "CLOUD"),
    ("tencent", "Tencent", "tencent.com", "OTHER"),
    ("spotify", "Spotify", "spotify.com", "STREAMING"),
    ("dropbox", "Dropbox", "dropbox.com", "WORK"),
    ("zoom video", "Zoom Video Communications", "zoom.us", "WORK"),
    ("salesforce", "Salesforce", "salesforce.com", "WORK"),
    ("telmex", "Telmex / Uninet (México)", "telmex.com", "ISP"),
    ("uninet", "Telmex / Uninet (México)", "telmex.com", "ISP"),
    ("totalplay", "Totalplay (México)", "totalplay.com.mx", "ISP"),
    ("megacable", "Megacable (México)", "megacable.com.mx", "ISP"),
    ("izzi", "Izzi Telecom (México)", "izzi.mx", "ISP"),
    ("cablevision", "Izzi / Cablevisión", "izzi.mx", "ISP"),
    ("alestra", "Alestra (México)", "alestra.com.mx", "ISP"),
    ("telefonica", "Telefónica", "telefonica.com", "ISP"),
    ("comcast", "Comcast Xfinity", "xfinity.com", "ISP"),
    ("verizon", "Verizon", "verizon.com", "ISP"),
    ("at&t", "AT&T", "att.com", "ISP"),
    ("level 3", "Lumen / Level 3", "lumen.com", "ISP"),
    ("lumen", "Lumen / Level 3", "lumen.com", "ISP"),
    ("cogent", "Cogent Communications", "cogentco.com", "ISP"),
    ("edgecast", "Edgio / Edgecast", "edg.io", "CLOUD"),
    ("stackpath", "StackPath", "stackpath.com", "CLOUD"),
    ("incapsula", "Imperva / Incapsula", "imperva.com", "CLOUD"),
    ("rackspace", "Rackspace", "rackspace.com", "CLOUD"),
    ("softlayer", "IBM SoftLayer", "ibm.com", "CLOUD"),
    ("godaddy", "GoDaddy", "godaddy.com", "CLOUD"),
    ("yahoo", "Yahoo", "yahoo.com", "OTHER"),
    ("twitter", "X / Twitter", "x.com", "SOCIAL"),
)

_COUNTRY_NAMES = {
    "US": "United States", "MX": "Mexico", "CA": "Canada", "BR": "Brazil", "AR": "Argentina", "CL": "Chile",
    "CO": "Colombia", "PE": "Peru", "VE": "Venezuela", "UY": "Uruguay", "EC": "Ecuador", "CR": "Costa Rica",
    "PA": "Panama", "GT": "Guatemala", "DO": "Dominican Republic", "CU": "Cuba", "GB": "United Kingdom",
    "IE": "Ireland", "FR": "France", "DE": "Germany", "NL": "Netherlands", "BE": "Belgium", "LU": "Luxembourg",
    "CH": "Switzerland", "AT": "Austria", "ES": "Spain", "PT": "Portugal", "IT": "Italy", "SE": "Sweden",
    "NO": "Norway", "DK": "Denmark", "FI": "Finland", "IS": "Iceland", "PL": "Poland", "CZ": "Czechia",
    "SK": "Slovakia", "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria", "GR": "Greece", "TR": "Turkey",
    "UA": "Ukraine", "RU": "Russia", "BY": "Belarus", "LT": "Lithuania", "LV": "Latvia", "EE": "Estonia",
    "IL": "Israel", "AE": "United Arab Emirates", "SA": "Saudi Arabia", "EG": "Egypt", "ZA": "South Africa",
    "NG": "Nigeria", "KE": "Kenya", "IN": "India", "PK": "Pakistan", "BD": "Bangladesh", "CN": "China",
    "HK": "Hong Kong", "TW": "Taiwan", "JP": "Japan", "KR": "South Korea", "SG": "Singapore", "MY": "Malaysia",
    "TH": "Thailand", "VN": "Vietnam", "ID": "Indonesia", "PH": "Philippines", "AU": "Australia",
    "NZ": "New Zealand", "KZ": "Kazakhstan", "IR": "Iran", "IQ": "Iraq", "QA": "Qatar",
}

_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


# ---------------------------------------------------------------- utilidades

def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _is_ip(val: str) -> bool:
    try:
        ipaddress.ip_address(val)
        return True
    except ValueError:
        return False


def _is_public_ip(val: str) -> bool:
    try:
        ip = ipaddress.ip_address(val)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _valid_domain(val: str) -> bool:
    return bool(val) and bool(_DOMAIN_RE.match(val.lower()))


def favicon_for(domain: str) -> str:
    domain = (domain or "").strip().lower()
    if not _valid_domain(domain):
        return ""
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=32"


def _normalize_key(val: str) -> str:
    return (val or "").strip().lower().rstrip(".")[:255]


def _blank_entry(key: str, source: str) -> dict:
    now = _now_str()
    return {
        "lookup_key": key, "is_ip": _is_ip(key), "resolved_domain": "", "org_name": "", "asn": "", "asn_name": "",
        "whois_org": "", "country_code": "", "country_name": "", "favicon_url": "", "category": "OTHER",
        "lookup_source": source, "hit_count": 1, "created_at": now, "updated_at": now, "_ts": time.monotonic(),
    }


def _run_with_timeout(fn, arg, timeout: float):
    """Ejecuta una resolución DNS bloqueante sin dejar colgado al worker más de `timeout` s."""
    box: list = []

    def _target():
        try:
            box.append(fn(arg))
        except Exception:
            box.append(None)

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    return box[0] if box else None


# ---------------------------------------------------------------- rate limiter

def _prune(now: float) -> None:
    while _API_CALLS and now - _API_CALLS[0] > 3600:
        _API_CALLS.popleft()


def _requests_in_window(seconds: float) -> int:
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        return sum(1 for t in _API_CALLS if now - t <= seconds)


def _reserve_slot() -> bool:
    """Ventana deslizante: reserva atómicamente una petición HTTP externa si hay presupuesto."""
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        last_min = sum(1 for t in _API_CALLS if now - t <= 60)
        if last_min >= MAX_API_REQUESTS_PER_MIN or len(_API_CALLS) >= MAX_API_REQUESTS_PER_HOUR:
            return False
        _API_CALLS.append(now)
        return True


def _seconds_until_slot() -> float:
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        waits = []
        recent = [t for t in _API_CALLS if now - t <= 60]
        if len(recent) >= MAX_API_REQUESTS_PER_MIN:
            waits.append(60 - (now - recent[0]))
        if len(_API_CALLS) >= MAX_API_REQUESTS_PER_HOUR:
            waits.append(3600 - (now - _API_CALLS[0]))
        return max(1.0, max(waits)) if waits else 1.0


class _NoBudget(Exception):
    pass


def _http_json(url: str, timeout: Optional[float] = None) -> Optional[dict]:
    if not _reserve_slot():
        raise _NoBudget()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "monitor-intel/1.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout or HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read(200_000).decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        log.debug("intel http fail %s: %s", url.split("?")[0], exc)
        return None


# ---------------------------------------------------------------- DNS crudo (8.8.8.8) + Team Cymru
# El servidor .72 solo tiene salida UDP/53 hacia 8.8.8.8 / 8.8.4.4: ASN/país/org se resuelven por DNS.

DNS_SERVERS = ("8.8.8.8", "8.8.4.4")
_QTYPE_A, _QTYPE_PTR, _QTYPE_TXT, _QTYPE_AAAA = 1, 12, 16, 28


def _dns_encode_name(qname: str) -> bytes:
    out = b""
    for label in qname.strip(".").split("."):
        raw = label.encode("idna")
        if not raw or len(raw) > 63:
            raise ValueError("etiqueta DNS inválida")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _dns_read_name(msg: bytes, off: int):
    """Lee un nombre (con compresión). Retorna (nombre, offset_siguiente)."""
    labels, jumped, end = [], False, off
    for _ in range(128):
        if off >= len(msg):
            raise ValueError("nombre DNS truncado")
        ln = msg[off]
        if ln == 0:
            off += 1
            break
        if ln & 0xC0 == 0xC0:
            ptr = ((ln & 0x3F) << 8) | msg[off + 1]
            if not jumped:
                end = off + 2
            jumped, off = True, ptr
            continue
        labels.append(msg[off + 1:off + 1 + ln].decode("ascii", "replace"))
        off += 1 + ln
    else:
        raise ValueError("bucle de compresión DNS")
    return ".".join(labels), (end if jumped else off)


def _dns_parse(msg: bytes, qtype: int) -> List[str]:
    if len(msg) < 12:
        return []
    _id, flags, qd, an = struct.unpack("!HHHH", msg[:8])
    if flags & 0x000F:  # RCODE != 0 (NXDOMAIN, SERVFAIL...)
        return []
    off = 12
    for _ in range(qd):
        _n, off = _dns_read_name(msg, off)
        off += 4
    results: List[str] = []
    for _ in range(an):
        _n, off = _dns_read_name(msg, off)
        rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", msg[off:off + 10])
        off += 10
        rdata = msg[off:off + rdlen]
        if rtype == qtype:
            if qtype == _QTYPE_TXT:
                parts, i = [], 0
                while i < len(rdata):
                    n = rdata[i]
                    parts.append(rdata[i + 1:i + 1 + n].decode("utf-8", "replace"))
                    i += 1 + n
                results.append("".join(parts))
            elif qtype == _QTYPE_PTR:
                results.append(_dns_read_name(msg, off)[0])
            elif qtype == _QTYPE_A and rdlen == 4:
                results.append(socket.inet_ntoa(rdata))
            elif qtype == _QTYPE_AAAA and rdlen == 16:
                results.append(socket.inet_ntop(socket.AF_INET6, rdata))
        off += rdlen
    return results


def _dns_query_8888(qname: str, qtype: int, timeout: float = 2.0) -> List[str]:
    """Consulta DNS pura (UDP, con reintento TCP si viene truncada) a 8.8.8.8 y luego 8.8.4.4.
    qtype: 1=A, 12=PTR, 16=TXT, 28=AAAA. Retorna lista de strings ([] si no hay respuesta)."""
    try:
        qbody = _dns_encode_name(qname) + struct.pack("!HH", qtype, 1)
    except Exception:
        return []
    for server in DNS_SERVERS:
        txid = int.from_bytes(os.urandom(2), "big")
        packet = struct.pack("!HHHHHH", txid, 0x0100, 1, 0, 0, 0) + qbody
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.settimeout(timeout)
                sock.sendto(packet, (server, 53))
                deadline = time.monotonic() + timeout
                while True:
                    data, _addr = sock.recvfrom(4096)
                    if len(data) >= 2 and struct.unpack("!H", data[:2])[0] == txid:
                        break
                    if time.monotonic() > deadline:
                        raise socket.timeout()
            finally:
                sock.close()
            if len(data) >= 4 and struct.unpack("!H", data[2:4])[0] & 0x0200:  # TC -> reintentar por TCP
                tcp = socket.create_connection((server, 53), timeout=timeout)
                try:
                    tcp.settimeout(timeout)
                    tcp.sendall(struct.pack("!H", len(packet)) + packet)
                    hdr = tcp.recv(2)
                    need = struct.unpack("!H", hdr)[0]
                    buf = b""
                    while len(buf) < need:
                        chunk = tcp.recv(need - len(buf))
                        if not chunk:
                            break
                        buf += chunk
                    data = buf
                finally:
                    tcp.close()
            return _dns_parse(data, qtype)
        except Exception as exc:
            log.debug("dns %s %s fail: %s", server, qname, exc)
            continue
    return []


def _reverse_name(ip: str) -> str:
    addr = ipaddress.ip_address(ip)
    if addr.version == 4:
        return ".".join(reversed(ip.split(".")))
    return ".".join(reversed(addr.exploded.replace(":", ""))) 


def _dns_ptr(ip: str, timeout: float = 2.0) -> str:
    try:
        rev = _reverse_name(ip) + (".in-addr.arpa" if ":" not in ip else ".ip6.arpa")
    except ValueError:
        return ""
    res = _dns_query_8888(rev, _QTYPE_PTR, timeout)
    return _normalize_key(res[0]) if res else ""


def _txt_fields(txt: str) -> List[str]:
    return [f.strip() for f in txt.strip().strip('"').split("|")]


def _cymru_lookup(ip: str, timeout: float = 2.0) -> Optional[dict]:
    """ASN, prefijo BGP, país, registro y nombre de la organización vía Team Cymru sobre DNS (puerto 53)."""
    try:
        rev = _reverse_name(ip)
    except ValueError:
        return None
    zone = "origin.asn.cymru.com" if ":" not in ip else "origin6.asn.cymru.com"
    rows = _dns_query_8888(f"{rev}.{zone}", _QTYPE_TXT, timeout)
    if not rows:
        return None
    f = _txt_fields(rows[0])  # "54113 | 199.232.156.0/22 | US | arin | 2016-05-04"
    if len(f) < 3 or not f[0]:
        return None
    asn_num = f[0].split()[0]
    if not asn_num.isdigit():
        return None
    asn, cc = f"AS{asn_num}", (f[2] if len(f) > 2 else "").upper()
    asn_name = ""
    names = _dns_query_8888(f"AS{asn_num}.asn.cymru.com", _QTYPE_TXT, timeout)
    if names:
        nf = _txt_fields(names[0])  # "54113 | US | arin | 2011-10-13 | FASTLY, US"
        if len(nf) >= 5:
            asn_name = nf[4]
            cc = cc or nf[1].upper()
    return {"country_code": cc, "country_name": "", "asn": asn, "asn_name": asn_name, "org": asn_name,
            "isp": asn_name, "domain": "", "reverse": "", "prefix": f[1] if len(f) > 1 else "",
            "registry": (f[3] if len(f) > 3 else "").upper(), "source": "cymru"}


# ---------------------------------------------------------------- consultas ASN / WHOIS / país (HTTP)


def _split_as(as_field: str):
    m = re.match(r"^(AS\d+)\s*(.*)$", (as_field or "").strip(), re.I)
    return (m.group(1).upper(), m.group(2).strip()) if m else ("", (as_field or "").strip())


def _geo_ip_api(ip: str, timeout: Optional[float] = None) -> Optional[dict]:
    d = _http_json(f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,isp,org,as,asname,reverse,query",
                   timeout)
    if not d or d.get("status") != "success":
        return None
    asn, asn_org = _split_as(d.get("as", ""))
    return {"country_code": d.get("countryCode", ""), "country_name": d.get("country", ""), "asn": asn,
            "asn_name": d.get("asname", "") or asn_org, "org": d.get("org", "") or asn_org, "isp": d.get("isp", ""),
            "domain": "", "reverse": d.get("reverse", ""), "source": "ip-api"}


def _geo_ipwho(ip: str) -> Optional[dict]:
    d = _http_json(f"https://ipwho.is/{ip}")
    if not d or d.get("success") is False:
        return None
    c = d.get("connection") or {}
    asn = f"AS{c['asn']}" if c.get("asn") else ""
    return {"country_code": d.get("country_code", ""), "country_name": d.get("country", ""), "asn": asn,
            "asn_name": c.get("org", ""), "org": c.get("org", ""), "isp": c.get("isp", ""),
            "domain": c.get("domain", ""), "reverse": "", "source": "ipwho.is"}


def _geo_ipinfo(ip: str) -> Optional[dict]:
    d = _http_json(f"https://ipinfo.io/{ip}/json")
    if not d or d.get("bogon") or not (d.get("org") or d.get("country")):
        return None
    asn, org = _split_as(d.get("org", ""))
    return {"country_code": d.get("country", ""), "country_name": "", "asn": asn, "asn_name": org, "org": org,
            "isp": org, "domain": "", "reverse": d.get("hostname", ""), "source": "ipinfo.io"}


def _geo_rdap(ip: str) -> Optional[dict]:
    d = _http_json(f"https://rdap.arin.net/registry/ip/{ip}")
    if not d or not d.get("name"):
        return None
    return {"country_code": d.get("country", ""), "country_name": "", "asn": "", "asn_name": "",
            "org": d.get("name", ""), "isp": "", "domain": "", "reverse": "", "source": "rdap-arin"}


def _geo_lookup(ip: str) -> Optional[dict]:
    """Cadena de fallback entre APIs públicas. Lanza _NoBudget si se agota la cuota."""
    for fn in (_geo_ip_api, _geo_ipwho, _geo_ipinfo, _geo_rdap):
        res = fn(ip)
        if res:
            return res
    return None


# ---------------------------------------------------------------- composición

def _brand_for(asn: str, *texts: str):
    if asn in _ASN_BRANDS:
        return _ASN_BRANDS[asn]
    blob = " ".join(t for t in texts if t).lower()
    for kw, name, dom, cat in _ORG_KEYWORDS:
        if kw in blob:
            return name, dom, cat
    return None


def _base_domain(host: str) -> str:
    """PTR/host -> dominio base legible (a.b.example.co.uk -> example.co.uk)."""
    parts = [p for p in host.lower().rstrip(".").split(".") if p]
    if len(parts) < 2:
        return ""
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("com", "net", "org", "gob", "edu", "co", "ac"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _compose(key: str, ptr: str, target_ip: str, geo: Optional[dict], source_parts: list) -> dict:
    e = _blank_entry(key, "")
    is_ip = e["is_ip"]
    geo = geo or {}
    cc = (geo.get("country_code") or "").upper()[:8]
    asn = geo.get("asn", "")
    org = (geo.get("org") or geo.get("isp") or "").strip()
    brand = _brand_for(asn, org, geo.get("isp", ""), geo.get("asn_name", ""), ptr, geo.get("reverse", ""))

    resolved, category = "", "OTHER"
    # 1) PTR / reverse / dominio de conexión / la clave misma -> servicio canónico del catálogo
    candidates = [ptr, geo.get("reverse", ""), geo.get("domain", "")] if is_ip else [key, ptr]
    for cand in candidates:
        cand = _normalize_key(cand)
        if not cand or _is_ip(cand):
            continue
        hit = lookup_service_catalog(cand)
        if hit:
            resolved, category = hit
            source_parts.append("catalog")
            break
    # 2) PTR base domain (si no es ruido de ISP) o dominio de la propia clave
    if not resolved:
        if not is_ip:
            clean, cat = match_service_domain_or_ip(key)
            resolved, category = clean or key, cat
        else:
            for cand in (ptr, geo.get("reverse", ""), geo.get("domain", "")):
                cand = _normalize_key(cand)
                if cand and not _is_ip(cand):
                    clean, cat = match_service_domain_or_ip(cand)
                    base = clean or _base_domain(cand)
                    if base and _valid_domain(base):
                        resolved, category = base, cat
                        break
    # 3) Marca por ASN/WHOIS (CDN, nube, etc.)
    if brand:
        b_name, b_dom, b_cat = brand
        if not resolved:
            resolved = b_dom
        if category == "OTHER":
            category = b_cat
        org_name = b_name
    else:
        org_name = org
    if not resolved and not is_ip:
        resolved = key

    fav_domain = resolved if _valid_domain(resolved) else (brand[1] if brand else "")
    e.update({
        "resolved_domain": resolved[:255], "org_name": org_name[:255], "asn": asn[:64],
        "asn_name": (geo.get("asn_name") or "")[:255], "whois_org": (geo.get("org") or geo.get("isp") or "")[:255],
        "country_code": cc, "country_name": (geo.get("country_name") or _COUNTRY_NAMES.get(cc, ""))[:128],
        "favicon_url": favicon_for(fav_domain), "category": category,
        "lookup_source": "+".join(dict.fromkeys(source_parts))[:64] or "dns",
    })
    return e


_http_fail_streak = 0
_http_paused_until = 0.0


def _http_enrich(ip: str) -> Optional[dict]:
    """Paso 2 opcional: ip-api.com con timeout corto. Se pausa solo si el HTTP saliente está bloqueado."""
    global _http_fail_streak, _http_paused_until
    if time.monotonic() < _http_paused_until:
        return None
    res = _geo_ip_api(ip, ENRICH_HTTP_TIMEOUT)  # puede lanzar _NoBudget
    if res:
        _http_fail_streak = 0
        return res
    _http_fail_streak += 1
    if _http_fail_streak >= HTTP_BREAKER_FAILS:
        _http_paused_until = time.monotonic() + HTTP_BREAKER_PAUSE
        _http_fail_streak = 0
        log.info("intel: HTTP saliente no responde; enriquecimiento HTTP en pausa %ss (solo DNS/Cymru)", HTTP_BREAKER_PAUSE)
    return None


def _merge_geo(base: Optional[dict], extra: Optional[dict]) -> Optional[dict]:
    """Completa campos vacíos de `base` (Cymru) con `extra` (HTTP) sin pisar lo ya resuelto."""
    if not base:
        return extra
    if not extra:
        return base
    out = dict(base)
    for k, v in extra.items():
        if k != "source" and v and not out.get(k):
            out[k] = v
    out["source"] = base["source"] + "+" + extra["source"]
    return out


def _resolve_intel_sync(key: str) -> Optional[dict]:
    """Resuelve una IP/dominio desconocido. Retorna None si hay que reintentar por falta de cuota."""
    is_ip = _is_ip(key)
    source_parts: list = []
    ptr = ""
    target_ip = key if is_ip else ""

    if not is_ip:
        target_ip = _run_with_timeout(socket.gethostbyname, key, DNS_TIMEOUT) or ""
        if not target_ip:
            a = _dns_query_8888(key, _QTYPE_A, 2.0)
            target_ip = a[0] if a else ""
        if target_ip:
            source_parts.append("dns")

    geo = None
    if target_ip and _is_public_ip(target_ip):
        # Paso 1 (solo DNS/UDP 53 hacia 8.8.8.8): PTR en paralelo + Team Cymru (ASN, prefijo, país, org).
        box: list = []
        if is_ip:
            t = threading.Thread(target=lambda: box.append(_dns_ptr(target_ip, 2.0)), daemon=True)
            t.start()
        geo = _cymru_lookup(target_ip, 2.0)
        if is_ip:
            t.join(2.5)
            ptr = box[0] if box else ""
            if not ptr:  # respaldo: resolvedor del sistema
                res = _run_with_timeout(socket.gethostbyaddr, key, DNS_TIMEOUT)
                if res and res[0] and not _is_ip(res[0]):
                    ptr = _normalize_key(res[0])
            if ptr:
                source_parts.append("ptr")
        if geo:
            if geo.get("country_code") and not geo.get("country_name"):
                geo["country_name"] = _COUNTRY_NAMES.get(geo["country_code"], "")
            source_parts.append("cymru")
        # Paso 2 (opcional): HTTP solo si faltó país u organización.
        if not geo or not geo.get("country_code") or not (geo.get("org") or geo.get("asn_name")):
            try:
                extra = _http_enrich(target_ip)
            except _NoBudget:
                extra = None
                if not geo and not ptr:
                    prov = _compose(key, ptr, target_ip, None, source_parts + ["rdns-local"])
                    prov["lookup_source"] = "local"
                    _store_mem(prov)
                    return None
            if extra:
                geo = _merge_geo(geo, extra)
                source_parts.append(extra["source"])
    elif target_ip:
        source_parts.append("private")
    elif is_ip:
        pass

    entry = _compose(key, ptr, target_ip, geo, source_parts)
    if not geo and not ptr and is_ip:
        entry["lookup_source"] = "failed"
    return entry


def _seed_run() -> None:
    """Encola IPs/dominios sin resolver de network_domains_daily (retroactivo)."""
    try:
        conn = _sqlite_conn()
        try:
            rows = conn.execute("SELECT DISTINCT domain FROM network_domains_daily WHERE domain != ''").fetchall()
        finally:
            conn.close()
    except Exception as exc:  # la tabla puede no existir todavía
        log.debug("intel: seed omitido: %s", exc)
        return
    n = 0
    for (dom,) in rows:
        key = _normalize_key(dom)
        if not key:
            continue
        cached = _MEM_CACHE.get(key)
        src = (cached or {}).get("lookup_source", "")
        if cached is not None and src not in ("pending", "local", "failed"):
            continue
        if cached is not None and src in ("pending", "local", "failed") and key not in _IN_FLIGHT:
            _enqueue(key)  # falló o quedó provisional: reintento inmediato
            n += 1
            continue
        if cached is None:
            get_or_enqueue_intel(key)
            n += 1
    if n:
        log.info("intel: seed encoló %d IP/dominios sin resolver", n)


_seed_last = 0.0


def seed_existing_unknowns(force: bool = False) -> None:
    """No bloqueante y con límite de frecuencia: lanza el backfill en un hilo."""
    global _seed_last
    now = time.monotonic()
    if not force and now - _seed_last < SEED_INTERVAL:
        return
    _seed_last = now
    if not _started:
        start()
    threading.Thread(target=_seed_run, name="intel-seed", daemon=True).start()


# ---------------------------------------------------------------- almacenamiento

def _store_mem(entry: dict) -> None:
    old = _MEM_CACHE.get(entry["lookup_key"])
    if old:
        entry["hit_count"] = max(entry.get("hit_count", 1), old.get("hit_count", 1))
        entry["created_at"] = old.get("created_at", entry["created_at"])
    entry["updated_at"] = _now_str()
    entry["_ts"] = time.monotonic()
    _MEM_CACHE[entry["lookup_key"]] = entry


def _row_values(e: dict) -> tuple:
    return (e["lookup_key"], bool(e["is_ip"]), e["resolved_domain"], e["org_name"], e["asn"], e["asn_name"],
            e["whois_org"], e["country_code"], e["country_name"], e["favicon_url"], e["category"],
            e["lookup_source"], int(e.get("hit_count", 1)), e["created_at"], e["updated_at"])


def _sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(SQLITE_PATH), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_ensure() -> None:
    conn = _sqlite_conn()
    try:
        conn.execute(SQLITE_DDL)
        conn.commit()
    finally:
        conn.close()


def _sqlite_upsert(e: dict) -> None:
    conn = _sqlite_conn()
    try:
        conn.execute(
            f"""INSERT INTO ip_domain_intel ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})
                ON CONFLICT(lookup_key) DO UPDATE SET
                  is_ip=excluded.is_ip, resolved_domain=excluded.resolved_domain, org_name=excluded.org_name,
                  asn=excluded.asn, asn_name=excluded.asn_name, whois_org=excluded.whois_org,
                  country_code=excluded.country_code, country_name=excluded.country_name,
                  favicon_url=excluded.favicon_url, category=excluded.category,
                  lookup_source=excluded.lookup_source, hit_count=excluded.hit_count,
                  updated_at=excluded.updated_at""",
            (e["lookup_key"], 1 if e["is_ip"] else 0) + _row_values(e)[2:],
        )
        conn.commit()
    finally:
        conn.close()


def _pg_get():
    """Devuelve una conexión PG viva o None (reintenta como máximo cada PG_RETRY_SECONDS)."""
    global _pg_conn, _pg_connected, _pg_next_try
    if psycopg2 is None:
        return None
    if _pg_conn is not None and not getattr(_pg_conn, "closed", 1):
        return _pg_conn
    now = time.monotonic()
    if now < _pg_next_try:
        return None
    try:
        conn = psycopg2.connect(PG_INTEL_DSN, connect_timeout=2)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(PG_DDL)
        _pg_conn, _pg_connected = conn, True
        log.info("intel: PostgreSQL conectado")
        return conn
    except Exception as exc:
        _pg_conn, _pg_connected = None, False
        _pg_next_try = now + PG_RETRY_SECONDS
        log.info("intel: PostgreSQL no disponible (%s); usando espejo SQLite", str(exc).splitlines()[0] if str(exc) else exc)
        return None


def _pg_upsert(e: dict) -> None:
    global _pg_conn, _pg_connected, _pg_next_try
    with _pg_lock:
        conn = _pg_get()
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO ip_domain_intel ({','.join(_COLS)}) VALUES ({','.join(['%s'] * len(_COLS))})
                        ON CONFLICT (lookup_key) DO UPDATE SET
                          is_ip=EXCLUDED.is_ip, resolved_domain=EXCLUDED.resolved_domain, org_name=EXCLUDED.org_name,
                          asn=EXCLUDED.asn, asn_name=EXCLUDED.asn_name, whois_org=EXCLUDED.whois_org,
                          country_code=EXCLUDED.country_code, country_name=EXCLUDED.country_name,
                          favicon_url=EXCLUDED.favicon_url, category=EXCLUDED.category,
                          lookup_source=EXCLUDED.lookup_source, hit_count=EXCLUDED.hit_count,
                          updated_at=EXCLUDED.updated_at""",
                    _row_values(e),
                )
        except Exception as exc:
            log.warning("intel: fallo escribiendo en PostgreSQL: %s", exc)
            try:
                conn.close()
            except Exception:
                pass
            _pg_conn, _pg_connected, _pg_next_try = None, False, time.monotonic() + PG_RETRY_SECONDS


def _persist(e: dict) -> None:
    e["persisted"] = True
    try:
        _sqlite_upsert(e)
    except Exception as exc:
        log.warning("intel: fallo escribiendo espejo SQLite: %s", exc)
    _pg_upsert(e)


def _upgrade_daily_rows(e: dict) -> None:
    """Si PTR/WHOIS reveló un servicio canónico del catálogo, reetiqueta filas históricas de la IP/dominio crudo."""
    if "catalog" not in e["lookup_source"] or not e["resolved_domain"] or e["resolved_domain"] == e["lookup_key"]:
        return
    try:
        conn = _sqlite_conn()
        try:
            conn.execute(
                "UPDATE OR IGNORE network_domains_daily SET domain = ?, category = ? WHERE domain = ?",
                (e["resolved_domain"], e["category"], e["lookup_key"]),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # la tabla puede no existir aún o estar ocupada; en lectura se vuelve a mezclar
        log.debug("intel: no se pudo actualizar network_domains_daily: %s", exc)


def _load_cache() -> None:
    """Precarga _MEM_CACHE desde SQLite y luego PostgreSQL (PG prevalece)."""
    try:
        _sqlite_ensure()
        conn = _sqlite_conn()
        try:
            for r in conn.execute("SELECT * FROM ip_domain_intel").fetchall():
                d = dict(r)
                d["is_ip"] = bool(d["is_ip"])
                d["persisted"], d["_ts"] = True, time.monotonic()
                _MEM_CACHE.setdefault(d["lookup_key"], d)
        finally:
            conn.close()
    except Exception as exc:
        log.warning("intel: no se pudo precargar SQLite: %s", exc)
    with _pg_lock:
        conn = _pg_get()
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {','.join(_COLS)} FROM ip_domain_intel")
                for row in cur.fetchall():
                    d = dict(zip(_COLS, row))
                    for k in ("created_at", "updated_at"):
                        d[k] = d[k].strftime("%Y-%m-%d %H:%M:%S") if hasattr(d[k], "strftime") else str(d[k])
                    d["persisted"], d["_ts"] = True, time.monotonic()
                    _MEM_CACHE[d["lookup_key"]] = d
        except Exception as exc:
            log.warning("intel: no se pudo precargar PostgreSQL: %s", exc)


# ---------------------------------------------------------------- workers

def _enqueue(key: str) -> None:
    with _LOCK:
        if key in _IN_FLIGHT:
            return
        _IN_FLIGHT.add(key)
    try:
        _QUEUE.put_nowait(key)
    except queue.Full:
        with _LOCK:
            _IN_FLIGHT.discard(key)


def _requeue_later(key: str, delay: float) -> None:
    def _put():
        try:
            _QUEUE.put_nowait(key)
        except queue.Full:
            with _LOCK:
                _IN_FLIGHT.discard(key)
    t = threading.Timer(delay, _put)
    t.daemon = True
    t.start()


def _worker_loop() -> None:
    while True:
        key = _QUEUE.get()
        done = True
        try:
            entry = _resolve_intel_sync(key)
            if entry is None:
                n = _ATTEMPTS.get(key, 0) + 1
                _ATTEMPTS[key] = n
                if n < MAX_ATTEMPTS * 4:  # los reintentos por cuota no cuentan como fallo de red
                    _requeue_later(key, _seconds_until_slot())
                    done = False
                else:
                    entry = _compose(key, "", "", None, ["failed"])
                    entry["lookup_source"] = "failed"
            if entry is not None:
                _store_mem(entry)
                if entry["lookup_source"] != "failed":
                    _persist(entry)
                    _upgrade_daily_rows(entry)
                _ATTEMPTS.pop(key, None)
        except Exception as exc:
            log.warning("intel: error resolviendo %s: %s", key, exc)
        finally:
            if done:
                with _LOCK:
                    _IN_FLIGHT.discard(key)


def start() -> None:
    """Idempotente: precarga caché y arranca los workers."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    try:
        _sqlite_ensure()
    except Exception as exc:
        log.warning("intel: no se pudo crear tabla SQLite: %s", exc)
    for i in range(MAX_CONCURRENT_WORKERS):
        threading.Thread(target=_worker_loop, name=f"intel-worker-{i}", daemon=True).start()
    # Precarga (SQLite + PG) fuera del hilo de arranque para no retrasar el servidor si PG tarda.
    def _preload_and_seed():
        _load_cache()
        seed_existing_unknowns(force=True)
    threading.Thread(target=_preload_and_seed, name="intel-preload", daemon=True).start()


# ---------------------------------------------------------------- API pública

def _catalog_stub(key: str, hit) -> dict:
    canon, cat = hit
    e = _blank_entry(key, "catalog")
    e.update({"resolved_domain": canon, "category": cat, "favicon_url": favicon_for(canon)})
    return e


def get_or_enqueue_intel(domain_or_ip: str, app_name: str = "") -> dict:
    """No bloqueante. Devuelve de inmediato lo conocido; encola el enriquecimiento si falta."""
    key = _normalize_key(domain_or_ip)
    if not key:
        return _blank_entry("", "local")
    if not _started:
        start()
    cached = _MEM_CACHE.get(key)
    if cached is not None:
        cached["hit_count"] = cached.get("hit_count", 1) + 1
        src = cached.get("lookup_source", "")
        stale_fail = src == "failed" and time.monotonic() - cached.get("_ts", 0) > FAILED_RETRY_SECONDS
        if (src in ("pending", "local") and key not in _IN_FLIGHT) or stale_fail:
            _enqueue(key)
        return cached

    is_ip = _is_ip(key)
    if is_ip and not _is_public_ip(key):
        e = _blank_entry(key, "local")
        e.update({"org_name": "Red local", "category": "SYSTEM"})
        _MEM_CACHE[key] = e
        return e

    hit = lookup_service_catalog(key)
    if hit and not is_ip:
        # Servicio conocido: sin llamadas externas; favicon y categoría inmediatos.
        e = _catalog_stub(key, hit)
        _MEM_CACHE[key] = e
        return e

    # IP/dominio desconocido (o IP de prefijo conocido sin ASN/país): stub inmediato + enriquecimiento en background.
    e = _catalog_stub(key, hit) if hit else _blank_entry(key, "pending")
    if hit:
        e["lookup_source"] = "pending"
    elif not is_ip:
        clean, cat = match_service_domain_or_ip(key, app_name)
        e.update({"resolved_domain": clean or key, "category": cat, "favicon_url": favicon_for(clean or key)})
    _MEM_CACHE[key] = e
    _enqueue(key)
    return e


def public_view(entry: Optional[dict]) -> dict:
    """Campos de inteligencia que se adjuntan a las filas del dashboard."""
    e = entry or {}
    return {
        "raw_target": e.get("lookup_key", "") if e.get("is_ip") else "",
        "resolved_domain": e.get("resolved_domain", ""),
        "asn": e.get("asn", ""),
        "org_name": e.get("org_name", ""),
        "whois_org": e.get("whois_org", ""),
        "country_code": (e.get("country_code") or "").upper(),
        "country_name": e.get("country_name", ""),
        "favicon_url": e.get("favicon_url", ""),
        "intel_source": e.get("lookup_source", ""),
    }


def get_status() -> dict:
    persisted = [e for e in list(_MEM_CACHE.values()) if e.get("lookup_source") not in _TRANSIENT_SOURCES]
    persisted.sort(key=lambda e: e.get("updated_at", ""), reverse=True)
    pg_total = None
    if _pg_connected:
        with _pg_lock:
            conn = _pg_conn
            try:
                if conn is not None:
                    with conn.cursor() as cur:
                        cur.execute("SELECT COUNT(*) FROM ip_domain_intel")
                        pg_total = int(cur.fetchone()[0])
            except Exception:
                pg_total = None
    return {
        "pg_connected": bool(_pg_connected),
        "pg_driver": psycopg2 is not None,
        "storage": "postgresql" if _pg_connected else "sqlite",
        "total_cached": pg_total if pg_total is not None else len(persisted),
        "mem_entries": len(_MEM_CACHE),
        "pending": len(_IN_FLIGHT),
        "requests_last_min": _requests_in_window(60),
        "requests_last_hour": _requests_in_window(3600),
        "max_requests_per_min": MAX_API_REQUESTS_PER_MIN,
        "max_requests_per_hour": MAX_API_REQUESTS_PER_HOUR,
        "recent": [
            {k: e.get(k, "") for k in ("lookup_key", "resolved_domain", "org_name", "asn", "country_code",
                                       "category", "lookup_source", "updated_at")}
            for e in persisted[:15]
        ],
    }
