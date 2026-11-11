import time
from typing import Dict, List, Optional
from fastapi import WebSocket
from pydantic import BaseModel


class AliasUpdateRequest(BaseModel):
    alias: str


class ChatSendRequest(BaseModel):
    message: str


class ClipboardSetRequest(BaseModel):
    text: str
    auto_paste: bool = False


class UserCreateRequest(BaseModel):
    username: str
    password: str
    display_name: str
    role: str = "tenant_admin"  # 'tenant_admin' | 'sub_user'
    tenant_id: Optional[str] = None
    can_control: bool = True


class UserUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    password: Optional[str] = None
    can_control: Optional[bool] = None
    is_active: Optional[bool] = None
    tenant_id: Optional[str] = None


class DeviceTenantAssignRequest(BaseModel):
    tenant_id: str


class AgentConnection:
    """Representa una PC conectada activamente al Hub Central."""

    def __init__(
        self,
        agent_id: str,
        websocket: WebSocket,
        info: dict,
        saved_alias: Optional[str] = None,
        tenant_id: str = "sitai-master",
        tenant_name: str = "SITAI Global",
    ):
        self.agent_id: str = agent_id
        self.raw_agent_id: str = str(info.get("agent_id") or agent_id)
        self.raw_alias: str = str(info.get("alias") or "").strip()
        self.websocket: WebSocket = websocket
        self.hostname: str = info.get("hostname", "PC-Desconocida")
        self.username: str = info.get("username", "usuario")
        self.alias: str = saved_alias or self.raw_alias or f"{self.hostname} ({self.username})"
        self.os_name: str = info.get("os", "Windows")
        self.local_ip: str = info.get("local_ip", "")
        self.tenant_id: str = tenant_id or "sitai-master"
        self.tenant_name: str = tenant_name or "SITAI Global"
        self.monitors: List[dict] = info.get("monitors", [])
        self.current_monitor: int = int(info.get("current_monitor", 1))
        self.idle_seconds: int = int(info.get("idle_seconds", 0))
        self.session_id: Optional[int] = int(info["session_id"]) if info.get("session_id") is not None else None
        self.is_background_session: bool = bool(info.get("session_background", False)) or (not bool(info.get("thumbnail")))
        raw_win = info.get("active_window", "Escritorio")
        if self.is_background_session and raw_win in ("Escritorio", ""):
            self.active_window: str = "Sesión en 2º plano (Cambio de usuario)"
        else:
            self.active_window: str = raw_win
        self.connected_at: float = time.time()
        self.last_seen: float = time.time()
        self.thumbnail_b64: Optional[str] = info.get("thumbnail") or None
        self.stream_quality: int = 65
        self.stream_fps: int = 18

    @property
    def activity_status(self) -> str:
        if self.idle_seconds < 180:
            return "active"
        elif self.idle_seconds < 600:
            return "idle"
        return "away"

    def to_dict(self, include_thumbnail: bool = True) -> dict:
        data = {
            "agent_id": self.agent_id,
            "alias": self.alias,
            "hostname": self.hostname,
            "username": self.username,
            "os": self.os_name,
            "local_ip": self.local_ip,
            "tenant_id": self.tenant_id,
            "tenant_name": self.tenant_name,
            "monitors": self.monitors,
            "current_monitor": self.current_monitor,
            "idle_seconds": self.idle_seconds,
            "activity_status": self.activity_status,
            "active_window": self.active_window,
            "session_id": self.session_id,
            "is_background_session": self.is_background_session,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "stream_quality": self.stream_quality,
            "stream_fps": self.stream_fps,
        }
        if include_thumbnail and self.thumbnail_b64:
            data["thumbnail"] = self.thumbnail_b64
        return data

