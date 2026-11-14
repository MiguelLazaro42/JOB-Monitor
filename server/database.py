import csv
import hashlib
import hmac
import io
import json
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import config

try:
    from server.service_catalog import match_service_domain_or_ip, infer_site_from_window_title, is_browser_name
except ImportError:  # el servidor se ejecuta con server/ como directorio raíz
    from service_catalog import match_service_domain_or_ip, infer_site_from_window_title, is_browser_name

try:
    from server.intel_resolver import get_or_enqueue_intel, get_status as intel_get_status, public_view as intel_public_view, start as intel_start, seed_existing_unknowns as intel_seed
except ImportError:
    from intel_resolver import get_or_enqueue_intel, get_status as intel_get_status, public_view as intel_public_view, start as intel_start, seed_existing_unknowns as intel_seed

DB_PATH: Path = config.DATA_DIR / "remote_assist.db"
MASTER_TENANT_ID = "sitai-master"


_NOISE_APPS = {
    "system idle process", "svchost.exe", "backgroundtaskhost.exe", "msmpeng.exe", "smartscreen.exe",
    "parsecd", "cloudd", "rapportd", "searchtoold", "assistantd", "com.apple.geod", "system",
}


def _app_noise_rank(app: str) -> int:
    """0 = app de usuario (navegador, mensajería...), 1 = proceso de fondo del SO."""
    return 1 if (app or "").strip().lower() in _NOISE_APPS else 0


@lru_cache(maxsize=8192)
def _normalize_domain(domain: str, app_name: str = "", agent_cat: str = "OTHER") -> tuple:
    """Traduce dominio CDN/IP crudo al servicio canónico (catálogo AdGuard + nDPI).
    Conserva la categoría enviada por el agente si el catálogo no aporta una mejor."""
    clean, cat = match_service_domain_or_ip(domain, app_name)
    if not clean:
        return "", agent_cat
    if cat == "OTHER" and agent_cat and agent_cat != "OTHER":
        cat = agent_cat
    return clean[:253], cat


_SHARED_CDN_DOMAINS = (
    "aws.amazon.com", "amazonaws.com", "cloudfront.net", "fastly.com", "fastly.net", "fastlylb.net",
    "akamai.com", "akamaitechnologies.com", "akamaiedge.net", "akam.net", "cloudflare.com", "cloudflare.net",
    "1e100.net", "gstatic.com", "googleapis.com", "azureedge.net", "trafficmanager.net", "msedge.net",
    "edgecastcdn.net", "llnwd.net", "footprint.net", "cdn77.org",
)
_USER_SITE_CATEGORIES = ("SHOPPING", "STREAMING", "SOCIAL", "MESSAGING", "AI", "GAMING")
_BROWSER_APP_KEYS = ("chrome", "safari", "edge", "firefox", "brave", "opera", "vivaldi", "arc", "webkit", "chromium")


def _merge_shared_cdn_rows(rows: list) -> list:
    """Dentro de un mismo slice y navegador, atribuye CDN/cloud genérica al sitio de usuario más activo.
    Filas: (day, agent, domain, app, cat, hits, est, first, last, keys, ptype, focus, conf, rx, tx, dir, port)."""
    def is_cdn(dom):
        return any(dom == k or dom.endswith("." + k) for k in _SHARED_CDN_DOMAINS)

    def is_browser(app):
        low = (app or "").lower()
        return any(k in low for k in _BROWSER_APP_KEYS)

    best = {}  # app -> índice del sitio de usuario más activo
    for i, r in enumerate(rows):
        if r[4] in _USER_SITE_CATEGORIES and not is_cdn(r[2]) and is_browser(r[3]):
            if r[3] not in best or (r[6], r[5]) > (rows[best[r[3]]][6], rows[best[r[3]]][5]):
                best[r[3]] = i
    if not best:
        return rows
    out = list(rows)
    drop = set()
    for i, r in enumerate(rows):
        t = best.get(r[3])
        if t is None or i == t or r[4] in _USER_SITE_CATEGORIES or not is_cdn(r[2]):
            continue
        tr = list(out[t])
        tr[5] += r[5]; tr[6] += r[6]; tr[13] += r[13]; tr[14] += r[14]; tr[9] += r[9]
        out[t] = tuple(tr)
        drop.add(i)
    return [r for i, r in enumerate(out) if i not in drop]


def hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(plain: str, stored_hash: str) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    try:
        algo, salt, hex_digest = stored_hash.split("$", 2)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt.encode("utf-8"), 120_000)
        return hmac.compare_digest(dk.hex(), hex_digest)
    except Exception:
        return False

AVAILABLE_TIMEZONES = [
    {"id": "America/Merida", "label": "Mérida, MX (UTC-6)"},
    {"id": "America/Mexico_City", "label": "CDMX, MX (UTC-6)"},
    {"id": "America/Cancun", "label": "Cancún, MX (UTC-5)"},
    {"id": "America/Monterrey", "label": "Monterrey, MX (UTC-6)"},
    {"id": "America/Tijuana", "label": "Tijuana, MX (UTC-8/-7)"},
    {"id": "UTC", "label": "UTC (Universal)"},
]


def format_duration_short(seconds: int) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    mins, secs = divmod(s, 60)
    if mins < 60:
        return f"{mins}m {secs}s" if secs else f"{mins}m"
    hrs, rem_mins = divmod(mins, 60)
    return f"{hrs}h {rem_mins}m"


def _csv_safe(value) -> str:
    """Neutraliza inyección de fórmulas en Excel para texto controlado por el cliente."""
    text = str(value if value is not None else "")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text



def _canonical_intel(dom: str, cat: str, app: str = ""):
    """Si dom es una IP cruda o categoría OTHER y la inteligencia ya resolvió un dominio, devuelve (dominio, categoría, entry, ip_cruda)."""
    if not dom:
        return dom, cat, None, ""
    try:
        import ipaddress
        ipaddress.ip_address(dom)
        is_ip = True
    except ValueError:
        is_ip = False
    if not (is_ip or cat == "OTHER"):
        return dom, cat, None, ""
    entry = get_or_enqueue_intel(dom, app) or {}
    rd = (entry.get("resolved_domain") or "").strip().lower()
    if rd and rd != dom:
        raw = dom if (is_ip or entry.get("is_ip")) else ""
        return rd, (entry.get("category") or cat), entry, raw
    return dom, cat, entry, ""


