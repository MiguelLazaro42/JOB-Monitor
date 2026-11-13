import csv
import hashlib
import hmac
import io
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import config

try:
    from server.service_catalog import match_service_domain_or_ip
except ImportError:  # el servidor se ejecuta con server/ como directorio raíz
    from service_catalog import match_service_domain_or_ip

DB_PATH: Path = config.DATA_DIR / "remote_assist.db"
MASTER_TENANT_ID = "sitai-master"


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
    ):
        now_dt = self._now_dt()
        today_str = now_dt.strftime("%Y-%m-%d")
        time_only = now_dt.strftime("%H:%M:%S")
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        delta = max(1, min(30, int(delta_seconds)))
        act_inc = delta if is_active else 0
        idl_inc = 0 if is_active else delta
        alert_inc = 1 if increment_away_alert else 0
        clean_app = (app_name or "Escritorio").strip()[:60]

        with self._lock:
            conn = self._get_conn()
            try:
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
                if is_active and clean_app:
                    conn.execute(
                        """
                        INSERT INTO window_usage_daily (date, agent_id, app_name, total_seconds)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(date, agent_id, app_name) DO UPDATE SET
                            total_seconds = total_seconds + excluded.total_seconds
                        """,
                        (today_str, agent_id, clean_app, delta),
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
            try:
                hits = max(1, int(d.get("connections", d.get("hit_count", 1))))
                est = max(0, int(d.get("estimated_bytes", 0)))
            except (TypeError, ValueError):
                continue
            rows.append((day, agent_id, domain, app, cat, hits, est, now_str, now_str))
        if not rows:
            return
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executemany(
                    """
                    INSERT INTO network_domains_daily
                        (date, agent_id, domain, app_name, category, hit_count, estimated_bytes, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, agent_id, domain, app_name) DO UPDATE SET
                        hit_count = hit_count + excluded.hit_count,
                        estimated_bytes = estimated_bytes + excluded.estimated_bytes,
                        category = excluded.category,
                        last_seen_at = excluded.last_seen_at
                    """,
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

    def get_network_dashboard_summary(
        self,
        date_str: Optional[str] = None,
        allowed_tenant_id: Optional[str] = None,
    ) -> dict:
        day = self._clean_date(date_str) or self._now_dt().strftime("%Y-%m-%d")
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
                    SELECT agent_id, domain, category, SUM(hit_count) AS hits, SUM(estimated_bytes) AS est
                    FROM network_domains_daily WHERE date = ?
                    GROUP BY agent_id, domain, category
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
            finally:
                conn.close()

        def _merge_rows(rows, sum_keys, max_key=None):
            merged: Dict[tuple, dict] = {}
            for r in rows:
                dom, cat = _normalize_domain(r["domain"], "", (r["category"] or "OTHER").upper())
                if not dom:
                    continue
                m = merged.setdefault((r["agent_id"], dom, cat), {
                    "agent_id": r["agent_id"], "domain": dom, "category": cat, **{k: 0 for k in sum_keys},
                    **({max_key: ""} if max_key else {}),
                })
                for k in sum_keys:
                    m[k] += r[k] or 0
                if max_key and (r[max_key] or "") > m[max_key]:
                    m[max_key] = r[max_key] or ""
            return list(merged.values())

        dom_rows = _merge_rows(dom_rows, ("hits", "est"))
        recent_rows = _merge_rows(recent_rows, (), "last_seen")
        valid_ids = {d["agent_id"] for d in devices}
        by_agent: Dict[str, List[dict]] = {}
        global_domains: Dict[str, dict] = {}
        categories: Dict[str, dict] = {}
        for r in dom_rows:
            if r["agent_id"] not in valid_ids:
                continue
            by_agent.setdefault(r["agent_id"], []).append(r)
            g = global_domains.setdefault(r["domain"], {"domain": r["domain"], "category": r["category"], "hits": 0, "estimated_bytes": 0})
            g["hits"] += r["hits"] or 0
            g["estimated_bytes"] += r["est"] or 0
            c = categories.setdefault(r["category"] or "OTHER", {"category": r["category"] or "OTHER", "hits": 0, "estimated_bytes": 0})
            c["hits"] += r["hits"] or 0
            c["estimated_bytes"] += r["est"] or 0

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
            })
        clients.sort(key=lambda c: (
            -round(c["total_bytes"] / (1024 * 1024)),
            (c["hostname"] or "").lower(),
            (c["username"] or "").lower(),
            c["agent_id"],
        ))
        top_domains = sorted(global_domains.values(), key=lambda x: (x["estimated_bytes"], x["hits"]), reverse=True)[:10]
        top_client = clients[0] if clients and clients[0]["total_bytes"] > 0 else None
        return {
            "date": day,
            "total_rx_bytes": total_rx,
            "total_tx_bytes": total_tx,
            "top_client": top_client,
            "top_domain": top_domains[0] if top_domains else None,
            "top_domains": top_domains,
            "categories": sorted(categories.values(), key=lambda x: x["estimated_bytes"], reverse=True),
            "clients": clients,
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
            if category and category != "ALL" and cat != category:
                continue
            if q_low and not any(q_low in str(v or "").lower() for v in (dom, r["domain"], r["app_name"], r["alias"], r["hostname"])):
                continue
            key = (r["agent_id"], dom, r["app_name"])
            m = merged.get(key)
            if m is None:
                merged[key] = {**r, "domain": dom, "category": cat}
                continue
            m["hit_count"] += r["hit_count"] or 0
            m["estimated_bytes"] += r["estimated_bytes"] or 0
            m["first_seen_at"] = min(m["first_seen_at"] or "9", r["first_seen_at"] or "9")
            m["last_seen_at"] = max(m["last_seen_at"] or "", r["last_seen_at"] or "")
        return sorted(merged.values(), key=lambda x: (x["estimated_bytes"], x["hit_count"]), reverse=True)[:max_rows]

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
                        "seconds": ar["total_seconds"],
                    })

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
                    agent_apps = apps_by_agent.get(aid, [])
                    total_app_secs = sum(x["seconds"] for x in agent_apps) or 1
                    top_apps = []
                    for item in agent_apps[:4]:
                        pct = round((item["seconds"] * 100.0) / total_app_secs)
                        top_apps.append({
                            "app_name": item["app_name"],
                            "seconds": item["seconds"],
                            "percent": max(1, pct),
                        })

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


db = DatabaseManager()


