import csv
import hashlib
import hmac
import io
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import config

DB_PATH: Path = config.DATA_DIR / "remote_assist.db"
MASTER_TENANT_ID = "sitai-master"


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
                conn.execute("DELETE FROM screen_time_hourly WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM resource_usage_daily WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM chat_messages WHERE agent_id = ?", (agent_id,))
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