class DatabaseManager:
    """Capa de persistencia SQLite para Equipos, Bitácora de Logs y Métricas Estilo Teams."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._cached_tz: str = getattr(config, "DEFAULT_TIMEZONE", "America/Merida")
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _now_dt(self) -> datetime:
        try:
            return datetime.now(ZoneInfo(self._cached_tz))
        except Exception:
            return datetime.now(ZoneInfo("America/Merida"))

    def get_timezone(self) -> str:
        return self._cached_tz

    def set_timezone(self, tz_name: str) -> str:
        clean_tz = (tz_name or "America/Merida").strip()
        # Validar que ZoneInfo soporte el identificador
        ZoneInfo(clean_tz)
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO system_settings (key, value)
                    VALUES ('timezone', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (clean_tz,),
                )
                conn.commit()
                self._cached_tz = clean_tz
            finally:
                conn.close()
        return self._cached_tz

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS system_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS devices (
                        agent_id TEXT PRIMARY KEY,
                        alias TEXT NOT NULL,
                        hostname TEXT NOT NULL,
                        username TEXT NOT NULL,
                        os_version TEXT DEFAULT '',
                        local_ip TEXT DEFAULT '',
                        is_online INTEGER DEFAULT 0,
                        last_seen_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS device_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        alias TEXT NOT NULL,
                        hostname TEXT NOT NULL,
                        username TEXT NOT NULL,
                        category TEXT NOT NULL,
                        message TEXT NOT NULL,
                        duration_seconds INTEGER NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_logs_agent_ts ON device_logs(agent_id, id DESC);
                    CREATE INDEX IF NOT EXISTS idx_logs_category ON device_logs(category, id DESC);

                    CREATE TABLE IF NOT EXISTS window_usage_daily (
                        date TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        app_name TEXT NOT NULL,
                        total_seconds INTEGER DEFAULT 0,
                        PRIMARY KEY (date, agent_id, app_name)
                    );

                    CREATE TABLE IF NOT EXISTS daily_activity_summary (
                        date TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        first_seen_at TEXT NOT NULL,
                        active_seconds INTEGER DEFAULT 0,
                        idle_seconds INTEGER DEFAULT 0,
                        away_alerts_count INTEGER DEFAULT 0,
                        PRIMARY KEY (date, agent_id)
                    );

                    CREATE TABLE IF NOT EXISTS screen_time_hourly (
                        date TEXT NOT NULL,
                        hour INTEGER NOT NULL,
                        agent_id TEXT NOT NULL,
                        active_seconds INTEGER DEFAULT 0,
                        idle_seconds INTEGER DEFAULT 0,
                        switches_count INTEGER DEFAULT 0,
                        PRIMARY KEY (date, hour, agent_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_sth_agent_date ON screen_time_hourly(agent_id, date);

                    CREATE TABLE IF NOT EXISTS dvr_timeline_buckets (
                        date TEXT NOT NULL,
                        slot_5m INTEGER NOT NULL,
                        agent_id TEXT NOT NULL,
                        active_seconds INTEGER DEFAULT 0,
                        idle_seconds INTEGER DEFAULT 0,
                        PRIMARY KEY (date, slot_5m, agent_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_dvr_agent_date ON dvr_timeline_buckets(agent_id, date);

                    CREATE TABLE IF NOT EXISTS resource_usage_daily (
                        date TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        app_name TEXT NOT NULL,
                        resource_title TEXT NOT NULL,
                        category_tag TEXT NOT NULL DEFAULT 'Productividad y Gestion',
                        total_seconds INTEGER DEFAULT 0,
                        switches_count INTEGER DEFAULT 0,
                        last_seen_at TEXT NOT NULL DEFAULT '',
                        PRIMARY KEY (date, agent_id, app_name, resource_title)
                    );
                    CREATE INDEX IF NOT EXISTS idx_rud_agent_date ON resource_usage_daily(agent_id, date);

                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        sender TEXT NOT NULL,
                        sender_name TEXT NOT NULL,
                        message TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_chat_agent_id ON chat_messages(agent_id, id ASC);

                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        role TEXT NOT NULL DEFAULT 'tenant_admin',
                        tenant_id TEXT NOT NULL,
                        parent_user_id INTEGER NULL,
                        can_control INTEGER NOT NULL DEFAULT 1,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS revoked_devices (
                        agent_id TEXT PRIMARY KEY,
                        revoked_at TEXT NOT NULL,
                        reason TEXT DEFAULT ''
                    );

                    CREATE TABLE IF NOT EXISTS network_usage_daily (
                        date TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        rx_bytes INTEGER DEFAULT 0,
                        tx_bytes INTEGER DEFAULT 0,
                        peak_rx_kbps REAL DEFAULT 0,
                        peak_tx_kbps REAL DEFAULT 0,
                        updated_at TEXT,
                        PRIMARY KEY (date, agent_id)
                    );

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
                    );

                    CREATE TABLE IF NOT EXISTS network_domains_daily (
                        date TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        app_name TEXT NOT NULL,
                        category TEXT,
                        hit_count INTEGER DEFAULT 0,
                        estimated_bytes INTEGER DEFAULT 0,
                        first_seen_at TEXT,
                        last_seen_at TEXT,
                        PRIMARY KEY (date, agent_id, domain, app_name)
                    );
                    """
                )
                # MF-05: interfaces de red por adaptador (Wi-Fi, Ethernet, VPN, Virtual) por día y agente
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS network_interfaces_daily (
                        date TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        iface TEXT NOT NULL,
                        iface_label TEXT DEFAULT '',
                        iface_kind TEXT DEFAULT 'OTHER',
                        ip_addr TEXT DEFAULT '',
                        mac_addr TEXT DEFAULT '',
                        rx_bytes INTEGER DEFAULT 0,
                        tx_bytes INTEGER DEFAULT 0,
                        peak_rx_kbps REAL DEFAULT 0,
                        peak_tx_kbps REAL DEFAULT 0,
                        last_seen_at TEXT DEFAULT '',
                        PRIMARY KEY (date, agent_id, iface)
                    );
                    """
                )
                # Migración automática segura para agregar tenant_id a devices si no existía
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(devices)").fetchall()]
                if "tenant_id" not in cols:
                    conn.execute(f"ALTER TABLE devices ADD COLUMN tenant_id TEXT NOT NULL DEFAULT '{MASTER_TENANT_ID}'")

                # Migración automática segura para soporte de auditoría de teclado y evidencias visuales
                log_cols = [r["name"] for r in conn.execute("PRAGMA table_info(device_logs)").fetchall()]
                if "evidence_path" not in log_cols:
                    conn.execute("ALTER TABLE device_logs ADD COLUMN evidence_path TEXT NULL")
                if "meta_json" not in log_cols:
                    conn.execute("ALTER TABLE device_logs ADD COLUMN meta_json TEXT NULL")

                # Migración segura: correlación teclado + foco + tipo de paquete en dominios
                for _ddl in (
                    "ALTER TABLE network_domains_daily ADD COLUMN keystrokes_count INTEGER DEFAULT 0",
                    "ALTER TABLE network_domains_daily ADD COLUMN last_packet_type TEXT DEFAULT ''",
                    "ALTER TABLE network_domains_daily ADD COLUMN focus_app TEXT DEFAULT ''",
                    "ALTER TABLE network_domains_daily ADD COLUMN confidence INTEGER DEFAULT 80",
                    "ALTER TABLE network_domains_daily ADD COLUMN rx_bytes INTEGER DEFAULT 0",
                    "ALTER TABLE network_domains_daily ADD COLUMN tx_bytes INTEGER DEFAULT 0",
                    "ALTER TABLE network_domains_daily ADD COLUMN direction TEXT DEFAULT 'OUTBOUND'",
                    "ALTER TABLE network_domains_daily ADD COLUMN port INTEGER DEFAULT 443",
                    # MF-05: protocolo L7 e interfaz atribuidos a cada flujo
                    "ALTER TABLE network_domains_daily ADD COLUMN proto_l7 TEXT DEFAULT 'HTTPS/TLS'",
                    "ALTER TABLE network_domains_daily ADD COLUMN iface TEXT DEFAULT ''",
                    "ALTER TABLE network_domains_daily ADD COLUMN iface_kind TEXT DEFAULT ''",
                ):
                    try:
                        conn.execute(_ddl)
                    except sqlite3.OperationalError:
                        pass

                default_tz = getattr(config, "DEFAULT_TIMEZONE", "America/Merida")
                conn.execute(
                    "INSERT OR IGNORE INTO system_settings (key, value) VALUES ('timezone', ?)",
                    (default_tz,),
                )
                row = conn.execute("SELECT value FROM system_settings WHERE key = 'timezone'").fetchone()
                if row and row["value"]:
                    self._cached_tz = row["value"]

                # Semilla / Sincronización automática del Super Admin desde config (.env)
                sa_user = getattr(config, "ADMIN_USERNAME", "admin").strip()
                sa_pass = getattr(config, "ADMIN_PASSWORD", "admin123")
                now_str = self._now_dt().strftime("%Y-%m-%d %H:%M:%S")
                existing_sa = conn.execute("SELECT * FROM users WHERE username = ?", (sa_user,)).fetchone()
                if not existing_sa:
                    conn.execute(
                        """
                        INSERT INTO users (username, password_hash, display_name, role, tenant_id, parent_user_id, can_control, is_active, created_at)
                        VALUES (?, ?, 'Super Admin SITAI', 'superadmin', ?, NULL, 1, 1, ?)
                        """,
                        (sa_user, hash_password(sa_pass), MASTER_TENANT_ID, now_str),
                    )
                else:
                    # Asegurar que el usuario maestro tenga siempre rol superadmin
                    conn.execute(
                        "UPDATE users SET role = 'superadmin', tenant_id = ?, can_control = 1, is_active = 1 WHERE username = ?",
                        (MASTER_TENANT_ID, sa_user),
                    )

                # Al iniciar el servidor, todos los equipos inician marcados como desconectados hasta que se reporten
                conn.execute("UPDATE devices SET is_online = 0")
                conn.commit()
            finally:
                conn.close()

    def _row_to_user_dict(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "tenant_id": row["tenant_id"],
            "parent_user_id": row["parent_user_id"],
            "can_control": bool(row["can_control"]),
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        }

    def authenticate_user(self, username: str, password: str) -> Optional[dict]:
        clean_user = (username or "").strip()
        if not clean_user or not password:
            return None
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM users WHERE LOWER(username) = LOWER(?) AND is_active = 1",
                    (clean_user,),
                ).fetchone()
                if row and verify_password(password, row["password_hash"]):
                    return self._row_to_user_dict(row)
                # Fallback directo a variables de entorno para el Super Admin si cambió .env
                if clean_user == config.ADMIN_USERNAME and password == config.ADMIN_PASSWORD and row:
                    conn.execute(
                        "UPDATE users SET password_hash = ? WHERE id = ?",
                        (hash_password(password), row["id"]),
                    )
                    conn.commit()
                    return self._row_to_user_dict(row)
                return None
            finally:
                conn.close()

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
                return self._row_to_user_dict(row) if row else None
            finally:
                conn.close()

    def get_user_by_username(self, username: str) -> Optional[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", ((username or "").strip(),)).fetchone()
                return self._row_to_user_dict(row) if row else None
            finally:
                conn.close()

    def get_tenant_name_map(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {MASTER_TENANT_ID: "SITAI Global (Super Admin)"}
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT tenant_id, display_name FROM users WHERE role = 'tenant_admin' ORDER BY id ASC"
                ).fetchall()
                for r in rows:
                    if r["tenant_id"] not in mapping:
                        mapping[r["tenant_id"]] = r["display_name"]
            finally:
                conn.close()
        return mapping

    def list_tenants(self) -> List[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                dev_counts: Dict[str, int] = {}
                for r in conn.execute("SELECT tenant_id, COUNT(*) AS cnt FROM devices GROUP BY tenant_id").fetchall():
                    dev_counts[r["tenant_id"]] = int(r["cnt"])

                tenants = [
                    {
                        "tenant_id": MASTER_TENANT_ID,
                        "name": "SITAI Global (Super Admin)",
                        "owner_username": config.ADMIN_USERNAME,
                        "devices_count": dev_counts.get(MASTER_TENANT_ID, 0),
                    }
                ]
                seen = {MASTER_TENANT_ID}
                rows = conn.execute(
                    "SELECT tenant_id, display_name, username FROM users WHERE role = 'tenant_admin' ORDER BY display_name ASC"
                ).fetchall()
                for r in rows:
                    tid = r["tenant_id"]
                    if tid in seen:
                        continue
                    seen.add(tid)
                    tenants.append({
                        "tenant_id": tid,
                        "name": r["display_name"],
                        "owner_username": r["username"],
                        "devices_count": dev_counts.get(tid, 0),
                    })
                return tenants
            finally:
                conn.close()

    def list_users(self, requester_role: str, requester_tenant_id: str) -> List[dict]:
        tenant_names = self.get_tenant_name_map()
        with self._lock:
            conn = self._get_conn()
            try:
                dev_counts: Dict[str, int] = {}
                for r in conn.execute("SELECT tenant_id, COUNT(*) AS cnt FROM devices GROUP BY tenant_id").fetchall():
                    dev_counts[r["tenant_id"]] = int(r["cnt"])

                if requester_role == "superadmin":
                    rows = conn.execute(
                        "SELECT * FROM users ORDER BY CASE role WHEN 'superadmin' THEN 0 WHEN 'tenant_admin' THEN 1 ELSE 2 END, display_name ASC"
                    ).fetchall()
                elif requester_role == "tenant_admin":
                    rows = conn.execute(
                        "SELECT * FROM users WHERE tenant_id = ? AND role != 'superadmin' ORDER BY CASE role WHEN 'tenant_admin' THEN 0 ELSE 1 END, display_name ASC",
                        (requester_tenant_id,),
                    ).fetchall()
                else:
                    rows = []

                result = []
                for r in rows:
                    u = self._row_to_user_dict(r)
                    u["tenant_name"] = tenant_names.get(u["tenant_id"], u["tenant_id"])
                    u["devices_count"] = dev_counts.get(u["tenant_id"], 0)
                    result.append(u)
                return result
            finally:
                conn.close()

    def create_user(
        self,
        username: str,
        password: str,
        display_name: str,
        role: str,
        tenant_id: Optional[str],
        parent_user_id: Optional[int],
        can_control: bool = True,
    ) -> dict:
        clean_user = re.sub(r"[^a-zA-Z0-9_.\-@]", "", (username or "").strip())
        if len(clean_user) < 3:
            raise ValueError("El nombre de usuario debe tener al menos 3 caracteres válidos")
        if not password or len(password) < 4:
            raise ValueError("La contraseña debe tener al menos 4 caracteres")
        clean_name = (display_name or clean_user).strip()
        if role not in ("tenant_admin", "sub_user"):
            role = "sub_user"

        if role == "tenant_admin":
            slug = re.sub(r"[^a-z0-9]+", "-", clean_user.lower()).strip("-")[:18] or "cliente"
            final_tenant_id = (tenant_id or "").strip() or f"tenant-{slug}-{secrets.token_hex(3)}"
        else:
            final_tenant_id = (tenant_id or "").strip() or MASTER_TENANT_ID

        now_str = self._now_dt().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                exists = conn.execute("SELECT 1 FROM users WHERE LOWER(username) = LOWER(?)", (clean_user,)).fetchone()
                if exists:
                    raise ValueError(f"El usuario '{clean_user}' ya existe")

                cur = conn.execute(
                    """
                    INSERT INTO users (username, password_hash, display_name, role, tenant_id, parent_user_id, can_control, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        clean_user,
                        hash_password(password),
                        clean_name,
                        role,
                        final_tenant_id,
                        parent_user_id,
                        1 if can_control else 0,
                        now_str,
                    ),
                )
                uid = cur.lastrowid
                conn.commit()
                row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
                return self._row_to_user_dict(row)
            finally:
                conn.close()

    def update_user(
        self,
        user_id: int,
        display_name: Optional[str] = None,
        password: Optional[str] = None,
        can_control: Optional[bool] = None,
        is_active: Optional[bool] = None,
        tenant_id: Optional[str] = None,
    ) -> Optional[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
                if not row:
                    return None
                new_name = (display_name.strip() if display_name and display_name.strip() else row["display_name"])
                new_hash = hash_password(password) if (password and len(password.strip()) >= 4) else row["password_hash"]
                new_ctrl = (1 if can_control else 0) if can_control is not None else row["can_control"]
                new_act = (1 if is_active else 0) if is_active is not None else row["is_active"]
                new_tid = (tenant_id.strip() if tenant_id and tenant_id.strip() else row["tenant_id"])
                if row["role"] == "superadmin":
                    new_ctrl = 1
                    new_act = 1
                    new_tid = MASTER_TENANT_ID

                conn.execute(
                    """
                    UPDATE users
                    SET display_name = ?, password_hash = ?, can_control = ?, is_active = ?, tenant_id = ?
                    WHERE id = ?
                    """,
                    (new_name, new_hash, new_ctrl, new_act, new_tid, int(user_id)),
                )
                conn.commit()
                updated = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
                return self._row_to_user_dict(updated) if updated else None
            finally:
                conn.close()

    def delete_user(self, user_id: int) -> bool:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
                if not row or row["role"] == "superadmin":
                    return False
                # Si se elimina un tenant_admin, también eliminar sus sub_users y reasignar equipos al master
                if row["role"] == "tenant_admin":
                    tid = row["tenant_id"]
                    conn.execute("DELETE FROM users WHERE tenant_id = ? AND role = 'sub_user'", (tid,))
                    conn.execute("UPDATE devices SET tenant_id = ? WHERE tenant_id = ?", (MASTER_TENANT_ID, tid))
                conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
                conn.commit()
                return True
            finally:
                conn.close()

    def get_saved_alias(self, agent_id: str) -> Optional[str]:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT alias FROM devices WHERE agent_id = ?",
                    (agent_id,),
                ).fetchone()
                return row["alias"] if row else None
            finally:
                conn.close()

    def get_device_tenant(self, agent_id: str) -> Optional[str]:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT tenant_id FROM devices WHERE agent_id = ?",
                    (agent_id,),
                ).fetchone()
                return row["tenant_id"] if row else None
            finally:
                conn.close()

    def set_device_tenant(self, agent_id: str, tenant_id: str):
        clean_tid = (tenant_id or MASTER_TENANT_ID).strip()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE devices SET tenant_id = ? WHERE agent_id = ?",
                    (clean_tid, agent_id),
                )
                conn.commit()
            finally:
                conn.close()

    def is_device_revoked(self, agent_id: str) -> bool:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT 1 FROM revoked_devices WHERE agent_id = ?", (agent_id,)).fetchone()
                return bool(row)
            finally:
                conn.close()

    def unrevoke_device(self, agent_id: str):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM revoked_devices WHERE agent_id = ?", (agent_id,))
                conn.commit()
            finally:
                conn.close()

    def delete_device(self, agent_id: str, revoke: bool = False):
        now_str = self._now_dt().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM devices WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM device_logs WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM daily_activity_summary WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM window_usage_daily WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM screen_time_hourly WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM resource_usage_daily WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM chat_messages WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM network_usage_daily WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM network_domains_daily WHERE agent_id = ?", (agent_id,))
                if revoke:
                    conn.execute(
                        "INSERT OR REPLACE INTO revoked_devices (agent_id, revoked_at, reason) VALUES (?, ?, 'autodestroy')",
                        (agent_id, now_str),
                    )
                conn.commit()
            finally:
                conn.close()

    def upsert_device(
        self,
        agent_id: str,
        alias: str,
        hostname: str,
        username: str,
        os_version: str,
        local_ip: str,
        is_online: bool = True,
        tenant_id: Optional[str] = None,
    ) -> str:
        now_dt = self._now_dt()
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        today_str = now_dt.strftime("%Y-%m-%d")
        time_only = now_dt.strftime("%H:%M:%S")
        reported_tid = (tenant_id or "").strip()

        with self._lock:
            conn = self._get_conn()
            try:
                existing = conn.execute("SELECT tenant_id FROM devices WHERE agent_id = ?", (agent_id,)).fetchone()
                if existing:
                    current_tid = existing["tenant_id"] or MASTER_TENANT_ID
                    # Si el equipo estaba en el tenant global por defecto y el agente trae un tenant_id específico del ZIP, adoptarlo
                    final_tid = reported_tid if (reported_tid and current_tid == MASTER_TENANT_ID) else current_tid
                else:
                    final_tid = reported_tid or MASTER_TENANT_ID

                conn.execute(
                    """
                    INSERT INTO devices (agent_id, alias, hostname, username, os_version, local_ip, is_online, last_seen_at, tenant_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        alias = excluded.alias,
                        hostname = excluded.hostname,
                        username = excluded.username,
                        os_version = excluded.os_version,
                        local_ip = excluded.local_ip,
                        is_online = excluded.is_online,
                        last_seen_at = excluded.last_seen_at,
                        tenant_id = excluded.tenant_id
                    """,
                    (agent_id, alias, hostname, username, os_version, local_ip, 1 if is_online else 0, now_str, final_tid),
                )

                conn.execute(
                    """
                    INSERT INTO daily_activity_summary (date, agent_id, first_seen_at, active_seconds, idle_seconds, away_alerts_count)
                    VALUES (?, ?, ?, 0, 0, 0)
                    ON CONFLICT(date, agent_id) DO NOTHING
                    """,
                    (today_str, agent_id, time_only),
                )
                conn.commit()
                return final_tid
            finally:
                conn.close()

    def set_device_offline(self, agent_id: str):
        now_str = self._now_dt().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE devices SET is_online = 0, last_seen_at = ? WHERE agent_id = ?",
                    (now_str, agent_id),
                )
                conn.commit()
            finally:
                conn.close()

    def update_device_alias(self, agent_id: str, new_alias: str):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE devices SET alias = ? WHERE agent_id = ?",
                    (new_alias, agent_id),
                )
                conn.execute(
                    "UPDATE device_logs SET alias = ? WHERE agent_id = ?",
                    (new_alias, agent_id),
                )
                conn.commit()
            finally:
                conn.close()

    def get_all_known_devices(self, allowed_tenant_id: Optional[str] = None) -> List[dict]:
        tenant_names = self.get_tenant_name_map()
        with self._lock:
            conn = self._get_conn()
            try:
                if allowed_tenant_id:
                    rows = conn.execute(
                        "SELECT * FROM devices WHERE tenant_id = ? ORDER BY is_online DESC, alias ASC",
                        (allowed_tenant_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM devices ORDER BY is_online DESC, alias ASC"
                    ).fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    tid = d.get("tenant_id") or MASTER_TENANT_ID
                    d["tenant_id"] = tid
                    d["tenant_name"] = tenant_names.get(tid, tid)
                    result.append(d)
                return result
            finally:
                conn.close()

    def add_log(
        self,
        agent_id: str,
        alias: str,
        hostname: str,
        username: str,
        category: str,
        message: str,
        duration_seconds: Optional[int] = None,
        evidence_path: Optional[str] = None,
        meta_json: Optional[str] = None,
        custom_timestamp: Optional[str] = None,
    ) -> dict:
        now_str = (custom_timestamp or "").strip() or self._now_dt().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO device_logs (timestamp, agent_id, alias, hostname, username, category, message, duration_seconds, evidence_path, meta_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (now_str, agent_id, alias, hostname, username, category, message, duration_seconds, evidence_path, meta_json),
                )
                log_id = cur.lastrowid
                conn.commit()
                return {
                    "id": log_id,
                    "timestamp": now_str,
                    "agent_id": agent_id,
                    "alias": alias,
                    "hostname": hostname,
                    "username": username,
                    "category": category,
                    "message": message,
                    "duration_seconds": duration_seconds,
                    "evidence_path": evidence_path,
                    "meta_json": meta_json,
                }
            finally:
                conn.close()

    def record_telemetry_slice(
        self,
        agent_id: str,
        delta_seconds: int,
        is_active: bool,
        app_name: str,
        increment_away_alert: bool = False,
        resource_title: str = "Ventana principal",
        category_tag: str = "Productividad y Gestion",
        resource_slices: Optional[List[dict]] = None,
        custom_timestamp: Optional[str] = None,
    ):
        now_dt = self._now_dt()
        if custom_timestamp and len(custom_timestamp) >= 19:
            today_str = custom_timestamp[:10]
            time_only = custom_timestamp[11:19]
            now_str = custom_timestamp[:19]
            try:
                hour_val = max(0, min(23, int(custom_timestamp[11:13])))
            except Exception:
                hour_val = now_dt.hour
        else:
            today_str = now_dt.strftime("%Y-%m-%d")
            time_only = now_dt.strftime("%H:%M:%S")
            now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            hour_val = now_dt.hour

        delta = max(1, min(30, int(delta_seconds)))
        act_inc = delta if is_active else 0
        idl_inc = 0 if is_active else delta
        alert_inc = 1 if increment_away_alert else 0
        clean_app = (app_name or "Escritorio").strip()[:60]
        clean_res = (resource_title or "Ventana principal").strip()[:115]
        clean_cat = (category_tag or "Productividad y Gestion").strip()[:45]

        with self._lock:
            conn = self._get_conn()
            try:
                if not custom_timestamp:
                    conn.execute(
                        "UPDATE devices SET last_seen_at = ?, is_online = 1 WHERE agent_id = ?",
                        (now_str, agent_id),
                    )
                conn.execute(
                    """
                    INSERT INTO daily_activity_summary (date, agent_id, first_seen_at, active_seconds, idle_seconds, away_alerts_count)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, agent_id) DO UPDATE SET
                        active_seconds = active_seconds + excluded.active_seconds,
                        idle_seconds = idle_seconds + excluded.idle_seconds,
                        away_alerts_count = away_alerts_count + excluded.away_alerts_count
                    """,
                    (today_str, agent_id, time_only, act_inc, idl_inc, alert_inc),
                )
                conn.execute(
                    """
                    INSERT INTO screen_time_hourly (date, hour, agent_id, active_seconds, idle_seconds, switches_count)
                    VALUES (?, ?, ?, ?, ?, 0)
                    ON CONFLICT(date, hour, agent_id) DO UPDATE SET
                        active_seconds = active_seconds + excluded.active_seconds,
                        idle_seconds = idle_seconds + excluded.idle_seconds
                    """,
                    (today_str, hour_val, agent_id, act_inc, idl_inc),
                )
                try:
                    min_val = max(0, min(59, int(time_only[3:5])))
                except Exception:
                    min_val = now_dt.minute
                slot_5m = max(0, min(287, hour_val * 12 + (min_val // 5)))
                conn.execute(
                    """
                    INSERT INTO dvr_timeline_buckets (date, slot_5m, agent_id, active_seconds, idle_seconds)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(date, slot_5m, agent_id) DO UPDATE SET
                        active_seconds = active_seconds + excluded.active_seconds,
                        idle_seconds = idle_seconds + excluded.idle_seconds
                    """,
                    (today_str, slot_5m, agent_id, act_inc, idl_inc),
                )
                if resource_slices:
                    for sl in resource_slices[:25]:
                        s_sec = max(0, min(30, int(sl.get("seconds") or 0)))
                        if s_sec <= 0:
                            continue
                        s_app = str(sl.get("app_name") or clean_app).strip()[:60]
                        s_res = str(sl.get("resource_title") or "Ventana principal").strip()[:115]
                        s_cat = str(sl.get("category_tag") or clean_cat).strip()[:45]
                        conn.execute(
                            """
                            INSERT INTO window_usage_daily (date, agent_id, app_name, total_seconds)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(date, agent_id, app_name) DO UPDATE SET
                                total_seconds = total_seconds + excluded.total_seconds
                            """,
                            (today_str, agent_id, s_app, s_sec),
                        )
                        conn.execute(
                            """
                            INSERT INTO resource_usage_daily (date, agent_id, app_name, resource_title, category_tag, total_seconds, switches_count, last_seen_at)
                            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                            ON CONFLICT(date, agent_id, app_name, resource_title) DO UPDATE SET
                                total_seconds = total_seconds + excluded.total_seconds,
                                category_tag = excluded.category_tag,
                                last_seen_at = excluded.last_seen_at
                            """,
                            (today_str, agent_id, s_app, s_res, s_cat, s_sec, time_only),
                        )
                elif is_active and clean_app:
                    conn.execute(
                        """
                        INSERT INTO window_usage_daily (date, agent_id, app_name, total_seconds)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(date, agent_id, app_name) DO UPDATE SET
                            total_seconds = total_seconds + excluded.total_seconds
                        """,
                        (today_str, agent_id, clean_app, delta),
                    )
                    conn.execute(
                        """
                        INSERT INTO resource_usage_daily (date, agent_id, app_name, resource_title, category_tag, total_seconds, switches_count, last_seen_at)
                        VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                        ON CONFLICT(date, agent_id, app_name, resource_title) DO UPDATE SET
                            total_seconds = total_seconds + excluded.total_seconds,
                            last_seen_at = excluded.last_seen_at
                        """,
                        (today_str, agent_id, clean_app, clean_res, clean_cat, delta, time_only),
                    )
                conn.commit()
            finally:
                conn.close()

    def record_focus_switch(
        self,
        agent_id: str,
        app_name: str,
        resource_title: str,
        category_tag: str,
        custom_timestamp: Optional[str] = None,
    ):
        """MF-ST08: Registra incremento de cambio de app/pestana por hora y por recurso."""
        now_dt = self._now_dt()
        if custom_timestamp and len(custom_timestamp) >= 19:
            today_str = custom_timestamp[:10]
            time_only = custom_timestamp[11:19]
            try:
                hour_val = max(0, min(23, int(custom_timestamp[11:13])))
            except Exception:
                hour_val = now_dt.hour
        else:
            today_str = now_dt.strftime("%Y-%m-%d")
            time_only = now_dt.strftime("%H:%M:%S")
            hour_val = now_dt.hour
        c_app = (app_name or "Escritorio").strip()[:60]
        c_res = (resource_title or "Ventana principal").strip()[:115]
        c_cat = (category_tag or "Productividad y Gestion").strip()[:45]
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO screen_time_hourly (date, hour, agent_id, active_seconds, idle_seconds, switches_count)
                    VALUES (?, ?, ?, 0, 0, 1)
                    ON CONFLICT(date, hour, agent_id) DO UPDATE SET
                        switches_count = switches_count + 1
                    """,
                    (today_str, hour_val, agent_id),
                )
                conn.execute(
                    """
                    INSERT INTO resource_usage_daily (date, agent_id, app_name, resource_title, category_tag, total_seconds, switches_count, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, 0, 1, ?)
                    ON CONFLICT(date, agent_id, app_name, resource_title) DO UPDATE SET
                        switches_count = switches_count + 1,
                        category_tag = excluded.category_tag,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (today_str, agent_id, c_app, c_res, c_cat, time_only),
                )
                conn.commit()
            finally:
                conn.close()

    def query_logs(
        self,
        agent_id: Optional[str] = None,
        category: Optional[str] = None,
        search_q: Optional[str] = None,
        limit: int = 250,
        allowed_tenant_id: Optional[str] = None,
    ) -> List[dict]:
        clauses = []
        params: list = []

        if allowed_tenant_id:
            clauses.append("agent_id IN (SELECT agent_id FROM devices WHERE tenant_id = ?)")
            params.append(allowed_tenant_id)

        if agent_id and agent_id != "ALL":
            clauses.append("agent_id = ?")
            params.append(agent_id)

        if category and category != "ALL":
            clauses.append("category = ?")
            params.append(category)

        if search_q and search_q.strip():
            q = f"%{search_q.strip()}%"
            clauses.append("(message LIKE ? OR alias LIKE ? OR hostname LIKE ? OR username LIKE ?)")
            params.extend([q, q, q, q])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM device_logs {where_sql} ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(2000, int(limit))))

        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def export_logs_csv(
        self,
        agent_id: Optional[str] = None,
        category: Optional[str] = None,
        search_q: Optional[str] = None,
        allowed_tenant_id: Optional[str] = None,
    ) -> str:
        logs = self.query_logs(
            agent_id=agent_id,
            category=category,
            search_q=search_q,
            limit=2000,
            allowed_tenant_id=allowed_tenant_id,
        )
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["ID", "Fecha y Hora", "Equipo (Alias)", "Hostname", "Usuario", "Categoría", "Detalle del Evento", "Duración (seg)"])
        for row in logs:
            writer.writerow([
                row["id"],
                row["timestamp"],
                row["alias"],
                row["hostname"],
                row["username"],
                row["category"],
                row["message"],
                row["duration_seconds"] if row["duration_seconds"] is not None else "",
            ])
        return out.getvalue()

    # ------------------------------------------------------------------
    # Analítica de red y dominios
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_date(custom_date: Optional[str]) -> Optional[str]:
        val = (custom_date or "").strip()[:10]
        if len(val) == 10 and val[4] == "-" and val[7] == "-":
            return val
        return None

    def record_network_slice(
        self,
        agent_id: str,
        delta_rx_bytes: int,
        delta_tx_bytes: int,
        rx_kbps: float,
        tx_kbps: float,
        custom_date: Optional[str] = None,
    ):
        now_dt = self._now_dt()
        day = self._clean_date(custom_date) or now_dt.strftime("%Y-%m-%d")
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        rx = max(0, int(delta_rx_bytes or 0))
        tx = max(0, int(delta_tx_bytes or 0))
        prx = max(0.0, float(rx_kbps or 0.0))
        ptx = max(0.0, float(tx_kbps or 0.0))
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO network_usage_daily (date, agent_id, rx_bytes, tx_bytes, peak_rx_kbps, peak_tx_kbps, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, agent_id) DO UPDATE SET
                        rx_bytes = rx_bytes + excluded.rx_bytes,
                        tx_bytes = tx_bytes + excluded.tx_bytes,
                        peak_rx_kbps = MAX(peak_rx_kbps, excluded.peak_rx_kbps),
                        peak_tx_kbps = MAX(peak_tx_kbps, excluded.peak_tx_kbps),
                        updated_at = excluded.updated_at
                    """,
                    (day, agent_id, rx, tx, prx, ptx, now_str),
                )
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _infer_proto_l7(port: int = 0, direction: str = "OUTBOUND", domain: str = "",
                        app_name: str = "", prev_proto: str = "") -> str:
        """
        MF-06: Infiere el protocolo L7 en el servidor cuando el flujo proviene de un agente aún
        no actualizado (sin proto_l7). Usa puerto, dominio y proceso como respaldo.
        """
        prev = (prev_proto or "").strip()
        if prev and prev != "Desconocido":
            return prev
        try:
            p = int(port or 0)
        except (TypeError, ValueError):
            p = 0
        dom = (domain or "").strip().lower().rstrip(".")
        proc = (app_name or "").strip().lower()
        doh_domains = ("dns.google", "cloudflare-dns.com", "dns.quad9.net", "dns.adguard.com", "one.one.one.one")
        if p == 853 or any(dom == k or dom.endswith("." + k) for k in doh_domains):
            return "DoH/DoT"
        if p in (53, 5353):
            return "DNS"
        if p in (9001, 9030, 9050, 9150) or "tor" in proc:
            return "Tor"
        if p in (51820, 51821) or "wireguard" in proc:
            return "WireGuard"
        if p in (41641, 9993) or "tailscale" in proc or "zerotier" in proc:
            return "Tailscale"
        if p in (1194, 500, 4500) or any(k in proc for k in ("openvpn", "anyconnect", "globalprotect", "forticlient")):
            return "OpenVPN/IPsec"
        if p in (22, 2222):
            return "SSH"
        if p in (3389, 5900, 5901, 7070, 5938) or (21115 <= p <= 21119):
            return "RDP/Remoto"
        if p in (445, 139, 548, 2049):
            return "SMB/LAN"
        if p in (25, 465, 587, 993, 995, 143, 110):
            return "Correo"
        if p in (3306, 5432, 6379, 27017, 8006, 1433):
            return "DB/Infra"
        if p in (3478, 3479, 3480, 3481):
            return "WebRTC"
        if p in (443, 8443):
            return "HTTPS/TLS"
        if p in (80, 8080, 8000, 3000, 5000):
            return "HTTP"
        return "Desconocido"

    _VALID_IFACE_KINDS = ("WIFI", "ETH", "VPN", "VIRTUAL", "OTHER")

    def record_network_interfaces_batch(
        self,
        agent_id: str,
        interfaces: Optional[List[dict]] = None,
        custom_date: Optional[str] = None,
    ):
        """
        MF-06: Persiste las interfaces de red reportadas por el agente, acumulando los bytes
        RX/TX del día y registrando el pico de velocidad, la IP, MAC y tipo por adaptador.
        """
        if not interfaces or not isinstance(interfaces, list):
            return
        now_dt = self._now_dt()
        day = self._clean_date(custom_date) or now_dt.strftime("%Y-%m-%d")
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        rows = []
        for it in interfaces:
            if not isinstance(it, dict):
                continue
            iface = str(it.get("iface") or "").strip()[:60]
            if not iface:
                continue
            kind = str(it.get("kind") or it.get("iface_kind") or "OTHER").strip().upper()[:12]
            if kind not in self._VALID_IFACE_KINDS:
                kind = "OTHER"
            label = str(it.get("label") or "").strip()[:120]
            ip_addr = str(it.get("ip") or "").strip()[:60]
            mac_addr = str(it.get("mac") or "").strip()[:40]
            try:
                rx = max(0, int(it.get("delta_rx_bytes") or 0))
                tx = max(0, int(it.get("delta_tx_bytes") or 0))
                prx = max(0.0, float(it.get("rx_kbps") or 0.0))
                ptx = max(0.0, float(it.get("tx_kbps") or 0.0))
            except (TypeError, ValueError):
                continue
            rows.append((day, agent_id, iface, label, kind, ip_addr, mac_addr, rx, tx, prx, ptx, now_str))
        if not rows:
            return
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executemany(
                    """
                    INSERT INTO network_interfaces_daily
                        (date, agent_id, iface, iface_label, iface_kind, ip_addr, mac_addr,
                         rx_bytes, tx_bytes, peak_rx_kbps, peak_tx_kbps, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, agent_id, iface) DO UPDATE SET
                        iface_label = CASE WHEN excluded.iface_label != '' THEN excluded.iface_label ELSE iface_label END,
                        iface_kind = excluded.iface_kind,
                        ip_addr = CASE WHEN excluded.ip_addr != '' THEN excluded.ip_addr ELSE ip_addr END,
                        mac_addr = CASE WHEN excluded.mac_addr != '' THEN excluded.mac_addr ELSE mac_addr END,
                        rx_bytes = rx_bytes + excluded.rx_bytes,
                        tx_bytes = tx_bytes + excluded.tx_bytes,
                        peak_rx_kbps = MAX(peak_rx_kbps, excluded.peak_rx_kbps),
                        peak_tx_kbps = MAX(peak_tx_kbps, excluded.peak_tx_kbps),
                        last_seen_at = excluded.last_seen_at
                    """,
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

    def record_network_telemetry_slice(
        self,
        agent_id: str,
        domains_list: Optional[List[dict]] = None,
        interfaces: Optional[List[dict]] = None,
        custom_date: Optional[str] = None,
    ):
        """
        MF-06: Punto de entrada unificado para un slice de telemetría de red: guarda los flujos
        por dominio (con proto_l7/iface) y las interfaces de red del equipo.
        """
        if domains_list:
            self.record_domain_activity_batch(agent_id, domains_list, custom_date=custom_date)
        if interfaces:
            self.record_network_interfaces_batch(agent_id, interfaces, custom_date=custom_date)

    def get_network_interfaces_summary(
        self, date_str: Optional[str] = None, allowed_tenant_id: Optional[str] = None,
    ) -> Dict[str, List[dict]]:
        """MF-06: devuelve las interfaces del día agrupadas por agent_id."""
        day = self._clean_date(date_str) or self._now_dt().strftime("%Y-%m-%d")
        with self._lock:
            conn = self._get_conn()
            try:
                if allowed_tenant_id:
                    rows = conn.execute(
                        """SELECT * FROM network_interfaces_daily
                           WHERE date = ? AND agent_id IN (SELECT agent_id FROM devices WHERE tenant_id = ?)""",
                        (day, allowed_tenant_id),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM network_interfaces_daily WHERE date = ?", (day,)
                    ).fetchall()
            finally:
                conn.close()
        by_agent: Dict[str, List[dict]] = {}
        for r in rows:
            d = dict(r)
            total = int(d.get("rx_bytes") or 0) + int(d.get("tx_bytes") or 0)
            by_agent.setdefault(d["agent_id"], []).append({
                "iface": d["iface"],
                "label": d.get("iface_label") or d["iface"],
                "kind": d.get("iface_kind") or "OTHER",
                "ip": d.get("ip_addr") or "",
                "mac": d.get("mac_addr") or "",
                "rx_bytes": int(d.get("rx_bytes") or 0),
                "tx_bytes": int(d.get("tx_bytes") or 0),
                "total_bytes": total,
                "peak_rx_kbps": float(d.get("peak_rx_kbps") or 0),
                "peak_tx_kbps": float(d.get("peak_tx_kbps") or 0),
                "last_seen_at": d.get("last_seen_at") or "",
            })
        for lst in by_agent.values():
            lst.sort(key=lambda x: x["total_bytes"], reverse=True)
        return by_agent

    def record_domain_activity_batch(
        self,
        agent_id: str,
        domains_list: List[dict],
        custom_date: Optional[str] = None,
    ):
        if not domains_list:
            return
        now_dt = self._now_dt()
        day = self._clean_date(custom_date) or now_dt.strftime("%Y-%m-%d")
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        rows = []
        for d in domains_list:
            if not isinstance(d, dict):
                continue
            domain = str(d.get("domain") or "").strip().lower()[:253]
            if not domain:
                continue
            app = str(d.get("app_name") or "Desconocido").strip()[:80] or "Desconocido"
            cat = str(d.get("category") or "OTHER").strip().upper()[:30]
            domain, cat = _normalize_domain(domain, app, cat)
            if not domain:
                continue
            get_or_enqueue_intel(domain, app)  # no bloquea: caché en memoria o encola enriquecimiento ASN/WHOIS
            try:
                hits = max(1, int(d.get("connections", d.get("hit_count", 1))))
                est = max(0, int(d.get("estimated_bytes", 0) or 0))
                if d.get("rx_bytes") is not None or d.get("tx_bytes") is not None:
                    rx_b = max(0, int(d.get("rx_bytes") or 0))
                    tx_b = max(0, int(d.get("tx_bytes") or 0))
                    est = max(est, rx_b + tx_b)
                    if rx_b + tx_b == 0 and est > 0:
                        rx_b = int(est * 0.85)
                        tx_b = est - rx_b
                else:
                    rx_b = int(est * 0.85)
                    tx_b = est - rx_b
                direction = "INBOUND" if str(d.get("direction") or "").upper() == "INBOUND" else "OUTBOUND"
                port = int(d.get("port") or d.get("local_port") or 443)
                if not 0 < port <= 65535:
                    port = 443
            except (TypeError, ValueError):
                continue
            try:
                keys = max(0, int(d.get("keystrokes", d.get("keystrokes_count", 0)) or 0))
                conf = max(0, min(100, int(d.get("confidence", 80) or 80)))
            except (TypeError, ValueError):
                keys, conf = 0, 80
            ptype = str(d.get("packet_type") or d.get("last_packet_type") or "").strip()[:80]
            focus = str(d.get("focus_app") or "").strip()[:40]
            # MF-06: proto_l7 e interfaz atribuidos por el agente; si faltan, se infieren aquí
            proto_l7 = str(d.get("proto_l7") or "").strip()[:30]
            if not proto_l7:
                proto_l7 = self._infer_proto_l7(port=port, direction=direction, domain=domain, app_name=app)
            iface = str(d.get("iface") or "").strip()[:60]
            iface_kind = str(d.get("iface_kind") or "").strip().upper()[:12]
            rows.append((day, agent_id, domain, app, cat, hits, est, now_str, now_str, keys, ptype, focus, conf,
                         rx_b, tx_b, direction, port, proto_l7, iface, iface_kind))
        if not rows:
            return
        rows = _merge_shared_cdn_rows(rows)
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executemany(
                    """
                    INSERT INTO network_domains_daily
                        (date, agent_id, domain, app_name, category, hit_count, estimated_bytes, first_seen_at, last_seen_at,
                         keystrokes_count, last_packet_type, focus_app, confidence,
                         rx_bytes, tx_bytes, direction, port, proto_l7, iface, iface_kind)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, agent_id, domain, app_name) DO UPDATE SET
                        hit_count = hit_count + excluded.hit_count,
                        estimated_bytes = estimated_bytes + excluded.estimated_bytes,
                        category = excluded.category,
                        last_seen_at = excluded.last_seen_at,
                        keystrokes_count = COALESCE(keystrokes_count, 0) + excluded.keystrokes_count,
                        last_packet_type = CASE WHEN excluded.last_packet_type != ''
                                                THEN excluded.last_packet_type ELSE COALESCE(last_packet_type, '') END,
                        focus_app = CASE WHEN excluded.focus_app != ''
                                         THEN excluded.focus_app ELSE COALESCE(focus_app, '') END,
                        confidence = MAX(COALESCE(confidence, 0), excluded.confidence),
                        rx_bytes = COALESCE(rx_bytes, 0) + excluded.rx_bytes,
                        tx_bytes = COALESCE(tx_bytes, 0) + excluded.tx_bytes,
                        direction = CASE WHEN excluded.direction = 'INBOUND' THEN 'INBOUND'
                                         ELSE COALESCE(network_domains_daily.direction, 'OUTBOUND') END,
                        port = CASE WHEN excluded.direction = 'INBOUND' THEN excluded.port
                                    ELSE COALESCE(network_domains_daily.port, 443) END,
                        proto_l7 = CASE WHEN excluded.proto_l7 != '' AND excluded.proto_l7 != 'Desconocido'
                                        THEN excluded.proto_l7 ELSE COALESCE(network_domains_daily.proto_l7, 'HTTPS/TLS') END,
                        iface = CASE WHEN excluded.iface != '' THEN excluded.iface ELSE COALESCE(network_domains_daily.iface, '') END,
                        iface_kind = CASE WHEN excluded.iface_kind != '' THEN excluded.iface_kind ELSE COALESCE(network_domains_daily.iface_kind, '') END
                    """,
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

    _site_backfill_last = 0.0

    def backfill_window_sites_today(self, force: bool = False) -> int:
        """Enriquece network_domains_daily de hoy con sitios deducidos de los logs WINDOW (máx. 1 vez/60 s).
        Solo inserta pares (agent_id, dominio) aún ausentes, con la hora del log de ventana."""
        mono = time.monotonic()
        if not force and mono - self._site_backfill_last < 60:
            return 0
        self._site_backfill_last = mono
        day = self._now_dt().strftime("%Y-%m-%d")
        inserted = 0
        try:
            with self._lock:
                conn = self._get_conn()
                try:
                    logs = conn.execute(
                        "SELECT timestamp, agent_id, message, meta_json FROM device_logs "
                        "WHERE category = 'WINDOW' AND timestamp >= ? ORDER BY id ASC",
                        (day + " 00:00:00",),
                    ).fetchall()
                    known = {
                        (r["agent_id"], r["domain"])
                        for r in conn.execute("SELECT agent_id, domain FROM network_domains_daily WHERE date = ?", (day,))
                    }
                    rows = []
                    for lg in logs:
                        app, text = "", ""
                        try:
                            meta = json.loads(lg["meta_json"]) if lg["meta_json"] else {}
                        except (TypeError, ValueError):
                            meta = {}
                        if isinstance(meta, dict) and (meta.get("app_name") or meta.get("active_window")):
                            app = str(meta.get("app_name") or "")
                            text = f"{meta.get('active_window') or ''} {meta.get('resource_title') or ''}"
                        else:
                            # "Cambio a 'App — título' (permanencia previa en '...')": ignorar la ventana previa
                            text = (lg["message"] or "").split(" (permanencia previa", 1)[0]
                        if not (is_browser_name(app) or is_browser_name(text)):
                            continue
                        hint = infer_site_from_window_title(text, app)
                        if not hint or (lg["agent_id"], hint[0]) in known:
                            continue
                        known.add((lg["agent_id"], hint[0]))
                        browser = (app or "Google Chrome").strip()[:80] or "Google Chrome"
                        ts = lg["timestamp"]
                        rows.append((day, lg["agent_id"], hint[0], browser, hint[1], 3, 28672, ts, ts,
                                     0, "", browser[:40], 95, 24576, 4096, "OUTBOUND", 443))
                    if rows:
                        conn.executemany(
                            """INSERT OR IGNORE INTO network_domains_daily
                               (date, agent_id, domain, app_name, category, hit_count, estimated_bytes, first_seen_at,
                                last_seen_at, keystrokes_count, last_packet_type, focus_app, confidence,
                                rx_bytes, tx_bytes, direction, port)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            rows,
                        )
                        conn.commit()
                        inserted = len(rows)
                finally:
                    conn.close()
        except Exception as e:
            print(f"[Network] Backfill de sitios desde logs WINDOW falló: {e}")
        return inserted

    def get_network_dashboard_summary(
        self,
        date_str: Optional[str] = None,
        allowed_tenant_id: Optional[str] = None,
    ) -> dict:
        day = self._clean_date(date_str) or self._now_dt().strftime("%Y-%m-%d")
        self.backfill_window_sites_today()
        intel_seed()  # backfill throttled de IPs/dominios sin resolver
        tenant_names = self.get_tenant_name_map()
        with self._lock:
            conn = self._get_conn()
            try:
                if allowed_tenant_id:
                    devices = conn.execute(
                        "SELECT * FROM devices WHERE tenant_id = ? ORDER BY alias ASC", (allowed_tenant_id,)
                    ).fetchall()
                else:
                    devices = conn.execute("SELECT * FROM devices ORDER BY alias ASC").fetchall()
                usage = {
                    r["agent_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM network_usage_daily WHERE date = ?", (day,)).fetchall()
                }
                dom_rows = conn.execute(
                    """
                    SELECT agent_id, domain, category, hit_count AS hits, estimated_bytes AS est,
                           COALESCE(keystrokes_count, 0) AS keys, COALESCE(confidence, 0) AS conf,
                           COALESCE(last_seen_at, '') AS last_seen,
                           COALESCE(last_packet_type, '') AS ptype, COALESCE(focus_app, '') AS focus,
                           CASE WHEN COALESCE(rx_bytes, 0) + COALESCE(tx_bytes, 0) = 0 AND estimated_bytes > 0
                                THEN CAST(estimated_bytes * 0.85 AS INTEGER) ELSE COALESCE(rx_bytes, 0) END AS rx,
                           CASE WHEN COALESCE(rx_bytes, 0) + COALESCE(tx_bytes, 0) = 0 AND estimated_bytes > 0
                                THEN estimated_bytes - CAST(estimated_bytes * 0.85 AS INTEGER) ELSE COALESCE(tx_bytes, 0) END AS tx,
                           COALESCE(direction, 'OUTBOUND') AS direction, COALESCE(port, 443) AS port
                    FROM network_domains_daily WHERE date = ?
                    """,
                    (day,),
                ).fetchall()
                recent_cutoff = (self._now_dt() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
                recent_rows = conn.execute(
                    """
                    SELECT agent_id, domain, category, MAX(last_seen_at) AS last_seen
                    FROM network_domains_daily
                    WHERE date = ? AND last_seen_at >= ?
                    GROUP BY agent_id, domain, category
                    """,
                    (day, recent_cutoff),
                ).fetchall()
                # MF-06: desglose de protocolos L7 por agente (bytes, conexiones)
                proto_rows = conn.execute(
                    """
                    SELECT agent_id, COALESCE(NULLIF(proto_l7, ''), 'Desconocido') AS proto,
                           SUM(hit_count) AS hits,
                           SUM(CASE WHEN COALESCE(rx_bytes,0)+COALESCE(tx_bytes,0)=0 AND estimated_bytes>0
                                    THEN estimated_bytes ELSE COALESCE(rx_bytes,0)+COALESCE(tx_bytes,0) END) AS bytes
                    FROM network_domains_daily WHERE date = ?
                    GROUP BY agent_id, proto
                    """,
                    (day,),
                ).fetchall()
            finally:
                conn.close()

        def _merge_rows(rows, sum_keys, max_key=None, extras=False):
            merged: Dict[tuple, dict] = {}
            for r in rows:
                dom, cat = _normalize_domain(r["domain"], "", (r["category"] or "OTHER").upper())
                if not dom:
                    continue
                dom, cat, _e, _raw = _canonical_intel(dom, cat)
                m = merged.setdefault((r["agent_id"], dom, cat), {
                    "agent_id": r["agent_id"], "domain": dom, "category": cat, **{k: 0 for k in sum_keys},
                    **({max_key: ""} if max_key else {}),
                })
                for k in sum_keys:
                    m[k] += r[k] or 0
                if max_key and (r[max_key] or "") > m[max_key]:
                    m[max_key] = r[max_key] or ""
                if extras:
                    if r["direction"] == "INBOUND":
                        m["direction"], m["port"] = "INBOUND", r["port"] or 443
                    else:
                        m.setdefault("direction", "OUTBOUND")
                        m.setdefault("port", r["port"] or 443)
                    m["conf"] = max(m.get("conf", 0), r["conf"] or 0)
                    ls = r["last_seen"] or ""
                    if r["ptype"] and ls >= m.get("_ls", ""):
                        m["_ls"], m["ptype"], m["focus"] = ls, r["ptype"], r["focus"] or ""
                    m.setdefault("ptype", "")
                    m.setdefault("focus", "")
                    if ls > m.get("last_seen", ""):
                        m["last_seen"] = ls
            return list(merged.values())

        dom_rows = _merge_rows(dom_rows, ("hits", "est", "keys", "rx", "tx"), extras=True)
        recent_rows = _merge_rows(recent_rows, (), "last_seen")
        valid_ids = {d["agent_id"] for d in devices}
        by_agent: Dict[str, List[dict]] = {}
        global_domains: Dict[str, dict] = {}
        categories: Dict[str, dict] = {}
        packet_types: Dict[str, dict] = {}
        inbound_flows_count = 0
        for r in dom_rows:
            if r["agent_id"] not in valid_ids:
                continue
            by_agent.setdefault(r["agent_id"], []).append(r)
            g = global_domains.setdefault(r["domain"], {
                "domain": r["domain"], "category": r["category"], "hits": 0, "estimated_bytes": 0,
                "keystrokes_count": 0, "last_packet_type": "", "focus_app": "", "confidence": 0, "_ls": "",
                "rx_bytes": 0, "tx_bytes": 0, "direction": "OUTBOUND", "port": r.get("port", 443),
            })
            g["rx_bytes"] += r.get("rx", 0) or 0
            g["tx_bytes"] += r.get("tx", 0) or 0
            if r.get("direction") == "INBOUND":
                g["direction"], g["port"] = "INBOUND", r.get("port", 443)
                inbound_flows_count += 1
            g["hits"] += r["hits"] or 0
            g["estimated_bytes"] += r["est"] or 0
            g["keystrokes_count"] += r.get("keys", 0) or 0
            g["confidence"] = max(g["confidence"], r.get("conf", 0) or 0)
            if r.get("ptype") and r.get("_ls", "") >= g["_ls"]:
                g["_ls"], g["last_packet_type"], g["focus_app"] = r["_ls"], r["ptype"], r.get("focus", "")
            if r.get("ptype"):
                pt = packet_types.setdefault(r["ptype"], {"packet_type": r["ptype"], "estimated_bytes": 0, "hits": 0, "keystrokes": 0})
                pt["estimated_bytes"] += r["est"] or 0
                pt["hits"] += r["hits"] or 0
                pt["keystrokes"] += r.get("keys", 0) or 0
            c = categories.setdefault(r["category"] or "OTHER", {"category": r["category"] or "OTHER", "hits": 0, "estimated_bytes": 0})
            c["hits"] += r["hits"] or 0
            c["estimated_bytes"] += r["est"] or 0

        # MF-06: agregación de protocolos L7 (por agente y global)
        proto_by_agent: Dict[str, Dict[str, dict]] = {}
        global_protocols: Dict[str, dict] = {}
        for pr in proto_rows:
            aid = pr["agent_id"]
            if aid not in valid_ids:
                continue
            proto = pr["proto"] or "Desconocido"
            pbytes = int(pr["bytes"] or 0)
            phits = int(pr["hits"] or 0)
            a_map = proto_by_agent.setdefault(aid, {})
            pe = a_map.setdefault(proto, {"proto_l7": proto, "bytes": 0, "connections": 0})
            pe["bytes"] += pbytes
            pe["connections"] += phits
            g = global_protocols.setdefault(proto, {"proto_l7": proto, "bytes": 0, "connections": 0})
            g["bytes"] += pbytes
            g["connections"] += phits

        def _proto_list_with_pct(d: Dict[str, dict]) -> List[dict]:
            items = sorted(d.values(), key=lambda x: x["bytes"], reverse=True)
            tot = sum(x["bytes"] for x in items) or 1
            for it in items:
                it["percent"] = round(100.0 * it["bytes"] / tot, 1)
            return items

        # MF-06: interfaces de red por agente y global (consulta fuera del lock anterior)
        iface_by_agent = self.get_network_interfaces_summary(date_str=day, allowed_tenant_id=allowed_tenant_id)
        global_interfaces: Dict[str, dict] = {}
        for aid, ifs in iface_by_agent.items():
            if aid not in valid_ids:
                continue
            for it in ifs:
                kind = it["kind"]
                gi = global_interfaces.setdefault(kind, {
                    "kind": kind, "rx_bytes": 0, "tx_bytes": 0, "total_bytes": 0, "ifaces": 0,
                })
                gi["rx_bytes"] += it["rx_bytes"]
                gi["tx_bytes"] += it["tx_bytes"]
                gi["total_bytes"] += it["total_bytes"]
                gi["ifaces"] += 1

        generic_domains = {"google.com", "microsoft.com"}
        priority_cats = {"STREAMING", "SOCIAL", "MESSAGING", "AI", "GAMING", "SHOPPING", "CLOUD"}
        recent_by_agent: Dict[str, List[dict]] = {}
        for r in recent_rows:
            recent_by_agent.setdefault(r["agent_id"], []).append(r)

        def _recent_top(aid: str) -> str:
            cands = [r for r in recent_by_agent.get(aid, []) if r["domain"] not in generic_domains]
            if not cands:
                return ""
            cands.sort(key=lambda r: (
                (r["category"] or "").upper() in priority_cats,
                r["last_seen"] or "",
            ), reverse=True)
            return cands[0]["domain"]

        clients = []
        total_rx = total_tx = 0
        for dev in devices:
            aid = dev["agent_id"]
            u = usage.get(aid, {})
            rx = int(u.get("rx_bytes") or 0)
            tx = int(u.get("tx_bytes") or 0)
            total_rx += rx
            total_tx += tx
            doms = by_agent.get(aid, [])
            top = max(doms, key=lambda x: (x["est"] or 0, x["hits"] or 0), default=None)
            # Los teclazos de un slice se replican en cada dominio del slice: el máximo evita contarlos N veces.
            keys_total = max((x.get("keys", 0) or 0 for x in doms), default=0)
            latest_pkt = max((x for x in doms if x.get("ptype")), key=lambda x: x.get("_ls", ""), default=None)
            tid = dev["tenant_id"] or MASTER_TENANT_ID
            clients.append({
                "agent_id": aid,
                "alias": dev["alias"],
                "hostname": dev["hostname"],
                "username": dev["username"],
                "tenant_id": tid,
                "tenant_name": tenant_names.get(tid, tid),
                "is_online": bool(dev["is_online"]),
                "rx_bytes": rx,
                "tx_bytes": tx,
                "total_bytes": rx + tx,
                "peak_rx_kbps": float(u.get("peak_rx_kbps") or 0),
                "peak_tx_kbps": float(u.get("peak_tx_kbps") or 0),
                "domains_count": len(doms),
                "top_domain": _recent_top(aid) or (top["domain"] if top else ""),
                "last_packet_type": latest_pkt["ptype"] if latest_pkt else "",
                "keystrokes_total": int(keys_total),
                "protocols_l7": _proto_list_with_pct({k: dict(v) for k, v in proto_by_agent.get(aid, {}).items()}),
                "interfaces": iface_by_agent.get(aid, []),
                "primary_iface": (iface_by_agent.get(aid, [{}])[0] or {}).get("kind", "") if iface_by_agent.get(aid) else "",
            })
        clients.sort(key=lambda c: (
            -round(c["total_bytes"] / (1024 * 1024)),
            (c["hostname"] or "").lower(),
            (c["username"] or "").lower(),
            c["agent_id"],
        ))
        for g in global_domains.values():
            g.pop("_ls", None)
        top_domains = sorted(global_domains.values(), key=lambda x: (x["estimated_bytes"], x["hits"]), reverse=True)[:10]
        for g in top_domains:
            g.update(intel_public_view(get_or_enqueue_intel(g["domain"])))
        # Favicon del dominio destacado de cada cliente (sin consultas externas si ya es conocido).
        for c in clients:
            c["top_domain_favicon"] = intel_public_view(get_or_enqueue_intel(c["top_domain"]))["favicon_url"] if c["top_domain"] else ""
        top_client = clients[0] if clients and clients[0]["total_bytes"] > 0 else None
        return {
            "date": day,
            "inbound_flows_count": inbound_flows_count,
            "total_rx_bytes": total_rx,
            "total_tx_bytes": total_tx,
            "top_client": top_client,
            "top_domain": top_domains[0] if top_domains else None,
            "top_domains": top_domains,
            "categories": sorted(categories.values(), key=lambda x: x["estimated_bytes"], reverse=True),
            "clients": clients,
            "packet_types": sorted(packet_types.values(), key=lambda x: x["estimated_bytes"], reverse=True),
            "protocols_l7": _proto_list_with_pct({k: dict(v) for k, v in global_protocols.items()}),
            "interfaces": sorted(global_interfaces.values(), key=lambda x: x["total_bytes"], reverse=True),
        }

    def get_agent_domains_detail(
        self,
        date_str: Optional[str] = None,
        agent_id: Optional[str] = "ALL",
        category: Optional[str] = "ALL",
        search_q: Optional[str] = "",
        limit: int = 300,
        allowed_tenant_id: Optional[str] = None,
    ) -> List[dict]:
        day = self._clean_date(date_str) or self._now_dt().strftime("%Y-%m-%d")
        self.backfill_window_sites_today()
        intel_seed()
        clauses = ["n.date = ?"]
        params: list = [day]
        if allowed_tenant_id:
            clauses.append("n.agent_id IN (SELECT agent_id FROM devices WHERE tenant_id = ?)")
            params.append(allowed_tenant_id)
        if agent_id and agent_id != "ALL":
            clauses.append("n.agent_id = ?")
            params.append(agent_id)
        # Categoría y búsqueda se aplican tras normalizar (filas históricas guardan dominios CDN crudos).
        q_low = (search_q or "").strip().lower()
        max_rows = max(1, min(5000, int(limit)))
        params.append(5000)
        sql = f"""
            SELECT n.date, n.agent_id, n.domain, n.app_name, n.category, n.hit_count, n.estimated_bytes,
                   n.first_seen_at, n.last_seen_at,
                   COALESCE(n.keystrokes_count, 0) AS keystrokes_count,
                   COALESCE(n.last_packet_type, '') AS last_packet_type,
                   COALESCE(n.focus_app, '') AS focus_app,
                   COALESCE(n.confidence, 80) AS confidence,
                   CASE WHEN COALESCE(n.rx_bytes, 0) + COALESCE(n.tx_bytes, 0) = 0 AND n.estimated_bytes > 0
                        THEN CAST(n.estimated_bytes * 0.85 AS INTEGER) ELSE COALESCE(n.rx_bytes, 0) END AS rx_bytes,
                   CASE WHEN COALESCE(n.rx_bytes, 0) + COALESCE(n.tx_bytes, 0) = 0 AND n.estimated_bytes > 0
                        THEN n.estimated_bytes - CAST(n.estimated_bytes * 0.85 AS INTEGER) ELSE COALESCE(n.tx_bytes, 0) END AS tx_bytes,
                   COALESCE(n.direction, 'OUTBOUND') AS direction, COALESCE(n.port, 443) AS port,
                   COALESCE(NULLIF(n.proto_l7, ''), 'Desconocido') AS proto_l7,
                   COALESCE(n.iface, '') AS iface, COALESCE(n.iface_kind, '') AS iface_kind,
                   COALESCE(d.alias, n.agent_id) AS alias, COALESCE(d.hostname, '') AS hostname,
                   COALESCE(d.username, '') AS username
            FROM network_domains_daily n
            LEFT JOIN devices d ON d.agent_id = n.agent_id
            WHERE {' AND '.join(clauses)}
            ORDER BY n.estimated_bytes DESC, n.hit_count DESC
            LIMIT ?
        """
        with self._lock:
            conn = self._get_conn()
            try:
                raw = [dict(r) for r in conn.execute(sql, params).fetchall()]
            finally:
                conn.close()
        merged: Dict[tuple, dict] = {}
        for r in raw:
            dom, cat = _normalize_domain(r["domain"], r["app_name"] or "", (r["category"] or "OTHER").upper())
            if not dom:
                continue
            orig_dom = dom
            dom, cat, _entry, _raw = _canonical_intel(dom, cat, r["app_name"] or "")
            if category and category != "ALL" and cat != category:
                continue
            if q_low and not any(q_low in str(v or "").lower() for v in (dom, orig_dom, r["domain"], r["app_name"], r["alias"], r["hostname"])):
                continue
            key = (r["agent_id"], dom)
            app = (r["app_name"] or "").strip()
            m = merged.get(key)
            if m is None:
                merged[key] = {**r, "domain": dom, "category": cat, "_apps": {app: r["estimated_bytes"] or 0} if app else {},
                               "_raws": {_raw} if _raw else set(), "_intel": _entry if _raw else None}
                continue
            if _raw:
                m["_raws"].add(_raw)
                if not m.get("_intel"):
                    m["_intel"] = _entry
            if app:
                m["_apps"][app] = m["_apps"].get(app, 0) + (r["estimated_bytes"] or 0)
            m["hit_count"] += r["hit_count"] or 0
            m["estimated_bytes"] += r["estimated_bytes"] or 0
            m["rx_bytes"] = (m["rx_bytes"] or 0) + (r["rx_bytes"] or 0)
            m["tx_bytes"] = (m["tx_bytes"] or 0) + (r["tx_bytes"] or 0)
            if r["direction"] == "INBOUND":
                m["direction"], m["port"] = "INBOUND", r["port"]
            # Conservar la clasificación L7 más específica y la interfaz atribuida
            if (not m.get("proto_l7") or m.get("proto_l7") == "Desconocido") and r.get("proto_l7") and r["proto_l7"] != "Desconocido":
                m["proto_l7"] = r["proto_l7"]
            if not m.get("iface") and r.get("iface"):
                m["iface"], m["iface_kind"] = r["iface"], r["iface_kind"]
            m["first_seen_at"] = min(m["first_seen_at"] or "9", r["first_seen_at"] or "9")
            if (r["last_seen_at"] or "") >= (m["last_seen_at"] or "") and r["last_packet_type"]:
                m["last_packet_type"], m["focus_app"] = r["last_packet_type"], r["focus_app"]
            m["last_seen_at"] = max(m["last_seen_at"] or "", r["last_seen_at"] or "")
            m["keystrokes_count"] = (m["keystrokes_count"] or 0) + (r["keystrokes_count"] or 0)
            m["confidence"] = max(m["confidence"] or 0, r["confidence"] or 0)
        for m in merged.values():
            apps = m.pop("_apps", {})
            ranked = sorted(apps.items(), key=lambda kv: (_app_noise_rank(kv[0]), -kv[1]))
            if ranked:
                m["app_name"] = ranked[0][0]
            m["apps_list"] = ", ".join(a for a, _ in ranked)
        out = sorted(merged.values(), key=lambda x: (x["estimated_bytes"], x["hit_count"]), reverse=True)[:max_rows]
        for row in out:
            raws = sorted(row.pop("_raws", set()))
            ip_entry = row.pop("_intel", None)
            entry = get_or_enqueue_intel(row["domain"], row.get("app_name") or "")
            row.update(intel_public_view(entry))
            if ip_entry:
                pv = intel_public_view(ip_entry)
                for k, v in pv.items():
                    if k != "raw_target" and v and not row.get(k):
                        row[k] = v
                if not row.get("org_name") and ip_entry.get("org_name"):
                    row["org_name"] = ip_entry["org_name"]
            if raws:
                row["raw_target"] = ", ".join(raws)
            elif not row["raw_target"] and entry.get("is_ip"):
                row["raw_target"] = row["domain"]
            if raws and not row.get("resolved_domain"):
                row["resolved_domain"] = row["domain"]
            if (row.get("category") or "OTHER") == "OTHER" and entry.get("category") not in (None, "", "OTHER"):
                row["category"] = entry["category"]
        return out

    def export_network_csv(
        self,
        date_str: Optional[str] = None,
        agent_id: Optional[str] = "ALL",
        category: Optional[str] = "ALL",
        search_q: Optional[str] = "",
        allowed_tenant_id: Optional[str] = None,
    ) -> str:
        rows = self.get_agent_domains_detail(
            date_str=date_str, agent_id=agent_id, category=category,
            search_q=search_q, limit=5000, allowed_tenant_id=allowed_tenant_id,
        )
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["Fecha", "Equipo (Alias)", "Hostname", "Usuario", "Dominio", "Aplicación", "Categoría",
                         "Conexiones", "Bytes Estimados", "Primera Vez", "Última Vez"])
        for r in rows:
            writer.writerow([
                r["date"], _csv_safe(r["alias"]), _csv_safe(r["hostname"]), _csv_safe(r["username"]),
                _csv_safe(r["domain"]), _csv_safe(r["app_name"]), r["category"] or "",
                r["hit_count"], r["estimated_bytes"], r["first_seen_at"] or "", r["last_seen_at"] or "",
            ])
        return out.getvalue()

    def get_daily_metrics(
        self,
        date_str: Optional[str] = None,
        allowed_tenant_id: Optional[str] = None,
    ) -> List[dict]:
        target_date = date_str or self._now_dt().strftime("%Y-%m-%d")
        tenant_names = self.get_tenant_name_map()
        with self._lock:
            conn = self._get_conn()
            try:
                if allowed_tenant_id:
                    devices_rows = conn.execute(
                        "SELECT * FROM devices WHERE tenant_id = ? ORDER BY alias ASC",
                        (allowed_tenant_id,),
                    ).fetchall()
                else:
                    devices_rows = conn.execute("SELECT * FROM devices ORDER BY alias ASC").fetchall()
                summary_rows = conn.execute(
                    "SELECT * FROM daily_activity_summary WHERE date = ?",
                    (target_date,),
                ).fetchall()
                summary_map = {r["agent_id"]: dict(r) for r in summary_rows}

                apps_rows = conn.execute(
                    "SELECT agent_id, app_name, total_seconds FROM window_usage_daily WHERE date = ? ORDER BY total_seconds DESC",
                    (target_date,),
                ).fetchall()

                apps_by_agent: Dict[str, List[dict]] = {}
                for ar in apps_rows:
                    apps_by_agent.setdefault(ar["agent_id"], []).append({
                        "app_name": ar["app_name"],
                        "seconds": ar["total_seconds"] or 0,
                    })

                # MF-BE01: agregacion horaria y switches por agente
                hourly_rows = conn.execute(
                    """
                    SELECT agent_id, hour, SUM(active_seconds) AS act, SUM(idle_seconds) AS idl, SUM(switches_count) AS sw
                    FROM screen_time_hourly
                    WHERE date = ?
                    GROUP BY agent_id, hour
                    """,
                    (target_date,),
                ).fetchall()
                hourly_by_agent: Dict[str, dict] = {}
                for hr in hourly_rows:
                    h = hr["hour"]
                    if h is None or not (0 <= int(h) <= 23):
                        continue
                    slot = hourly_by_agent.setdefault(hr["agent_id"], {
                        "active": [0] * 24,
                        "idle": [0] * 24,
                        "switches": 0,
                    })
                    slot["active"][int(h)] += int(hr["act"] or 0)
                    slot["idle"][int(h)] += int(hr["idl"] or 0)
                    slot["switches"] += int(hr["sw"] or 0)

                # MF-BE02: categorias y top apps categorizadas
                res_rows = conn.execute(
                    """
                    SELECT agent_id, app_name, COALESCE(MAX(category_tag), 'Productividad y Gestion') AS cat,
                           SUM(total_seconds) AS secs, SUM(switches_count) AS sw
                    FROM resource_usage_daily
                    WHERE date = ?
                    GROUP BY agent_id, app_name
                    ORDER BY secs DESC
                    """,
                    (target_date,),
                ).fetchall()
                res_by_agent: Dict[str, List[dict]] = {}
                for rr in res_rows:
                    res_by_agent.setdefault(rr["agent_id"], []).append({
                        "app_name": rr["app_name"],
                        "category": rr["cat"] or "Productividad y Gestion",
                        "seconds": int(rr["secs"] or 0),
                        "switches_count": int(rr["sw"] or 0),
                    })

                # MF-BE03: consumo de red diario
                net_rows = conn.execute(
                    """
                    SELECT agent_id, COALESCE(SUM(tx_bytes), 0) AS tx, COALESCE(SUM(rx_bytes), 0) AS rx
                    FROM network_usage_daily
                    WHERE date = ?
                    GROUP BY agent_id
                    """,
                    (target_date,),
                ).fetchall()
                net_by_agent = {
                    nr["agent_id"]: (int(nr["rx"] or 0), int(nr["tx"] or 0)) for nr in net_rows
                }

                productive_cats = {
                    "Productividad y Gestion",
                    "Desarrollo y Sistemas",
                    "Comunicacion",
                    "Diseno y Creatividad",
                }
                distractor_cats = {"Entretenimiento y Redes"}

                result = []
                for dev in devices_rows:
                    aid = dev["agent_id"]
                    tid = dev["tenant_id"] or MASTER_TENANT_ID
                    s = summary_map.get(aid, {
                        "first_seen_at": "---",
                        "active_seconds": 0,
                        "idle_seconds": 0,
                        "away_alerts_count": 0,
                    })

                    # Horario
                    hslot = hourly_by_agent.get(aid)
                    hourly_active = list(hslot["active"]) if hslot else [0] * 24
                    hourly_idle = list(hslot["idle"]) if hslot else [0] * 24
                    switches_total = hslot["switches"] if hslot else 0
                    agent_res = res_by_agent.get(aid, [])
                    if switches_total == 0 and agent_res:
                        switches_total = sum(x["switches_count"] for x in agent_res)
                    peak_hour = None
                    peak_val = 0
                    for h_idx, val in enumerate(hourly_active):
                        if val > peak_val:
                            peak_val = val
                            peak_hour = h_idx

                    # Apps categorizadas (fallback a window_usage_daily)
                    if agent_res:
                        app_items = agent_res
                    else:
                        app_items = [
                            {
                                "app_name": x["app_name"],
                                "category": "Productividad y Gestion",
                                "seconds": x["seconds"],
                                "switches_count": 0,
                            }
                            for x in apps_by_agent.get(aid, [])
                        ]
                    total_app_secs = sum(x["seconds"] for x in app_items)
                    denom = total_app_secs or 1

                    cat_totals: Dict[str, int] = {}
                    for x in app_items:
                        cat_totals[x["category"]] = cat_totals.get(x["category"], 0) + x["seconds"]
                    categories = [
                        {
                            "category": c,
                            "seconds": secs,
                            "percent": round((secs * 100.0) / denom) if total_app_secs else 0,
                        }
                        for c, secs in sorted(cat_totals.items(), key=lambda kv: kv[1], reverse=True)
                    ]
                    productive_seconds = sum(v for c, v in cat_totals.items() if c in productive_cats)
                    distractor_seconds = sum(v for c, v in cat_totals.items() if c in distractor_cats)
                    neutral_seconds = max(0, total_app_secs - productive_seconds - distractor_seconds)
                    pd_sum = productive_seconds + distractor_seconds
                    focus_score = round(productive_seconds * 100.0 / pd_sum) if pd_sum > 0 else None

                    top_apps = []
                    for item in app_items[:6]:
                        pct = round((item["seconds"] * 100.0) / denom)
                        top_apps.append({
                            "app_name": item["app_name"],
                            "seconds": item["seconds"],
                            "percent": max(1, pct),
                            "category": item["category"],
                            "switches_count": item["switches_count"],
                        })

                    net_rx, net_tx = net_by_agent.get(aid, (0, 0))

                    result.append({
                        "agent_id": aid,
                        "alias": dev["alias"],
                        "hostname": dev["hostname"],
                        "username": dev["username"],
                        "tenant_id": tid,
                        "tenant_name": tenant_names.get(tid, tid),
                        "is_online": bool(dev["is_online"]),
                        "last_seen_at": dev["last_seen_at"],
                        "date": target_date,
                        "first_seen_at": s["first_seen_at"],
                        "active_seconds": s["active_seconds"],
                        "idle_seconds": s["idle_seconds"],
                        "away_alerts_count": s["away_alerts_count"],
                        "top_apps": top_apps,
                        "hourly_active": hourly_active,
                        "hourly_idle": hourly_idle,
                        "switches_count": switches_total,
                        "peak_hour": peak_hour,
                        "categories": categories,
                        "productive_seconds": productive_seconds,
                        "distractor_seconds": distractor_seconds,
                        "neutral_seconds": neutral_seconds,
                        "focus_score": focus_score,
                        "net_rx_bytes": net_rx,
                        "net_tx_bytes": net_tx,
                        "net_total_bytes": net_rx + net_tx,
                    })
                return result
            finally:
                conn.close()

    def add_chat_message(
        self,
        agent_id: str,
        sender: str,
        sender_name: str,
        message: str,
    ) -> dict:
        now_str = self._now_dt().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO chat_messages (timestamp, agent_id, sender, sender_name, message)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (now_str, agent_id, sender, sender_name, message),
                )
                msg_id = cur.lastrowid
                conn.commit()
                return {
                    "id": msg_id,
                    "timestamp": now_str,
                    "agent_id": agent_id,
                    "sender": sender,
                    "sender_name": sender_name,
                    "message": message,
                }
            finally:
                conn.close()

    def get_chat_history(self, agent_id: str, limit: int = 100) -> List[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM chat_messages
                        WHERE agent_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    ) ORDER BY id ASC
                    """,
                    (agent_id, max(1, min(500, int(limit)))),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()


    def get_screen_time_analytics(
        self,
        agent_id: str = "ALL",
        date_str: Optional[str] = None,
        period: str = "day",
        allowed_tenant_id: Optional[str] = None,
    ) -> dict:
        """MF-ST08/ST09: Consulta jerarquica estilo Tiempo en Pantalla (24h, Categorias, App -> Pestanas)."""
        base_str = (date_str or "").strip() or self._now_dt().strftime("%Y-%m-%d")
        try:
            anchor_dt = datetime.strptime(base_str[:10], "%Y-%m-%d")
        except Exception:
            anchor_dt = self._now_dt()
            base_str = anchor_dt.strftime("%Y-%m-%d")

        is_7d = str(period).lower() == "7d"
        if is_7d:
            start_str = (anchor_dt - timedelta(days=6)).strftime("%Y-%m-%d")
            end_str = base_str
            prev_start = (anchor_dt - timedelta(days=13)).strftime("%Y-%m-%d")
            prev_end = (anchor_dt - timedelta(days=7)).strftime("%Y-%m-%d")
        else:
            start_str = end_str = base_str
            prev_start = prev_end = (anchor_dt - timedelta(days=1)).strftime("%Y-%m-%d")

        with self._lock:
            conn = self._get_conn()
            try:
                dev_clauses, dev_params = [], []
                if allowed_tenant_id:
                    dev_clauses.append("tenant_id = ?")
                    dev_params.append(allowed_tenant_id)
                if agent_id and agent_id != "ALL":
                    dev_clauses.append("agent_id = ?")
                    dev_params.append(agent_id)
                where_dev = f"WHERE {' AND '.join(dev_clauses)}" if dev_clauses else ""
                dev_rows = conn.execute(f"SELECT agent_id, alias FROM devices {where_dev} ORDER BY alias ASC", dev_params).fetchall()
                valid_ids = [r["agent_id"] for r in dev_rows]
                if not valid_ids:
                    return {
                        "date": base_str, "period": "7d" if is_7d else "day", "agent_id": agent_id,
                        "summary": {"active_seconds": 0, "idle_seconds": 0, "switches_count": 0, "prev_active_seconds": 0, "delta_pct": 0},
                        "hourly": [{"hour": h, "active_seconds": 0, "idle_seconds": 0, "switches_count": 0} for h in range(24)],
                        "categories": [], "apps": [],
                    }

                ph = ",".join("?" for _ in valid_ids)
                cur_sum = conn.execute(
                    f"SELECT COALESCE(SUM(active_seconds),0) AS act, COALESCE(SUM(idle_seconds),0) AS idl FROM daily_activity_summary WHERE date BETWEEN ? AND ? AND agent_id IN ({ph})",
                    [start_str, end_str, *valid_ids],
                ).fetchone()
                prev_sum = conn.execute(
                    f"SELECT COALESCE(SUM(active_seconds),0) AS act FROM daily_activity_summary WHERE date BETWEEN ? AND ? AND agent_id IN ({ph})",
                    [prev_start, prev_end, *valid_ids],
                ).fetchone()

                hourly_map = {h: {"hour": h, "active_seconds": 0, "idle_seconds": 0, "switches_count": 0} for h in range(24)}
                h_rows = conn.execute(
                    f"SELECT hour, SUM(active_seconds) AS act, SUM(idle_seconds) AS idl, SUM(switches_count) AS sw FROM screen_time_hourly WHERE date BETWEEN ? AND ? AND agent_id IN ({ph}) GROUP BY hour",
                    [start_str, end_str, *valid_ids],
                ).fetchall()
                total_switches = 0
                for hr in h_rows:
                    hv = max(0, min(23, int(hr["hour"] or 0)))
                    sw_v = int(hr["sw"] or 0)
                    total_switches += sw_v
                    hourly_map[hv] = {"hour": hv, "active_seconds": int(hr["act"] or 0), "idle_seconds": int(hr["idl"] or 0), "switches_count": sw_v}

                r_rows = conn.execute(
                    f"""
                    SELECT app_name, resource_title, MAX(category_tag) AS cat, SUM(total_seconds) AS secs, SUM(switches_count) AS sw, MAX(last_seen_at) AS seen
                    FROM resource_usage_daily
                    WHERE date BETWEEN ? AND ? AND agent_id IN ({ph})
                    GROUP BY app_name, resource_title
                    ORDER BY secs DESC
                    """,
                    [start_str, end_str, *valid_ids],
                ).fetchall()

                if not r_rows:
                    w_rows = conn.execute(
                        f"SELECT app_name, SUM(total_seconds) AS secs FROM window_usage_daily WHERE date BETWEEN ? AND ? AND agent_id IN ({ph}) GROUP BY app_name ORDER BY secs DESC",
                        [start_str, end_str, *valid_ids],
                    ).fetchall()
                    r_rows = [{"app_name": wr["app_name"], "resource_title": "Ventana principal", "cat": "Productividad y Gestion", "secs": wr["secs"], "sw": 0, "seen": ""} for wr in w_rows]

                cat_totals: Dict[str, int] = {}
                apps_dict: Dict[str, dict] = {}
                for rr in r_rows:
                    secs = int(rr["secs"] or 0)
                    sw = int(rr["sw"] or 0)
                    if secs <= 0 and sw <= 0:
                        continue
                    app_n = str(rr["app_name"] or "Escritorio")
                    res_t = str(rr["resource_title"] or "Ventana principal")
                    cat_t = str(rr["cat"] or "Productividad y Gestion")
                    cat_totals[cat_t] = cat_totals.get(cat_t, 0) + secs
                    entry = apps_dict.setdefault(app_n, {"app_name": app_n, "category": cat_t, "total_seconds": 0, "switches_count": 0, "resources": []})
                    entry["total_seconds"] += secs
                    entry["switches_count"] += sw
                    entry["resources"].append({"resource_title": res_t, "category": cat_t, "seconds": secs, "switches_count": sw, "last_seen_at": str(rr["seen"] or "")})

                total_res_secs = sum(cat_totals.values()) or 1
                categories = [
                    {"category": k, "seconds": v, "percent": max(1, round(v * 100.0 / total_res_secs))}
                    for k, v in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True) if v > 0
                ]
                apps_list = sorted(apps_dict.values(), key=lambda a: (a["total_seconds"], a["switches_count"]), reverse=True)
                for ap in apps_list[:40]:
                    ap["percent"] = max(1, round(ap["total_seconds"] * 100.0 / total_res_secs)) if ap["total_seconds"] > 0 else 0
                    a_tot = ap["total_seconds"] or 1
                    ap["resources"].sort(key=lambda r: (r["seconds"], r["switches_count"]), reverse=True)
                    ap["resources"] = ap["resources"][:30]
                    for res_item in ap["resources"]:
                        res_item["percent"] = max(1, round(res_item["seconds"] * 100.0 / a_tot)) if res_item["seconds"] > 0 else 0

                act_s = int(cur_sum["act"] or 0)
                idl_s = int(cur_sum["idl"] or 0)
                prev_s = int(prev_sum["act"] or 0)
                delta_pct = round(((act_s - prev_s) * 100.0) / prev_s) if prev_s > 0 else 0

                return {
                    "date": base_str,
                    "start_date": start_str,
                    "end_date": end_str,
                    "period": "7d" if is_7d else "day",
                    "agent_id": agent_id,
                    "summary": {
                        "active_seconds": act_s,
                        "idle_seconds": idl_s,
                        "switches_count": total_switches,
                        "prev_active_seconds": prev_s,
                        "delta_pct": delta_pct,
                    },
                    "hourly": [hourly_map[h] for h in range(24)],
                    "categories": categories,
                    "apps": apps_list[:40],
                }
            finally:
                conn.close()

    def get_dvr_day_data(
        self,
        agent_id: Optional[str] = None,
        target_date: Optional[str] = None,
        category: Optional[str] = None,
        search_q: Optional[str] = None,
        allowed_tenant_id: Optional[str] = None,
    ) -> dict:
        now_dt = self._now_dt()
        today_str = now_dt.strftime("%Y-%m-%d")
        day_str = (target_date or "").strip()[:10]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day_str):
            day_str = today_str
        now_min = now_dt.hour * 60 + now_dt.minute

        dev_clause = ""
        base_params: list = []
        if allowed_tenant_id:
            dev_clause += " AND agent_id IN (SELECT agent_id FROM devices WHERE tenant_id = ?)"
            base_params.append(allowed_tenant_id)
        if agent_id and agent_id != "ALL":
            dev_clause += " AND agent_id = ?"
            base_params.append(agent_id)

        with self._lock:
            conn = self._get_conn()
            try:
                dates_rows = conn.execute(
                    f"""
                    SELECT DISTINCT d FROM (
                        SELECT substr(timestamp, 1, 10) AS d FROM device_logs WHERE length(timestamp) >= 10 {dev_clause}
                        UNION
                        SELECT date AS d FROM daily_activity_summary WHERE 1=1 {dev_clause}
                    ) WHERE d GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                    ORDER BY d DESC LIMIT 14
                    """,
                    base_params + base_params,
                ).fetchall()
                avail_dates = [r["d"] for r in dates_rows if r["d"]]
                if today_str not in avail_dates:
                    avail_dates.insert(0, today_str)
                avail_dates = sorted(set(avail_dates), reverse=True)[:14]

                b_rows = conn.execute(
                    f"SELECT slot_5m, SUM(active_seconds) AS act, SUM(idle_seconds) AS idl FROM dvr_timeline_buckets WHERE date = ? {dev_clause} GROUP BY slot_5m",
                    [day_str] + base_params,
                ).fetchall()
                buckets = {int(r["slot_5m"]): (int(r["act"] or 0), int(r["idl"] or 0)) for r in b_rows}

                h_rows = conn.execute(
                    f"SELECT hour, SUM(active_seconds) AS act, SUM(idle_seconds) AS idl FROM screen_time_hourly WHERE date = ? {dev_clause} GROUP BY hour",
                    [day_str] + base_params,
                ).fetchall()
                hourly_fb = {int(r["hour"]): (int(r["act"] or 0), int(r["idl"] or 0)) for r in h_rows}

                all_day_logs = conn.execute(
                    f"SELECT * FROM device_logs WHERE timestamp LIKE ? {dev_clause} ORDER BY id ASC LIMIT 2500",
                    [f"{day_str}%"] + base_params,
                ).fetchall()
            finally:
                conn.close()

        slot_active_ev = [0] * 288
        slot_idle_ev = [0] * 288
        slot_caps = [0] * 288
        slot_total_ev = [0] * 288
        filtered_events = []
        q_low = (search_q or "").strip().lower()
        cat_f = (category or "ALL").strip().upper()

        for r in all_day_logs:
            d = dict(r)
            ts = str(d.get("timestamp") or "")
            h, m, s = 0, 0, 0
            if len(ts) >= 19:
                try:
                    h = max(0, min(23, int(ts[11:13])))
                    m = max(0, min(59, int(ts[14:16])))
                    s = max(0, min(59, int(ts[17:19])))
                except Exception:
                    pass
            sec_day = h * 3600 + m * 60 + s
            sl = max(0, min(287, sec_day // 300))
            c = str(d.get("category") or "WINDOW")
            slot_total_ev[sl] += 1
            if d.get("evidence_path"):
                slot_caps[sl] += 1
            if c in ("WINDOW", "KEYBOARD", "RESUME", "AUDIT"):
                slot_active_ev[sl] += 1
            else:
                slot_idle_ev[sl] += 1

            if cat_f != "ALL" and c != cat_f:
                continue
            if q_low:
                hay = f"{d.get('message','')} {d.get('alias','')} {d.get('hostname','')} {d.get('meta_json','')}".lower()
                if q_low not in hay:
                    continue
            d["sec_of_day"] = sec_day
            d["slot_5m"] = sl
            filtered_events.append(d)

        slots = []
        hours_with_5m = {sl_k // 12 for sl_k in buckets.keys()}
        for sl in range(288):
            start_m = sl * 5
            hr = sl // 12
            act_s, idl_s = buckets.get(sl, (0, 0))
            if hr not in hours_with_5m and hr in hourly_fb and slot_total_ev[sl] == 0:
                h_act, h_idl = hourly_fb[hr]
                if sum(slot_total_ev[hr * 12:(hr + 1) * 12]) == 0:
                    act_s, idl_s = h_act, h_idl
            if day_str > today_str or (day_str == today_str and start_m > now_min):
                st = "future"
            elif act_s > 0 or slot_active_ev[sl] > 0:
                st = "active"
            elif idl_s > 0 or slot_idle_ev[sl] > 0:
                st = "idle"
            else:
                st = "offline"
            slots.append({
                "slot": sl,
                "start_min": start_m,
                "state": st,
                "captures": slot_caps[sl],
                "events": slot_total_ev[sl],
            })

        return {
            "date": day_str,
            "today": today_str,
            "now_minute": now_min if day_str == today_str else 1439,
            "available_dates": avail_dates,
            "slots": slots,
            "events": filtered_events[-1500:],
        }


db = DatabaseManager()


