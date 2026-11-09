import time
from typing import Dict, List, Optional
from fastapi import WebSocket
from pydantic import BaseModel


class AliasUpdateRequest(BaseModel):
    alias: str


class AgentConnection:
    """Representa una PC conectada activamente al Hub Central."""

    def __init__(self, agent_id: str, websocket: WebSocket, info: dict, saved_alias: Optional[str] = None):
        self.agent_id: str = agent_id
        self.websocket: WebSocket = websocket
        self.hostname: str = info.get("hostname", "PC-Desconocida")
        self.username: str = info.get("username", "usuario")
        self.alias: str = saved_alias or info.get("alias") or f"{self.hostname} ({self.username})"
        self.os_name: str = info.get("os", "Windows")
        self.local_ip: str = info.get("local_ip", "")
        self.monitors: List[dict] = info.get("monitors", [])
        self.current_monitor: int = int(info.get("current_monitor", 1))
        self.idle_seconds: int = int(info.get("idle_seconds", 0))
        self.active_window: str = info.get("active_window", "Escritorio")
        self.connected_at: float = time.time()
        self.last_seen: float = time.time()
        self.thumbnail_b64: Optional[str] = info.get("thumbnail")
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
            "monitors": self.monitors,
            "current_monitor": self.current_monitor,
            "idle_seconds": self.idle_seconds,
            "activity_status": self.activity_status,
            "active_window": self.active_window,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "stream_quality": self.stream_quality,
            "stream_fps": self.stream_fps,
        }
        if include_thumbnail and self.thumbnail_b64:
            data["thumbnail"] = self.thumbnail_b64
        return data

