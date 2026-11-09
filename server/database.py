import csv
import io
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import config

DB_PATH: Path = config.DATA_DIR / "remote_assist.db"


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
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.executescript(
                    """
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
                    """
                )
                # Al iniciar el servidor, todos los equipos inician marcados como desconectados hasta que se reporten
                conn.execute("UPDATE devices SET is_online = 0")
                conn.commit()
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

    def upsert_device(
        self,
        agent_id: str,
        alias: str,
        hostname: str,
        username: str,
        os_version: str,
        local_ip: str,
        is_online: bool = True,
    ):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today_str = datetime.now().strftime("%Y-%m-%d")
        time_only = datetime.now().strftime("%H:%M:%S")

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO devices (agent_id, alias, hostname, username, os_version, local_ip, is_online, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        alias = excluded.alias,
                        hostname = excluded.hostname,
                        username = excluded.username,
                        os_version = excluded.os_version,
                        local_ip = excluded.local_ip,
                        is_online = excluded.is_online,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (agent_id, alias, hostname, username, os_version, local_ip, 1 if is_online else 0, now_str),
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
            finally:
                conn.close()

    def set_device_offline(self, agent_id: str):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

    def get_all_known_devices(self) -> List[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM devices ORDER BY is_online DESC, alias ASC"
                ).fetchall()
                return [dict(r) for r in rows]
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
    ) -> dict:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO device_logs (timestamp, agent_id, alias, hostname, username, category, message, duration_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (now_str, agent_id, alias, hostname, username, category, message, duration_seconds),
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
        today_str = datetime.now().strftime("%Y-%m-%d")
        time_only = datetime.now().strftime("%H:%M:%S")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    ) -> List[dict]:
        clauses = []
        params: list = []

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
    ) -> str:
        logs = self.query_logs(agent_id=agent_id, category=category, search_q=search_q, limit=2000)
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

    def get_daily_metrics(self, date_str: Optional[str] = None) -> List[dict]:
        target_date = date_str or datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            conn = self._get_conn()
            try:
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


db = DatabaseManager()

