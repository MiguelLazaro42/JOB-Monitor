import argparse
import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import re
import socket
import subprocess
import threading
import time
import uuid
import webbrowser
import zipfile
import zlib
from typing import Dict, List, Optional, Set

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
from database import AVAILABLE_TIMEZONES, MASTER_TENANT_ID, db, intel_get_status, intel_start
from service_catalog import classify_packet_profile, infer_site_from_window_title, is_browser_name
from models import (
    AgentConnection,
    AliasUpdateRequest,
    ChatSendRequest,
    ClipboardSetRequest,
    DeviceTenantAssignRequest,
    UserCreateRequest,
    UserUpdateRequest,
)

CAPTURES_DIR = config.DATA_DIR / "captures"
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

# Clave Asimétrica RSA-2048 del Servidor para Descifrado Exclusivo de Archivos Offline (.sitai)
# El agente cliente solo posee (SITAI_RSA_N, SITAI_RSA_E); únicamente el servidor posee SITAI_RSA_D.
SITAI_MAGIC_HEADER = b"SITAI01\x00"
SITAI_RSA_N = int(
    "e4ceaf9ce8fb52cd99b3ca633b3568fcaea40a7244e40c7f4335671b915fa729"
    "e9f9abd6245259dee6449aad5203b07f367990377dee3e851107f60e178bae1f"
    "79d90c2293d114f0fed232ccd9c9b484632b07190e47126ab67cc9651a208bdb"
    "ed8bbff87895408024b25b05b1050a946562398089f70074d498b4423f1aa9a2"
    "013fdbe2154a7342d69a751791a5cafbd8b753cdcda1ecd711bad96cf5aca997"
    "11848e6a6e6f4d16fc04be3b627aeffc9f12ed3e4ad3452f465d7a86a43d1785"
    "696d82ef3b1b1891b1c565cf680a69aa0a8b641206407cf555dbe34b3109aad4"
    "8eca8dfe0939acd2b68dbd82d28b63f4ccb9a975eec949242d61fa43ce3514f5",
    16,
)
SITAI_RSA_D = int(
    "81e2be98113dd2247a933059a25bd06c586b30aeeb6295a18cb27d048e42c16f"
    "2eae1798e9678232e17ea25baf49fee048393bdff471d135d294355e8c893360"
    "14d85c662889311d195a9391ec33ef7093a6f2518be456951360c526ca6d1d57"
    "cc825f889a53079976bb74a64775f82a835efe0b74c62d9124ba46e1b056df61"
    "cfc977708fcd1c77035225cc5c9a07f9a9c47c6e55ecd1d257474e47dc94c33e"
    "57e30b06d7adad20580f40c55e3a8575d4d4d0555f255a0a99de1345eda21bb3"
    "5da0b0207c73ec0b0fdfbadc79a2c3f46cf65dacc7bbd0aeaa68b851e32c1483"
    "5dc74aee1da88e3a0e9c2da3d09a3b7651ccbace2ca1e79ae4c3a896a1aa14a1",
    16,
)


def _sitai_keystream_xor(data: bytes, enc_key: bytes, nonce: bytes) -> bytes:
    out = bytearray(len(data))
    prefix = enc_key + nonce
    mv = memoryview(data)
    for i in range(0, len(data), 32):
        blk = hashlib.sha256(prefix + (i >> 5).to_bytes(4, "big")).digest()
        chunk = mv[i : i + 32]
        clen = len(chunk)
        out[i : i + clen] = (
            int.from_bytes(chunk, "big") ^ int.from_bytes(blk[:clen], "big")
        ).to_bytes(clen, "big")
    return bytes(out)


def decrypt_sitai_packet(raw_bytes: bytes) -> dict:
    """
    Descifra un contenedor binario .sitai usando la llave privada RSA-2048 exclusiva del servidor.
    """
    if len(raw_bytes) < 312 or raw_bytes[:8] != SITAI_MAGIC_HEADER:
        raise ValueError("Formato de contenedor .sitai invalido")
    rsa_cipher = raw_bytes[8:264]
    nonce = raw_bytes[264:280]
    mac_tag = raw_bytes[280:312]
    ciphertext = raw_bytes[312:]

    c_int = int.from_bytes(rsa_cipher, "big")
    m_int = pow(c_int, SITAI_RSA_D, SITAI_RSA_N)
    padded = m_int.to_bytes(256, "big")
    if padded[0] != 0x00 or padded[1] != 0x02:
        raise ValueError("Fallo de descifrado asimetrico RSA-2048 en .sitai")
    sep_idx = padded.find(b"\x00", 2)
    if sep_idx < 10 or (256 - sep_idx - 1) != 32:
        raise ValueError("Estructura de semilla efimera invalida en .sitai")
    master_seed = padded[sep_idx + 1 :]

    enc_key = hashlib.sha256(master_seed + b":sitai-enc:" + nonce).digest()
    mac_key = hashlib.sha256(master_seed + b":sitai-mac:" + nonce).digest()
    expected_mac = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac_tag, expected_mac):
        raise ValueError("Firma de integridad HMAC-SHA256 invalida en archivo .sitai")

    compressed = _sitai_keystream_xor(ciphertext, enc_key, nonce)
    plain_json = zlib.decompress(compressed)
    return json.loads(plain_json.decode("utf-8"))


app = FastAPI(title="JOB-Monitor — Centro de Monitoreo y Control Corporativo")
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))


def create_session_token(user: dict) -> str:
    payload_str = json.dumps(
        {
            "uid": user["id"],
            "usr": user["username"],
            "rol": user["role"],
            "tid": user["tenant_id"],
            "iat": int(time.time()),
        },
        separators=(",", ":"),
    )
    b64 = base64.urlsafe_b64encode(payload_str.encode("utf-8")).decode("ascii").rstrip("=")
    secret = f"{config.ADMIN_PASSWORD}:{config.AGENT_TOKEN}".encode("utf-8")
    sig = hmac.new(secret, b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"st1.{b64}.{sig}"


def resolve_user_from_token(supplied: str) -> Optional[dict]:
    token = (supplied or "").strip()
    if not token:
        return None
    if token.startswith("st1.") and token.count(".") == 2:
        try:
            _, b64, sig = token.split(".", 2)
            secret = f"{config.ADMIN_PASSWORD}:{config.AGENT_TOKEN}".encode("utf-8")
            expected = hmac.new(secret, b64.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return None
            padded = b64 + "=" * (-len(b64) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
            user = db.get_user_by_id(int(data["uid"]))
            if user and user.get("is_active"):
                return user
        except Exception:
            return None
    if token == config.ADMIN_PASSWORD:
        return db.get_user_by_username(config.ADMIN_USERNAME)
    return None


class HubManager:
    def __init__(self):
        self.agents: Dict[str, AgentConnection] = {}
        self.admins: Set[WebSocket] = set()
        self.admin_users: Dict[WebSocket, dict] = {}
        self.subscriptions: Dict[WebSocket, Optional[str]] = {}
        self.shell_sessions: Dict[str, tuple[WebSocket, str]] = {}
        self.fs_requests: Dict[str, tuple[WebSocket, str]] = {}
        self._last_connect_log: Dict[str, float] = {}
        self._last_disconnect_log: Dict[str, float] = {}

    def can_user_access_tenant(self, user: Optional[dict], tenant_id: str) -> bool:
        if not user:
            return False
        if user.get("role") == "superadmin":
            return True
        return user.get("tenant_id") == (tenant_id or MASTER_TENANT_ID)

    def can_user_access_agent(self, user: Optional[dict], agent_id: str) -> bool:
        if not user:
            return False
        if user.get("role") == "superadmin":
            return True
        ag = self.agents.get(agent_id)
        tid = ag.tenant_id if ag else (db.get_device_tenant(agent_id) or MASTER_TENANT_ID)
        return user.get("tenant_id") == tid

    async def _is_agent_ws_alive(self, conn: AgentConnection) -> bool:
        if time.time() - conn.last_seen > 16.0:
            return False
        try:
            await asyncio.wait_for(conn.websocket.send_json({"type": "ping"}), timeout=1.2)
            return True
        except Exception:
            return False

    async def resolve_effective_agent_id(self, raw_agent_id: str, ws: WebSocket, info: dict) -> str:
        """
        Desambiguación automática multi-sesión (Windows Fast User Switching / Multi-usuario):
        Si dos sesiones de usuario en la misma PC comparten el mismo config_agent.json
        (mismo raw_agent_id), detecta que la primera conexión sigue activa y asigna a la
        segunda sesión el identificador derivado '{raw_agent_id}-S2', evitando desconexiones en bucle.
        """
        old = self.agents.get(raw_agent_id)
        if not old or old.websocket == ws:
            return raw_agent_id

        if not await self._is_agent_ws_alive(old):
            return raw_agent_id

        for slot_idx in range(2, 10):
            candidate_id = f"{raw_agent_id}-S{slot_idx}"
            slot_conn = self.agents.get(candidate_id)
            if not slot_conn or slot_conn.websocket == ws:
                return candidate_id
            if not await self._is_agent_ws_alive(slot_conn):
                return candidate_id

        return f"{raw_agent_id}-S2"

    async def register_agent(self, agent_id: str, ws: WebSocket, info: dict) -> AgentConnection:
        raw_agent_id = str(info.get("agent_id") or agent_id)
        old = self.agents.get(agent_id)
        if old and old.websocket != ws:
            try:
                await old.websocket.close()
            except Exception:
                pass

        saved_alias = db.get_saved_alias(agent_id)
        reported_alias = str(info.get("alias") or "").strip()
        fallback_alias = reported_alias or f"{info.get('hostname', 'PC')} ({info.get('username', 'usuario')})"
        if not saved_alias and agent_id != raw_agent_id:
            primary_alias = db.get_saved_alias(raw_agent_id) or ""
            if reported_alias and reported_alias != primary_alias:
                fallback_alias = reported_alias
            else:
                base_lbl = primary_alias or fallback_alias
                suffix_num = agent_id.rsplit("-S", 1)[-1] if "-S" in agent_id else "2"
                fallback_alias = f"{base_lbl} (Sesión {suffix_num})"

        reported_tid = str(info.get("tenant_id") or "").strip()
        if not reported_tid and agent_id != raw_agent_id:
            reported_tid = db.get_device_tenant(raw_agent_id) or ""

        final_tid = db.upsert_device(
            agent_id=agent_id,
            alias=saved_alias or fallback_alias,
            hostname=info.get("hostname", "PC-Desconocida"),
            username=info.get("username", "usuario"),
            os_version=info.get("os", "Windows"),
            local_ip=info.get("local_ip", ""),
            is_online=True,
            tenant_id=reported_tid,
        )
        tenant_names = db.get_tenant_name_map()
        conn = AgentConnection(
            agent_id,
            ws,
            info,
            saved_alias=saved_alias or fallback_alias,
            tenant_id=final_tid,
            tenant_name=tenant_names.get(final_tid, final_tid),
        )
        self.agents[agent_id] = conn

        if agent_id != raw_agent_id:
            await self.send_to_agent(
                agent_id,
                {
                    "type": "assign_agent_id",
                    "agent_id": agent_id,
                    "alias": conn.alias,
                },
            )

        # Throttling de logs de conexión idénticos para evitar saturar la bitácora
        now_ts = time.time()
        last_conn_log = self._last_connect_log.get(agent_id, 0)
        if (now_ts - last_conn_log) > 15.0:
            self._last_connect_log[agent_id] = now_ts
            mon_count = max(1, len([m for m in conn.monitors if m.get("id", 0) > 0]))
            log_entry = db.add_log(
                agent_id=agent_id,
                alias=conn.alias,
                hostname=conn.hostname,
                username=conn.username,
                category="NETWORK",
                message=f"Agente inició sesión correctamente ({conn.os_name} • IP {conn.local_ip} • {mon_count} monitor{'es' if mon_count > 1 else ''})",
            )
            await self.broadcast_new_log(log_entry, tenant_id=conn.tenant_id)

        await self.broadcast_agents_list()

        if self.has_viewers(agent_id):
            await self.send_to_agent(agent_id, {"type": "start_stream"})
        return conn

    async def unregister_agent(self, agent_id: str, ws: WebSocket):
        current = self.agents.get(agent_id)
        if current and current.websocket == ws:
            tid = current.tenant_id
            del self.agents[agent_id]
            db.set_device_offline(agent_id)
            now_ts = time.time()
            last_disc_log = self._last_disconnect_log.get(agent_id, 0)
            if (now_ts - last_disc_log) > 15.0:
                self._last_disconnect_log[agent_id] = now_ts
                log_entry = db.add_log(
                    agent_id=agent_id,
                    alias=current.alias,
                    hostname=current.hostname,
                    username=current.username,
                    category="NETWORK",
                    message="El equipo se desconectó del servidor central",
                )
                await self.broadcast_new_log(log_entry, tenant_id=tid)
            await self.broadcast_agents_list()
            await self.broadcast_to_viewers(
                agent_id,
                {"type": "agent_disconnected", "agent_id": agent_id},
            )

    def has_viewers(self, agent_id: str) -> bool:
        return any(sub_id == agent_id for sub_id in self.subscriptions.values())

    async def set_admin_subscription(self, admin_ws: WebSocket, new_agent_id: Optional[str]):
        user = self.admin_users.get(admin_ws)
        if new_agent_id and not self.can_user_access_agent(user, new_agent_id):
            return

        old_agent_id = self.subscriptions.get(admin_ws)
        self.subscriptions[admin_ws] = new_agent_id

        if old_agent_id and old_agent_id != new_agent_id:
            if not self.has_viewers(old_agent_id):
                await self.send_to_agent(old_agent_id, {"type": "stop_stream"})

        if new_agent_id and new_agent_id in self.agents:
            agent = self.agents[new_agent_id]
            await admin_ws.send_json({
                "type": "agent_details",
                "agent": agent.to_dict(include_thumbnail=True),
            })
            await self.send_to_agent(new_agent_id, {"type": "start_stream"})

            who_lbl = user["display_name"] if user else "Administrador"
            log_entry = db.add_log(
                agent_id=agent.agent_id,
                alias=agent.alias,
                hostname=agent.hostname,
                username=agent.username,
                category="AUDIT",
                message=f"{who_lbl} inició sesión de visualización silenciosa",
            )
            await self.broadcast_new_log(log_entry, tenant_id=agent.tenant_id)

    async def update_alias(self, agent_id: str, new_alias: str):
        alias_clean = new_alias.strip()
        if not alias_clean:
            return
        db.update_device_alias(agent_id, alias_clean)
        agent = self.agents.get(agent_id)
        if agent:
            old_alias = agent.alias
            agent.alias = alias_clean
            await self.send_to_agent(agent_id, {"type": "update_alias", "alias": alias_clean})
            log_entry = db.add_log(
                agent_id=agent.agent_id,
                alias=agent.alias,
                hostname=agent.hostname,
                username=agent.username,
                category="AUDIT",
                message=f"Alias del equipo actualizado de '{old_alias}' a '{alias_clean}'",
            )
            await self.broadcast_new_log(log_entry, tenant_id=agent.tenant_id)
        await self.broadcast_agents_list()

    async def update_device_tenant(self, agent_id: str, new_tenant_id: str):
        clean_tid = (new_tenant_id or MASTER_TENANT_ID).strip()
        db.set_device_tenant(agent_id, clean_tid)
        tenant_names = db.get_tenant_name_map()
        agent = self.agents.get(agent_id)
        if agent:
            agent.tenant_id = clean_tid
            agent.tenant_name = tenant_names.get(clean_tid, clean_tid)
        await self.broadcast_agents_list()

    async def send_to_agent(self, agent_id: str, payload: dict) -> bool:
        agent = self.agents.get(agent_id)
        if not agent:
            return False
        try:
            await agent.websocket.send_json(payload)
            return True
        except Exception:
            return False

    def _build_agents_payload(self, user: Optional[dict] = None) -> dict:
        is_super = not user or user.get("role") == "superadmin"
        allowed_tid = None if is_super else user.get("tenant_id")
        tenant_names = db.get_tenant_name_map()

        for ag in self.agents.values():
            ag.tenant_name = tenant_names.get(ag.tenant_id, ag.tenant_id)

        visible_agents = [
            a.to_dict(include_thumbnail=True)
            for a in self.agents.values()
            if (is_super or a.tenant_id == allowed_tid)
        ]
        known_devices = db.get_all_known_devices(allowed_tenant_id=allowed_tid)
        online_ids = {a["agent_id"] for a in visible_agents}
        offline_count = sum(1 for d in known_devices if d["agent_id"] not in online_ids)

        payload = {
            "type": "agents_list",
            "agents": visible_agents,
            "known_devices": known_devices,
            "offline_count": offline_count,
            "timezone": db.get_timezone(),
            "available_timezones": AVAILABLE_TIMEZONES,
            "tenants": db.list_tenants() if is_super else [],
        }
        if user:
            payload["user"] = user
        return payload

    async def broadcast_agents_list(self):
        dead = []
        for ws in list(self.admins):
            try:
                user = self.admin_users.get(ws)
                payload = self._build_agents_payload(user=user)
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.remove_admin(ws)

    async def broadcast_telemetry_update(self, agent: AgentConnection):
        payload = {
            "type": "agent_telemetry",
            "agent": agent.to_dict(include_thumbnail=True),
            "rx_kbps": agent.rx_kbps,
            "tx_kbps": agent.tx_kbps,
            "today_rx_bytes": agent.today_rx_bytes,
            "today_tx_bytes": agent.today_tx_bytes,
            "recent_domains": agent.recent_domains,
        }
        dead = []
        for ws in list(self.admins):
            user = self.admin_users.get(ws)
            if not self.can_user_access_tenant(user, agent.tenant_id):
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.remove_admin(ws)

    async def broadcast_new_log(self, log_entry: dict, tenant_id: Optional[str] = None):
        payload = {
            "type": "new_log",
            "log": log_entry,
        }
        await self.broadcast_to_admins(payload, target_tenant_id=tenant_id)

    async def broadcast_to_admins(self, payload: dict, target_tenant_id: Optional[str] = None):
        dead = []
        for ws in list(self.admins):
            user = self.admin_users.get(ws)
            if target_tenant_id and not self.can_user_access_tenant(user, target_tenant_id):
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.remove_admin(ws)

    async def broadcast_to_viewers(self, agent_id: str, payload: dict):
        dead = []
        for ws, sub_id in list(self.subscriptions.items()):
            if sub_id == agent_id:
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
        for ws in dead:
            await self.remove_admin(ws)

    async def forward_frame_to_viewers(self, agent_id: str, frame_bytes: bytes):
        dead = []
        for ws, sub_id in list(self.subscriptions.items()):
            if sub_id == agent_id:
                try:
                    await ws.send_bytes(frame_bytes)
                except Exception:
                    dead.append(ws)
        for ws in dead:
            await self.remove_admin(ws)

    async def remove_admin(self, ws: WebSocket):
        self.admins.discard(ws)
        self.admin_users.pop(ws, None)
        old_agent_id = self.subscriptions.pop(ws, None)
        if old_agent_id and not self.has_viewers(old_agent_id):
            await self.send_to_agent(old_agent_id, {"type": "stop_stream"})
        for skey, (owner_ws, ag_id) in list(self.shell_sessions.items()):
            if owner_ws == ws:
                self.shell_sessions.pop(skey, None)
                await self.send_to_agent(ag_id, {"type": "shell_close", "session_key": skey})
        for rkey, (owner_ws, _) in list(self.fs_requests.items()):
            if owner_ws == ws:
                self.fs_requests.pop(rkey, None)


hub = HubManager()


def get_local_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "lan_ip": get_local_lan_ip(),
            "port": config.PORT,
        },
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "phase": 4,
        "agents_online": len(hub.agents),
        "lan_ip": get_local_lan_ip(),
        "port": config.PORT,
    }


def verify_admin_access(request: Request) -> dict:
    """
    Micro-Función M1: Protege los endpoints HTTP REST mediante token de sesión firmado (st1....)
    o clave maestra, devolviendo el perfil completo del usuario autenticado y su tenant_id.
    """
    supplied = (
        request.headers.get("x-admin-password")
        or request.query_params.get("admin_pass")
        or request.cookies.get("ra_admin_pass")
        or ""
    )
    user = resolve_user_from_token(supplied)
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado: sesión o credenciales inválidas")
    return user


@app.post("/api/login")
async def admin_login(request: Request, response: Response):
    """Valida usuario y contraseña (Super Admin, Usuario Principal o Sub-usuario) y devuelve token de sesión."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    user = db.authenticate_user(username, password)
    if not user:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    session_token = create_session_token(user)
    tenant_names = db.get_tenant_name_map()
    user["tenant_name"] = tenant_names.get(user["tenant_id"], user["tenant_id"])
    response.set_cookie(
        key="ra_admin_pass",
        value=session_token,
        max_age=86400 * 30,
        httponly=False,
        samesite="lax",
    )
    return {
        "ok": True,
        "username": user["username"],
        "token": session_token,
        "user": user,
        "timezone": db.get_timezone(),
        "tenants": db.list_tenants() if user["role"] == "superadmin" else [],
    }


@app.get("/api/me")
async def get_current_user_profile(request: Request):
    user = verify_admin_access(request)
    tenant_names = db.get_tenant_name_map()
    user["tenant_name"] = tenant_names.get(user["tenant_id"], user["tenant_id"])
    return {
        "ok": True,
        "user": user,
        "timezone": db.get_timezone(),
        "tenants": db.list_tenants() if user["role"] == "superadmin" else [],
    }


@app.get("/api/users")
async def list_users_endpoint(request: Request):
    user = verify_admin_access(request)
    role = user["role"]
    tid = user["tenant_id"]
    users_list = db.list_users(requester_role=role, requester_tenant_id=tid)
    tenants_list = db.list_tenants() if role == "superadmin" else [
        {
            "tenant_id": tid,
            "name": db.get_tenant_name_map().get(tid, user["display_name"]),
            "owner_username": user["username"],
            "devices_count": len(db.get_all_known_devices(allowed_tenant_id=tid)),
        }
    ]
    devices_list = db.get_all_known_devices(allowed_tenant_id=None if role == "superadmin" else tid)
    return {
        "ok": True,
        "users": users_list,
        "tenants": tenants_list,
        "devices": devices_list,
    }


@app.post("/api/users")
async def create_user_endpoint(request: Request, body: UserCreateRequest):
    requester = verify_admin_access(request)
    req_role = requester["role"]
    if req_role not in ("superadmin", "tenant_admin"):
        raise HTTPException(status_code=403, detail="No tienes permisos para crear usuarios")

    target_role = (body.role or "sub_user").strip()
    if req_role == "tenant_admin":
        # Un Usuario Principal solo puede crear sub-usuarios dentro de su propio tenant
        target_role = "sub_user"
        target_tenant_id = requester["tenant_id"]
    else:
        # Super Admin puede crear Usuarios Principales (nuevo tenant) o Sub-usuarios en cualquier tenant
        if target_role not in ("tenant_admin", "sub_user"):
            target_role = "tenant_admin"
        target_tenant_id = body.tenant_id if target_role == "sub_user" else None

    try:
        created = db.create_user(
            username=body.username,
            password=body.password,
            display_name=body.display_name,
            role=target_role,
            tenant_id=target_tenant_id,
            parent_user_id=requester["id"],
            can_control=body.can_control,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await hub.broadcast_agents_list()
    return {"ok": True, "user": created}


@app.put("/api/users/{user_id}")
async def update_user_endpoint(request: Request, user_id: int, body: UserUpdateRequest):
    requester = verify_admin_access(request)
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    req_role = requester["role"]
    if req_role == "sub_user":
        if requester["id"] != user_id:
            raise HTTPException(status_code=403, detail="Solo puedes actualizar tu propia contraseña")
        updated = db.update_user(user_id=user_id, password=body.password, display_name=body.display_name)
        return {"ok": True, "user": updated}

    if req_role == "tenant_admin":
        if target["tenant_id"] != requester["tenant_id"] or target["role"] == "superadmin":
            raise HTTPException(status_code=403, detail="No tienes permisos sobre este usuario")
        updated = db.update_user(
            user_id=user_id,
            display_name=body.display_name,
            password=body.password,
            can_control=body.can_control if target["id"] != requester["id"] else True,
            is_active=body.is_active if target["id"] != requester["id"] else True,
        )
        await hub.broadcast_agents_list()
        return {"ok": True, "user": updated}

    # Super Admin
    updated = db.update_user(
        user_id=user_id,
        display_name=body.display_name,
        password=body.password,
        can_control=body.can_control,
        is_active=body.is_active,
        tenant_id=body.tenant_id,
    )
    await hub.broadcast_agents_list()
    return {"ok": True, "user": updated}


@app.delete("/api/users/{user_id}")
async def delete_user_endpoint(request: Request, user_id: int):
    requester = verify_admin_access(request)
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if target["role"] == "superadmin" or target["id"] == requester["id"]:
        raise HTTPException(status_code=400, detail="No puedes eliminar al Super Admin ni tu propia sesión activa")

    if requester["role"] == "tenant_admin":
        if target["tenant_id"] != requester["tenant_id"] or target["role"] != "sub_user":
            raise HTTPException(status_code=403, detail="Solo puedes eliminar sub-usuarios de tu propia cuenta")
    elif requester["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="No tienes permisos para eliminar usuarios")

    db.delete_user(user_id)
    await hub.broadcast_agents_list()
    return {"ok": True, "deleted_id": user_id}


@app.post("/api/devices/{agent_id}/tenant")
async def assign_device_tenant(request: Request, agent_id: str, body: DeviceTenantAssignRequest):
    requester = verify_admin_access(request)
    if requester["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="Solo el Super Admin puede reasignar equipos entre cuentas")
    await hub.update_device_tenant(agent_id, body.tenant_id)
    return {"ok": True, "agent_id": agent_id, "tenant_id": body.tenant_id}


@app.delete("/api/devices/{agent_id}")
async def remove_device_endpoint(
    request: Request,
    agent_id: str,
    self_destruct: bool = Query(True, description="Enviar orden de autodestrucción y revocar agente"),
):
    requester = verify_admin_access(request)
    if not hub.can_user_access_agent(requester, agent_id):
        raise HTTPException(status_code=403, detail="No tienes acceso a este equipo")
    if requester["role"] == "sub_user":
        raise HTTPException(status_code=403, detail="Los sub-usuarios no pueden eliminar o desvincular equipos")

    live_agent = hub.agents.pop(agent_id, None)
    tid = live_agent.tenant_id if live_agent else (db.get_device_tenant(agent_id) or MASTER_TENANT_ID)
    alias_lbl = live_agent.alias if live_agent else agent_id

    if live_agent:
        if self_destruct:
            try:
                await live_agent.websocket.send_json({"type": "self_destruct"})
            except Exception:
                pass
        try:
            await live_agent.websocket.close(code=1008)
        except Exception:
            pass
        await hub.broadcast_to_viewers(
            agent_id,
            {"type": "agent_disconnected", "agent_id": agent_id},
        )

    db.delete_device(agent_id, revoke=bool(self_destruct))
    try:
        import shutil
        agent_cap_dir = CAPTURES_DIR / os.path.basename(agent_id)
        if agent_cap_dir.exists() and agent_cap_dir.is_dir():
            shutil.rmtree(agent_cap_dir, ignore_errors=True)
    except Exception:
        pass
    mode_lbl = "con orden de autodestrucción y revocación" if self_destruct else "desvinculado del panel"
    log_entry = db.add_log(
        agent_id="SYSTEM-SERVER",
        alias="Consola de Administración",
        hostname="Servidor Central",
        username=requester["username"],
        category="AUDIT",
        message=f"{requester['display_name']} eliminó el equipo '{alias_lbl}' ({mode_lbl})",
    )
    await hub.broadcast_new_log(log_entry, tenant_id=tid)
    await hub.broadcast_agents_list()
    return {"ok": True, "deleted_agent_id": agent_id, "self_destruct": bool(self_destruct)}


@app.get("/api/settings/timezone")
async def get_timezone_settings(request: Request):
    verify_admin_access(request)
    return {
        "ok": True,
        "timezone": db.get_timezone(),
        "available_timezones": AVAILABLE_TIMEZONES,
    }


@app.post("/api/settings/timezone")
async def update_timezone_settings(request: Request):
    requester = verify_admin_access(request)
    if requester["role"] == "sub_user":
        raise HTTPException(status_code=403, detail="Solo administradores pueden cambiar la zona horaria")
    try:
        body = await request.json()
    except Exception:
        body = {}
    tz_name = str(body.get("timezone") or "America/Merida").strip()
    try:
        saved_tz = db.set_timezone(tz_name)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Zona horaria inválida: {tz_name}")

    await hub.broadcast_to_admins({
        "type": "timezone_updated",
        "timezone": saved_tz,
    })
    return {
        "ok": True,
        "timezone": saved_tz,
    }


# ==============================================================================
# FASE 4 - BLOQUE A: MINI-FUNCIONES DE EMPAQUETADO DINÁMICO DEL AGENTE (.ZIP)
# ==============================================================================
def resolve_default_ws_url(request: Request) -> str:
    """Detecta la URL WebSocket ideal para las PCs cliente según el host o IP LAN."""
    lan_ip = get_local_lan_ip()
    host_header = request.headers.get("host", f"{lan_ip}:{config.PORT}")
    if host_header.startswith("127.0.0.1") or host_header.startswith("localhost"):
        host_header = f"{lan_ip}:{config.PORT}"
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    ws_scheme = "wss" if forwarded_proto == "https" else "ws"
    return f"{ws_scheme}://{host_header}/ws/agent"


def normalize_ws_url(raw_url: str, fallback_url: str) -> str:
    """Normaliza cualquier URL o IP ingresada por el administrador a ws(s)://.../ws/agent."""
    val = (raw_url or "").strip()
    if not val:
        return fallback_url
    if val.startswith("http://"):
        val = "ws://" + val[len("http://"):]
    elif val.startswith("https://"):
        val = "wss://" + val[len("https://"):]
    elif not val.startswith("ws://") and not val.startswith("wss://"):
        val = "ws://" + val
    val = val.rstrip("/")
    if not val.endswith("/ws/agent"):
        val = val + "/ws/agent"
    return val


def ensure_mtls_certificates() -> tuple[Optional[bytes], Optional[bytes]]:
    """
    Micro-Función S1: Asegura la existencia de la Autoridad Certificadora Corporativa (SITAI Root CA)
    y del Certificado X.509 de Cliente (agent_client.crt / agent_client.key) en data/certs/.
    Nunca se suben a Git; se empaquetan dinámicamente dentro del .ZIP de '+ Agente'.
    """
    ca_key = config.CERTS_DIR / "sitai_ca.key"
    ca_crt = config.CERTS_DIR / "sitai_ca.crt"
    cli_key = config.CERTS_DIR / "agent_client.key"
    cli_crt = config.CERTS_DIR / "agent_client.crt"
    cli_csr = config.CERTS_DIR / "agent_client.csr"
    ext_cfg = config.CERTS_DIR / "client_ext.cnf"

    try:
        if not (ca_key.exists() and ca_crt.exists()):
            subprocess.run(
                [
                    "openssl", "req", "-x509", "-newkey", "rsa:4096", "-nodes",
                    "-keyout", str(ca_key),
                    "-out", str(ca_crt),
                    "-days", "3650",
                    "-subj", "/C=MX/ST=Yucatan/L=Merida/O=SITAI.MX/OU=Security/CN=SITAI.MX JOB-Monitor Root CA",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            os.chmod(ca_key, 0o600)

        if not (cli_key.exists() and cli_crt.exists()):
            ext_cfg.write_text(
                "basicConstraints = CA:FALSE\n"
                "keyUsage = digitalSignature, keyEncipherment\n"
                "extendedKeyUsage = clientAuth\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "openssl", "req", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(cli_key),
                    "-out", str(cli_csr),
                    "-subj", "/C=MX/ST=Yucatan/L=Merida/O=SITAI.MX/OU=Agents/CN=SITAI-Authorized-Agent",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                [
                    "openssl", "x509", "-req",
                    "-in", str(cli_csr),
                    "-CA", str(ca_crt),
                    "-CAkey", str(ca_key),
                    "-CAcreateserial",
                    "-out", str(cli_crt),
                    "-days", "3650",
                    "-sha256",
                    "-extfile", str(ext_cfg),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            os.chmod(cli_key, 0o600)
            if cli_csr.exists():
                cli_csr.unlink()

        return cli_crt.read_bytes(), cli_key.read_bytes()
    except Exception:
        if cli_crt.exists() and cli_key.exists():
            return cli_crt.read_bytes(), cli_key.read_bytes()
        return None, None


def generate_agent_config_payload(
    server_url: str,
    alias: str = "",
    token: str = "",
    tenant_id: str = "",
) -> dict:
    """
    Mini-Función A1: Genera el diccionario de configuración para config_agent.json.
    Omite agent_id para que cada PC Windows/Mac calcule su propio UUID único de hardware al iniciar.
    Incluye referencias al par de llaves X.509 mTLS y el tenant_id del propietario.
    """
    payload = {
        "server_url": server_url,
        "token": (token or config.AGENT_TOKEN).strip(),
        "mtls_cert": "agent_client.crt",
        "mtls_key": "agent_client.key",
    }
    clean_alias = (alias or "").strip()
    if clean_alias:
        payload["alias"] = clean_alias
    clean_tid = (tenant_id or "").strip()
    if clean_tid:
        payload["tenant_id"] = clean_tid
    return payload


def generate_install_readme(server_url: str, alias: str = "") -> str:
    """Genera las instrucciones de instalación incluidas dentro del paquete .zip."""
    alias_line = alias.strip() if alias.strip() else "(Automático según nombre de usuario y equipo)"
    return (
        "====================================================================\r\n"
        "  JOB-Monitor — Paquete Instalador de Agente (Windows & macOS)\r\n"
        "====================================================================\r\n\r\n"
        f"Servidor configurado : {server_url}\r\n"
        f"Alias preconfigurado : {alias_line}\r\n\r\n"
        "--------------------------------------------------------------------\r\n"
        "OPCION A — INSTALACION EN WINDOWS (TODO EN UNO):\r\n"
        "--------------------------------------------------------------------\r\n"
        "1. Extrae este ZIP y haz doble clic en 'install_windows.bat'.\r\n"
        "2. Si el equipo no tiene Python, el instalador descarga e instala\r\n"
        "   automaticamente Python 3.12 oficial con su PATH configurado,\r\n"
        "   crea el entorno aislado e instala todas las dependencias.\r\n"
        "3. Configura en C:\\ProgramData\\MonitoreoEmpresa con inicializacion\r\n"
        "   al encender el equipo y Cron en Windows (cada 1 minuto).\r\n"
        "- Para desinstalar en Windows: doble clic en 'uninstall_windows.bat'.\r\n\r\n"
        "--------------------------------------------------------------------\r\n"
        "OPCION B — INSTALACION EN MACOS:\r\n"
        "--------------------------------------------------------------------\r\n"
        "1. RECOMENDADO (sin bloqueo de Gatekeeper en macOS 15+):\r\n"
        "   Usa el botón 'Copiar comando para Mac' desde '+ Agente' en el panel web,\r\n"
        "   abre Terminal y pégalo con Cmd+V.\r\n"
        "2. O si descargaste este ZIP en Mac, ejecuta en Terminal dentro de la carpeta:\r\n"
        "   xattr -cr . && bash install_macos.command\r\n"
        "3. Instala /Applications/JOB-Monitor Agent.app, configura auto-inicio\r\n"
        "   al encender el Mac y solicita permisos de Pantalla/Audio y Accesibilidad.\r\n"
        "- Para desinstalar en macOS: ejecuta 'bash uninstall_macos.command'.\r\n"
    )


def resolve_public_http_base(request: Request) -> str:
    lan_ip = get_local_lan_ip()
    host_header = request.headers.get("host", f"{lan_ip}:{config.PORT}")
    if host_header.startswith("127.0.0.1") or host_header.startswith("localhost"):
        host_header = f"{lan_ip}:{config.PORT}"
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    http_scheme = "https" if forwarded_proto == "https" else "http"
    return f"{http_scheme}://{host_header}"


def create_macos_install_key(server_url: str, alias: str = "", tenant_id: str = "") -> str:
    data_str = json.dumps(
        {
            "u": server_url,
            "a": (alias or "").strip(),
            "t": (tenant_id or "").strip(),
            "exp": int(time.time()) + 86400 * 7,
        },
        separators=(",", ":"),
    )
    b64 = base64.urlsafe_b64encode(data_str.encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(config.AGENT_TOKEN.encode("utf-8"), b64.encode("ascii"), hashlib.sha256).hexdigest()[:24]
    return f"{b64}.{sig}"


def verify_macos_install_key(key_str: str) -> Optional[dict]:
    if not key_str or "." not in key_str:
        return None
    try:
        b64, sig = key_str.rsplit(".", 1)
        expected = hmac.new(config.AGENT_TOKEN.encode("utf-8"), b64.encode("ascii"), hashlib.sha256).hexdigest()[:24]
        if not hmac.compare_digest(sig, expected):
            return None
        pad = "=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(b64 + pad).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def build_agent_package_zip(server_url: str, alias: str = "", tenant_id: str = "") -> bytes:
    """
    Mini-Función A2: Empaqueta en memoria (ZIP) el agente, dependencias,
    config_agent.json preconfigurado (con tenant_id y mTLS) y scripts de instalación.
    """
    agent_dir = config.PROJECT_ROOT / "agent"
    cfg_payload = generate_agent_config_payload(server_url=server_url, alias=alias, tenant_id=tenant_id)
    cfg_json_str = json.dumps(cfg_payload, indent=2, ensure_ascii=False)
    readme_str = generate_install_readme(server_url=server_url, alias=alias)

    mem_zip = io.BytesIO()
    folder_prefix = "JOB-Monitor-Agent"

    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in (
            "agent.py",
            "requirements-agent.txt",
            "install_windows.bat",
            "uninstall_windows.bat",
            "install_macos.command",
            "uninstall_macos.command",
            "JOBMonitorAgent_macos",
            "AppIcon.icns",
        ):
            fpath = agent_dir / fname
            if fpath.exists():
                zinfo = zipfile.ZipInfo(f"{folder_prefix}/{fname}")
                zinfo.compress_type = zipfile.ZIP_DEFLATED
                zinfo.create_system = 3  # 3 = UNIX (Permite a macOS Archive Utility extraer con permisos ejecutables)
                if fname.endswith(".command") or fname.endswith(".py") or fname == "JOBMonitorAgent_macos":
                    zinfo.external_attr = 0o100755 << 16
                else:
                    zinfo.external_attr = 0o100644 << 16
                zf.writestr(zinfo, fpath.read_bytes())

        cfg_info = zipfile.ZipInfo(f"{folder_prefix}/config_agent.json")
        cfg_info.compress_type = zipfile.ZIP_DEFLATED
        cfg_info.create_system = 3
        cfg_info.external_attr = 0o100644 << 16
        zf.writestr(cfg_info, cfg_json_str)

        readme_info = zipfile.ZipInfo(f"{folder_prefix}/LEEME_INSTALACION.txt")
        readme_info.compress_type = zipfile.ZIP_DEFLATED
        readme_info.create_system = 3
        readme_info.external_attr = 0o100644 << 16
        zf.writestr(readme_info, readme_str)

        cli_crt_bytes, cli_key_bytes = ensure_mtls_certificates()
        if cli_crt_bytes and cli_key_bytes:
            crt_info = zipfile.ZipInfo(f"{folder_prefix}/agent_client.crt")
            crt_info.compress_type = zipfile.ZIP_DEFLATED
            crt_info.create_system = 3
            crt_info.external_attr = 0o100644 << 16
            zf.writestr(crt_info, cli_crt_bytes)

            key_info = zipfile.ZipInfo(f"{folder_prefix}/agent_client.key")
            key_info.compress_type = zipfile.ZIP_DEFLATED
            key_info.create_system = 3
            key_info.external_attr = 0o100600 << 16
            zf.writestr(key_info, cli_key_bytes)

    mem_zip.seek(0)
    return mem_zip.read()


@app.get("/api/agent-package-info")
async def get_agent_package_info(request: Request):
    """Devuelve la URL WebSocket sugerida y el comando directo macOS para prellenar el modal."""
    user = verify_admin_access(request)
    default_ws = resolve_default_ws_url(request)
    http_base = resolve_public_http_base(request)
    target_tid = MASTER_TENANT_ID if user["role"] == "superadmin" else user["tenant_id"]
    key = create_macos_install_key(server_url=default_ws, alias="", tenant_id=target_tid)
    return {
        "ok": True,
        "default_ws_url": default_ws,
        "lan_ip": get_local_lan_ip(),
        "port": config.PORT,
        "tenant_id": user["tenant_id"],
        "tenants": db.list_tenants() if user["role"] == "superadmin" else [],
        "macos_command": f'curl -fsSL "{http_base}/api/macos-setup.sh?k={key}" | bash',
    }


@app.get("/api/macos-install-command")
async def get_macos_install_command(
    request: Request,
    server_url: Optional[str] = Query(None),
    alias: Optional[str] = Query(""),
    tenant_id: Optional[str] = Query(None),
):
    """Genera un comando de 1 línea para Terminal en macOS libre de bloqueo Gatekeeper (com.apple.quarantine)."""
    user = verify_admin_access(request)
    default_ws = resolve_default_ws_url(request)
    final_ws_url = normalize_ws_url(server_url or "", default_ws)
    clean_alias = (alias or "").strip()
    target_tid = ((tenant_id or "").strip() or MASTER_TENANT_ID) if user["role"] == "superadmin" else user["tenant_id"]
    http_base = resolve_public_http_base(request)
    key = create_macos_install_key(server_url=final_ws_url, alias=clean_alias, tenant_id=target_tid)
    cmd = f'curl -fsSL "{http_base}/api/macos-setup.sh?k={key}" | bash'
    return {
        "ok": True,
        "command": cmd,
    }


@app.get("/api/macos-setup.sh")
async def serve_macos_setup_script(request: Request, k: str = Query(...)):
    """Sirve el script bootstrapper para macOS autenticado mediante token HMAC efímero."""
    payload = verify_macos_install_key(k)
    if not payload:
        raise HTTPException(status_code=403, detail="Enlace de instalacion expirado o invalido")
    http_base = resolve_public_http_base(request)
    pkg_url = f"{http_base}/api/macos-package.zip?k={k}"
    script = (
        "#!/usr/bin/env bash\n"
        "set -e\n"
        "TMP_DIR=\"$(mktemp -d /tmp/jobmonitor-setup.XXXXXX)\"\n"
        "cleanup() { rm -rf \"$TMP_DIR\"; }\n"
        "trap cleanup EXIT\n"
        "echo \"Descargando instalador nativo SITAI JOB-Monitor para macOS...\"\n"
        f"curl -fsSL \"{pkg_url}\" -o \"$TMP_DIR/pkg.zip\"\n"
        "unzip -q \"$TMP_DIR/pkg.zip\" -d \"$TMP_DIR\"\n"
        "chmod +x \"$TMP_DIR/JOB-Monitor-Agent/install_macos.command\"\n"
        "bash \"$TMP_DIR/JOB-Monitor-Agent/install_macos.command\"\n"
    )
    return Response(
        content=script,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/macos-package.zip")
async def serve_macos_package_zip(k: str = Query(...)):
    """Descarga directa por curl del paquete preconfigurado para macOS (sin com.apple.quarantine)."""
    payload = verify_macos_install_key(k)
    if not payload:
        raise HTTPException(status_code=403, detail="Enlace de instalacion expirado o invalido")
    zip_bytes = build_agent_package_zip(
        server_url=str(payload.get("u") or ""),
        alias=str(payload.get("a") or ""),
        tenant_id=str(payload.get("t") or ""),
    )
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="JOB-Monitor-Agent-macOS.zip"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/download-agent")
async def download_agent_package(
    request: Request,
    server_url: Optional[str] = Query(None, description="URL WebSocket del servidor central"),
    alias: Optional[str] = Query("", description="Alias opcional preconfigurado para la PC"),
    tenant_id: Optional[str] = Query(None, description="Tenant ID destino (opcional para Super Admin)"),
):
    """
    Mini-Función A3: Endpoint HTTP que construye y descarga el paquete .zip
    preconfigurado para instalar el agente en Windows o macOS, ligado al tenant del usuario.
    """
    user = verify_admin_access(request)
    default_ws = resolve_default_ws_url(request)
    final_ws_url = normalize_ws_url(server_url or "", default_ws)
    clean_alias = (alias or "").strip()

    if user["role"] == "superadmin":
        target_tid = (tenant_id or "").strip() or MASTER_TENANT_ID
    else:
        target_tid = user["tenant_id"]

    zip_bytes = build_agent_package_zip(server_url=final_ws_url, alias=clean_alias, tenant_id=target_tid)

    safe_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", clean_alias).strip("-") if clean_alias else "Installer"
    filename = f"JOB-Monitor-Agent-{safe_slug}.zip"

    alias_info = f"con alias '{clean_alias}'" if clean_alias else "con alias automático"
    log_entry = db.add_log(
        agent_id="SYSTEM-SERVER",
        alias="Consola de Administración",
        hostname="Servidor Central",
        username=user["username"],
        category="AUDIT",
        message=f"{user['display_name']} generó paquete instalador '{filename}' ({alias_info} • Tenant: {target_tid})",
    )
    await hub.broadcast_new_log(log_entry, tenant_id=target_tid)

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/chat/{agent_id}")
async def get_agent_chat_history(request: Request, agent_id: str, limit: int = Query(100)):
    user = verify_admin_access(request)
    if not hub.can_user_access_agent(user, agent_id):
        raise HTTPException(status_code=403, detail="No tienes acceso a este equipo")
    messages = db.get_chat_history(agent_id=agent_id, limit=limit)
    return {
        "ok": True,
        "agent_id": agent_id,
        "messages": messages,
    }


@app.post("/api/send-file/{agent_id}")
async def send_file_to_agent(
    request: Request,
    agent_id: str,
    file: UploadFile = File(...),
    destination: str = Form("Desktop"),
    auto_open: str = Form("false"),
):
    user = verify_admin_access(request)
    if not hub.can_user_access_agent(user, agent_id):
        raise HTTPException(status_code=403, detail="No tienes acceso a este equipo")
    if not user.get("can_control", True):
        raise HTTPException(status_code=403, detail="Tu cuenta es de solo monitoreo (sin envío de archivos)")
    agent = hub.agents.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="El equipo seleccionado no está conectado en este momento")

    raw_bytes = await file.read()
    max_bytes = 20 * 1024 * 1024  # 20 MB límite seguro por WebSocket
    if len(raw_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail="El archivo supera el límite de 20 MB para transferencia directa")

    safe_filename = os.path.basename(file.filename or "archivo_soporte.bin").strip() or "archivo_soporte.bin"
    dest_clean = "Downloads" if destination.strip().lower() in ("downloads", "descargas") else "Desktop"
    dest_label = "Descargas (Downloads)" if dest_clean == "Downloads" else "Escritorio (Desktop)"
    should_open = auto_open.strip().lower() in ("true", "1", "yes", "on")

    b64_data = base64.b64encode(raw_bytes).decode("ascii")
    sent = await hub.send_to_agent(
        agent_id,
        {
            "type": "file_transfer",
            "filename": safe_filename,
            "destination": dest_clean,
            "auto_open": should_open,
            "data_b64": b64_data,
        },
    )
    if not sent:
        raise HTTPException(status_code=502, detail="No se pudo entregar el archivo al agente remoto")

    size_kb = max(1, round(len(raw_bytes) / 1024))
    open_note = "con apertura automática" if should_open else "sin abrir automáticamente"
    log_entry = db.add_log(
        agent_id=agent.agent_id,
        alias=agent.alias,
        hostname=agent.hostname,
        username=agent.username,
        category="AUDIT",
        message=f"{user['display_name']} envió archivo '{safe_filename}' ({size_kb} KB) a {dest_label} ({open_note})",
    )
    await hub.broadcast_new_log(log_entry, tenant_id=agent.tenant_id)

    return {
        "ok": True,
        "agent_id": agent_id,
        "filename": safe_filename,
        "size_bytes": len(raw_bytes),
        "destination": dest_clean,
        "auto_open": should_open,
    }


@app.get("/api/metrics")
async def get_metrics(
    request: Request,
    date: Optional[str] = Query(None, description="Fecha YYYY-MM-DD"),
    tenant_id: Optional[str] = Query(None, description="Filtro opcional de tenant para Super Admin"),
):
    user = verify_admin_access(request)
    if user["role"] == "superadmin":
        allowed_tid = (tenant_id or "").strip() if (tenant_id and tenant_id != "ALL") else None
    else:
        allowed_tid = user["tenant_id"]

    metrics = db.get_daily_metrics(date_str=date, allowed_tenant_id=allowed_tid)
    for item in metrics:
        live_ag = hub.agents.get(item["agent_id"])
        if live_ag:
            item["is_online"] = True
            item["activity_status"] = live_ag.activity_status
            item["current_idle_seconds"] = live_ag.idle_seconds
            item["active_window"] = live_ag.active_window
        else:
            item["is_online"] = False
            item["activity_status"] = "offline"
            item["current_idle_seconds"] = 0
            item["active_window"] = "Desconectado"
    return {
        "ok": True,
        "metrics": metrics,
    }


@app.get("/api/logs")
async def get_logs(
    request: Request,
    agent_id: Optional[str] = Query("ALL"),
    category: Optional[str] = Query("ALL"),
    q: Optional[str] = Query(""),
    limit: int = Query(250),
    tenant_id: Optional[str] = Query(None),
):
    user = verify_admin_access(request)
    if user["role"] == "superadmin":
        allowed_tid = (tenant_id or "").strip() if (tenant_id and tenant_id != "ALL") else None
    else:
        allowed_tid = user["tenant_id"]

    logs = db.query_logs(
        agent_id=agent_id,
        category=category,
        search_q=q,
        limit=limit,
        allowed_tenant_id=allowed_tid,
    )
    devices = db.get_all_known_devices(allowed_tenant_id=allowed_tid)
    return {
        "ok": True,
        "logs": logs,
        "devices": devices,
    }


@app.get("/api/dvr-day")
async def get_dvr_day(
    request: Request,
    agent_id: Optional[str] = Query("ALL"),
    date: Optional[str] = Query(None),
    category: Optional[str] = Query("ALL"),
    q: Optional[str] = Query(""),
    tenant_id: Optional[str] = Query(None),
):
    user = verify_admin_access(request)
    if user["role"] == "superadmin":
        allowed_tid = (tenant_id or "").strip() if (tenant_id and tenant_id != "ALL") else None
    else:
        allowed_tid = user["tenant_id"]

    payload = db.get_dvr_day_data(
        agent_id=agent_id,
        target_date=date,
        category=category,
        search_q=q,
        allowed_tenant_id=allowed_tid,
    )
    devices = db.get_all_known_devices(allowed_tenant_id=allowed_tid)
    return {
        "ok": True,
        **payload,
        "devices": devices,
    }


@app.get("/api/logs/export-csv")
async def export_logs_csv(
    request: Request,
    agent_id: Optional[str] = Query("ALL"),
    category: Optional[str] = Query("ALL"),
    q: Optional[str] = Query(""),
    tenant_id: Optional[str] = Query(None),
):
    user = verify_admin_access(request)
    if user["role"] == "superadmin":
        allowed_tid = (tenant_id or "").strip() if (tenant_id and tenant_id != "ALL") else None
    else:
        allowed_tid = user["tenant_id"]

    csv_text = db.export_logs_csv(
        agent_id=agent_id,
        category=category,
        search_q=q,
        allowed_tenant_id=allowed_tid,
    )
    return Response(
        content="\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="bitacora_equipos_job_monitor.csv"'
        },
    )


@app.get("/api/network/summary")
async def get_network_summary(
    request: Request,
    date: Optional[str] = Query(None, description="Fecha YYYY-MM-DD"),
    tenant_id: Optional[str] = Query(None),
):
    user = verify_admin_access(request)
    if user["role"] == "superadmin":
        allowed_tid = (tenant_id or "").strip() if (tenant_id and tenant_id != "ALL") else None
    else:
        allowed_tid = user["tenant_id"]
    summary = db.get_network_dashboard_summary(date_str=date, allowed_tenant_id=allowed_tid)
    for c in summary["clients"]:
        live = hub.agents.get(c["agent_id"])
        c["is_online"] = bool(live)
        c["rx_kbps"] = live.rx_kbps if live else 0.0
        c["tx_kbps"] = live.tx_kbps if live else 0.0
    return {"ok": True, **summary}


@app.get("/api/network/domains")
async def get_network_domains(
    request: Request,
    date: Optional[str] = Query(None),
    agent_id: Optional[str] = Query("ALL"),
    category: Optional[str] = Query("ALL"),
    q: Optional[str] = Query(""),
    limit: int = Query(300),
    tenant_id: Optional[str] = Query(None),
):
    user = verify_admin_access(request)
    if user["role"] == "superadmin":
        allowed_tid = (tenant_id or "").strip() if (tenant_id and tenant_id != "ALL") else None
    else:
        allowed_tid = user["tenant_id"]
    domains = db.get_agent_domains_detail(
        date_str=date, agent_id=agent_id, category=category, search_q=q,
        limit=limit, allowed_tenant_id=allowed_tid,
    )
    return {"ok": True, "domains": domains}


@app.get("/api/network/intel-status")
async def get_network_intel_status(request: Request):
    verify_admin_access(request)
    return {"ok": True, **intel_get_status()}


@app.get("/api/network/export-csv")
async def export_network_csv(
    request: Request,
    date: Optional[str] = Query(None),
    agent_id: Optional[str] = Query("ALL"),
    category: Optional[str] = Query("ALL"),
    q: Optional[str] = Query(""),
    tenant_id: Optional[str] = Query(None),
):
    user = verify_admin_access(request)
    if user["role"] == "superadmin":
        allowed_tid = (tenant_id or "").strip() if (tenant_id and tenant_id != "ALL") else None
    else:
        allowed_tid = user["tenant_id"]
    csv_text = db.export_network_csv(
        date_str=date, agent_id=agent_id, category=category, search_q=q,
        allowed_tenant_id=allowed_tid,
    )
    return Response(
        content="\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="trafico_red_job_monitor.csv"'},
    )


@app.post("/api/devices/{agent_id}/alias")
async def update_device_alias(request: Request, agent_id: str, body: AliasUpdateRequest):
    user = verify_admin_access(request)
    if not hub.can_user_access_agent(user, agent_id):
        raise HTTPException(status_code=403, detail="No tienes acceso a este equipo")
    if not user.get("can_control", True):
        raise HTTPException(status_code=403, detail="Tu cuenta es de solo monitoreo")
    if not body.alias.strip():
        raise HTTPException(status_code=400, detail="El alias no puede estar vacío")
    await hub.update_alias(agent_id, body.alias)
    return {"ok": True, "agent_id": agent_id, "alias": body.alias.strip()}


CAPTURE_RETENTION_DAYS = max(3, int(os.environ.get("RA_CAPTURE_RETENTION_DAYS", "30")))


def _cleanup_expired_captures() -> int:
    """MF-ST12: Depuracion automatica de evidencias WebP/JPG con mas de N dias en disco."""
    removed = 0
    cutoff = time.time() - (CAPTURE_RETENTION_DAYS * 86400)
    try:
        if not CAPTURES_DIR.exists():
            return 0
        for sub in CAPTURES_DIR.iterdir():
            if not sub.is_dir():
                continue
            for img_f in sub.iterdir():
                if img_f.is_file() and img_f.suffix.lower() in (".webp", ".jpg", ".jpeg", ".png"):
                    try:
                        if img_f.stat().st_mtime < cutoff:
                            img_f.unlink()
                            removed += 1
                    except Exception:
                        pass
    except Exception:
        pass
    return removed


async def _capture_retention_loop():
    await asyncio.sleep(10.0)
    while True:
        await asyncio.to_thread(_cleanup_expired_captures)
        await asyncio.sleep(43200.0)


@app.on_event("startup")
async def _start_retention_cleaner():
    asyncio.create_task(_capture_retention_loop())
    intel_start()  # workers de inteligencia IP/dominio + precarga de caché PostgreSQL/SQLite


@app.get("/api/screen-time")
async def get_screen_time(
    request: Request,
    agent_id: Optional[str] = Query("ALL"),
    date: Optional[str] = Query(None),
    period: Optional[str] = Query("day"),
    tenant_id: Optional[str] = Query(None),
):
    """MF-ST09: Endpoint de analitica jerarquica estilo Tiempo en Pantalla."""
    user = verify_admin_access(request)
    if user["role"] == "superadmin":
        allowed_tid = (tenant_id or "").strip() if (tenant_id and tenant_id != "ALL") else None
    else:
        allowed_tid = user["tenant_id"]

    data = db.get_screen_time_analytics(
        agent_id=agent_id or "ALL",
        date_str=date,
        period=period or "day",
        allowed_tenant_id=allowed_tid,
    )
    devices = db.get_all_known_devices(allowed_tenant_id=allowed_tid)
    return {"ok": True, "devices": devices, **data}


def _build_preview_webp_bytes(raw_bytes: bytes) -> Optional[bytes]:
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw_bytes))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        if im.width > 640:
            ratio = 640.0 / float(im.width)
            im = im.resize((640, max(1, int(im.height * ratio))), Image.Resampling.BILINEAR)
        out = io.BytesIO()
        im.save(out, format="WEBP", quality=58, method=3)
        return out.getvalue()
    except Exception:
        return None


@app.get("/api/captures/{agent_id}/{filename}")
async def get_capture_evidence(request: Request, agent_id: str, filename: str, preview: int = Query(0)):
    user = verify_admin_access(request)
    if not hub.can_user_access_agent(user, agent_id):
        raise HTTPException(status_code=403, detail="No tienes acceso a las capturas de este equipo")
    clean_filename = os.path.basename(filename)
    file_path = CAPTURES_DIR / agent_id / clean_filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Evidencia no encontrada")
    serve_path = file_path
    serve_name = clean_filename
    if preview == 1:
        prev_name = f".prev_{os.path.splitext(clean_filename)[0]}.webp"
        prev_path = CAPTURES_DIR / agent_id / prev_name
        if not prev_path.exists():
            p_bytes = _build_preview_webp_bytes(file_path.read_bytes())
            if p_bytes:
                try:
                    prev_path.write_bytes(p_bytes)
                except Exception:
                    pass
        if prev_path.exists() and prev_path.is_file():
            serve_path = prev_path
            serve_name = prev_name
    mime_type = "image/webp" if serve_name.lower().endswith(".webp") else "image/jpeg"
    return FileResponse(
        path=str(serve_path),
        media_type=mime_type,
        filename=clean_filename,
        content_disposition_type="inline",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _save_capture_bytes(agent_id: str, sct_b64: str, prefix: str = "ev") -> Optional[str]:
    if not sct_b64:
        return None
    try:
        img_bytes = base64.b64decode(sct_b64)
        agent_captures_dir = CAPTURES_DIR / agent_id
        agent_captures_dir.mkdir(parents=True, exist_ok=True)
        is_already_webp = len(img_bytes) > 12 and img_bytes[:4] == b"RIFF" and img_bytes[8:12] == b"WEBP"
        ext = "webp"
        if not is_already_webp:
            try:
                from PIL import Image
                img_obj = Image.open(io.BytesIO(img_bytes))
                if img_obj.mode not in ("RGB", "RGBA"):
                    img_obj = img_obj.convert("RGB")
                webp_buf = io.BytesIO()
                img_obj.save(webp_buf, format="WEBP", quality=80, method=4)
                img_bytes = webp_buf.getvalue()
            except Exception:
                ext = "jpg"
        fname = f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:6]}.{ext}"
        fpath = agent_captures_dir / fname
        fpath.write_bytes(img_bytes)
        prev_bytes = _build_preview_webp_bytes(img_bytes)
        if prev_bytes:
            try:
                (agent_captures_dir / f".prev_{os.path.splitext(fname)[0]}.webp").write_bytes(prev_bytes)
            except Exception:
                pass
        return f"/api/captures/{agent_id}/{fname}"
    except Exception as e:
        print(f"[Captures] Error al almacenar evidencia visual: {e}")
        return None


NETWORK_ALERT_CATEGORIES = {"STREAMING", "SOCIAL", "AI", "CLOUD", "REMOTE", "MESSAGING", "SHOPPING", "GAMING"}
NETWORK_ALERT_THROTTLE_SECONDS = 15 * 60
NETWORK_CATEGORY_LABELS = {
    "STREAMING": "Streaming / Video",
    "SOCIAL": "Redes Sociales",
    "AI": "Inteligencia Artificial",
    "CLOUD": "Almacenamiento Cloud",
    "REMOTE": "Acceso Remoto",
    "MESSAGING": "Mensajería",
    "SHOPPING": "Compras / E-commerce",
    "GAMING": "Videojuegos",
}
_network_alert_last: Dict[tuple, float] = {}


_GENERIC_BROWSER_INFRA = (
    "google.com", "cloudfront.net", "aws.amazon.com", "amazonaws.com", "akamaitechnologies.com",
    "akamai.com", "akamaiedge.net", "fastly.com", "fastly.net", "cloudflare.com", "cloudflare.net",
    "uninet.net.mx", "gstatic.com", "googleapis.com", "1e100.net",
)


def _is_generic_infra_entry(d: dict) -> bool:
    dom = str(d.get("domain") or "").strip().lower().rstrip(".")
    if not dom:
        return False
    if any(dom == k or dom.endswith("." + k) for k in _GENERIC_BROWSER_INFRA):
        return True
    parts = dom.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)  # IP cruda


def _inject_site_hint(domains: list, d_rx: int, d_tx: int, active_window: str, app_name: str, resource_title: str):
    """Inyecta/desambigua en `domains` el sitio inferido del título de ventana (modifica la lista in situ)."""
    combined_title = f"{active_window or ''} {resource_title or ''}".strip()
    site_hint = infer_site_from_window_title(combined_title, app_name)
    if not site_hint:
        return
    entries = [d for d in domains if isinstance(d, dict)]
    if not (is_browser_name(app_name) or is_browser_name(combined_title)
            or any(is_browser_name(str(d.get("app_name") or "")) for d in entries)):
        return
    site, cat = site_hint
    if any(str(d.get("domain") or "").strip().lower() == site for d in entries):
        return
    browser_app = next(
        (str(d.get("app_name")) for d in entries if is_browser_name(str(d.get("app_name") or ""))),
        (app_name if is_browser_name(app_name) else "") or "Google Chrome",
    )[:40]
    relabel = [d for d in entries
               if is_browser_name(str(d.get("app_name") or "")) and _is_generic_infra_entry(d)]
    if relabel:
        for d in relabel:
            d["domain"] = site
            d["category"] = cat
            d["app_name"] = str(d.get("app_name") or browser_app)[:40]
            d["confidence"] = max(int(d.get("confidence") or 0), 90)
        return
    conns = sum(int(d.get("connections", 1) or 1) for d in entries)
    rx_b = int(d_rx * 0.7) if d_rx > 0 else 4096
    tx_b = int(d_tx * 0.7) if d_tx > 0 else 1024
    domains.append({
        "domain": site, "app_name": browser_app, "category": cat,
        "connections": max(2, conns // 2 or 2),
        "rx_bytes": rx_b, "tx_bytes": tx_b, "estimated_bytes": rx_b + tx_b,
        "focus_app": browser_app, "confidence": 90,
    })


async def _ingest_network_telemetry_slice(
    agent_id: str,
    agent: AgentConnection,
    net_data: dict,
    custom_date: Optional[str] = None,
    active_window: str = "",
    app_name: str = "",
    resource_title: str = "",
):
    if not isinstance(net_data, dict):
        return
    try:
        d_rx = max(0, int(net_data.get("delta_rx_bytes", 0) or 0))
        d_tx = max(0, int(net_data.get("delta_tx_bytes", 0) or 0))
        rx_kbps = max(0.0, float(net_data.get("rx_kbps", 0.0) or 0.0))
        tx_kbps = max(0.0, float(net_data.get("tx_kbps", 0.0) or 0.0))
    except (TypeError, ValueError):
        return
    domains = net_data.get("domains") or []
    if not isinstance(domains, list):
        domains = []
    domains = domains[:100]

    # Agentes sin actualizar: deducir el sitio desde el título de ventana y atribuirlo aquí
    _inject_site_hint(domains, d_rx, d_tx, active_window or ("" if custom_date else getattr(agent, "active_window", "")),
                      app_name, resource_title)

    # Perfil de paquete: usar el del agente o calcularlo aquí (agentes sin actualizar)
    packet_profile = net_data.get("packet_profile")
    if not isinstance(packet_profile, dict) or not packet_profile.get("packet_label"):
        packet_profile = classify_packet_profile(
            rx_kbps, tx_kbps, 0, "",
            str(getattr(agent, "active_window", "") or ""), domains,
        )
        for d in domains:
            if isinstance(d, dict) and not d.get("packet_type"):
                d["packet_type"] = packet_profile["packet_label"]
                d["confidence"] = packet_profile["confidence"]

    if d_rx or d_tx or rx_kbps or tx_kbps:
        db.record_network_slice(agent_id, d_rx, d_tx, rx_kbps, tx_kbps, custom_date=custom_date)
    if domains:
        db.record_domain_activity_batch(agent_id, domains, custom_date=custom_date)

    # Métricas en vivo (las repeticiones offline no representan la velocidad actual)
    if not custom_date:
        today = db._now_dt().strftime("%Y-%m-%d")
        if getattr(agent, "_net_day", None) != today:
            agent._net_day = today
            agent.today_rx_bytes = 0
            agent.today_tx_bytes = 0
        agent.rx_kbps = rx_kbps
        agent.tx_kbps = tx_kbps
        agent.packet_profile = {
            "packet_code": str(packet_profile.get("packet_code") or ""),
            "packet_label": str(packet_profile.get("packet_label") or ""),
            "confidence": int(packet_profile.get("confidence") or 0),
            "keystrokes_slice": int(packet_profile.get("keystrokes_slice") or 0),
        }
        agent.today_rx_bytes += d_rx
        agent.today_tx_bytes += d_tx
        agent.recent_domains = [
            {
                "domain": str(d.get("domain") or "")[:120],
                "app_name": str(d.get("app_name") or "")[:40],
                "category": str(d.get("category") or "OTHER").upper(),
                "connections": int(d.get("connections") or 1),
                "estimated_bytes": int(d.get("estimated_bytes") or 0),
            }
            for d in domains[:15] if isinstance(d, dict) and d.get("domain")
        ]

    now_mono = time.monotonic()
    if len(_network_alert_last) > 5000:
        cutoff = now_mono - NETWORK_ALERT_THROTTLE_SECONDS
        for k in [k for k, v in _network_alert_last.items() if v < cutoff]:
            _network_alert_last.pop(k, None)
    for item in domains:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category") or "").strip().upper()
        domain = str(item.get("domain") or "").strip().lower()
        if cat not in NETWORK_ALERT_CATEGORIES or not domain:
            continue
        key = (agent_id, domain)
        last = _network_alert_last.get(key)
        if last is not None and (now_mono - last) < NETWORK_ALERT_THROTTLE_SECONDS:
            continue
        _network_alert_last[key] = now_mono
        app_label = str(item.get("app_name") or "Desconocido")[:80]
        msg = f"Conexión a {domain} ({NETWORK_CATEGORY_LABELS.get(cat, cat)}) desde '{app_label}'"
        log_entry = db.add_log(
            agent_id=agent_id,
            alias=agent.alias,
            hostname=agent.hostname,
            username=agent.username,
            category="NETWORK",
            message=msg,
            meta_json=json.dumps({"domain": domain, "app_name": app_label, "network_category": cat}),
            custom_timestamp=(custom_date.strip() if custom_date and len(custom_date.strip()) >= 19 else None),
        )
        await hub.broadcast_new_log(log_entry, tenant_id=agent.tenant_id)


async def _ingest_keyboard_event_payload(
    agent_id: str,
    agent: AgentConnection,
    data: dict,
    is_offline_replay: bool = False,
):
    active_win = str(data.get("active_window") or agent.active_window or "Escritorio")
    dur_secs = int(data.get("duration_seconds", 0))
    keystrokes_cnt = int(data.get("keystrokes_count", 0))
    txt_content = str(data.get("text_content", "")).strip()
    keys_detail = data.get("keystrokes_detail", [])
    kb_layout = str(data.get("keyboard_layout", "Estándar"))
    sct_b64 = str(data.get("screenshot_b64", "")).strip()
    custom_ts = str(data.get("timestamp") or "").strip() if is_offline_replay else None

    evidence_url = _save_capture_bytes(agent_id, sct_b64, prefix="kb")

    meta = {
        "text_content": txt_content,
        "keystrokes_detail": keys_detail,
        "keystrokes_count": keystrokes_cnt,
        "duration_seconds": dur_secs,
        "active_window": active_win,
        "keyboard_layout": kb_layout,
        "evidence_path": evidence_url,
        "offline_vault": is_offline_replay,
    }

    # Correlación cruzada teclado + app en foco + red (agentes sin actualizar: usar telemetría en vivo)
    kb_app = str(data.get("app_name") or "").strip()[:60]
    kb_title = str(data.get("resource_title") or "").strip()[:115]
    try:
        kb_rx = max(0.0, float(data.get("rx_kbps", agent.rx_kbps if not is_offline_replay else 0.0) or 0.0))
        kb_tx = max(0.0, float(data.get("tx_kbps", agent.tx_kbps if not is_offline_replay else 0.0) or 0.0))
    except (TypeError, ValueError):
        kb_rx = kb_tx = 0.0
    kb_domains = data.get("active_domains")
    if not isinstance(kb_domains, list):
        kb_domains = [] if is_offline_replay else list(agent.recent_domains or [])[:4]
    kb_domains = [
        {
            "domain": str(d.get("domain") or "")[:120],
            "category": str(d.get("category") or "OTHER").upper(),
            "estimated_bytes": int(d.get("estimated_bytes") or 0),
        }
        for d in kb_domains[:4] if isinstance(d, dict) and d.get("domain")
    ]
    kb_packet = data.get("packet_profile")
    if not isinstance(kb_packet, dict) or not kb_packet.get("packet_label"):
        kb_packet = classify_packet_profile(kb_rx, kb_tx, keystrokes_cnt, kb_app, f"{kb_title} {active_win}", kb_domains)
    meta.update({
        "app_name": kb_app,
        "resource_title": kb_title,
        "rx_kbps": kb_rx,
        "tx_kbps": kb_tx,
        "active_domains": kb_domains,
        "packet_profile": {
            "packet_code": str(kb_packet.get("packet_code") or ""),
            "packet_label": str(kb_packet.get("packet_label") or ""),
            "confidence": int(kb_packet.get("confidence") or 0),
            "keystrokes_slice": int(kb_packet.get("keystrokes_slice") or 0),
        },
    })

    preview_txt = f"'{txt_content[:70]}...'" if len(txt_content) > 70 else (f"'{txt_content}'" if txt_content else "(Pulsaciones de navegación)")
    msg_txt = f"Redacción en '{active_win}' (Pulsaciones: {preview_txt} • {keystrokes_cnt} teclas • Duración: {dur_secs}s • Idioma: {kb_layout})"
    if kb_domains or kb_rx >= 15 or kb_tx >= 10:
        dom_txt = " / ".join(d["domain"] for d in kb_domains[:3]) or "tráfico sin dominio"
        rate_txt = f"↓ {kb_rx / 1000.0:.1f} Mbps" if kb_rx >= kb_tx else f"↑ {kb_tx / 1000.0:.1f} Mbps"
        short_pkt = str(meta["packet_profile"]["packet_label"]).split(" (")[0].split(" / ")[-1]
        msg_txt += f" • Red: {dom_txt} ({rate_txt}, {short_pkt})"

    log_entry = db.add_log(
        agent_id=agent_id,
        alias=agent.alias,
        hostname=agent.hostname,
        username=agent.username,
        category="KEYBOARD",
        message=msg_txt,
        duration_seconds=dur_secs,
        evidence_path=evidence_url,
        meta_json=json.dumps(meta),
        custom_timestamp=custom_ts or None,
    )
    await hub.broadcast_new_log(log_entry, tenant_id=agent.tenant_id)



def _fmt_dur_short(seconds: int) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec}s" if sec else f"{m}m"
    h, rm = divmod(m, 60)
    return f"{h}h {rm}m"


async def _ingest_focus_switch_payload(
    agent_id: str,
    agent: AgentConnection,
    data: dict,
    is_offline_replay: bool = False,
):
    """MF-ST07: Persiste captura WebP de cambio de App/Pestana y actualiza metricas."""
    app_name = str(data.get("app_name") or "Escritorio").strip()[:60]
    res_title = str(data.get("resource_title") or "Ventana principal").strip()[:115]
    cat_tag = str(data.get("category_tag") or "Productividad y Gestion").strip()[:45]
    active_win = str(data.get("active_window") or app_name)[:140]
    prev_win = str(data.get("previous_window") or "Escritorio")[:140]
    dur_secs = max(1, int(data.get("duration_seconds") or 1))
    sct_b64 = str(data.get("screenshot_b64") or "").strip()
    custom_ts = str(data.get("timestamp") or "").strip() if is_offline_replay else None

    if not is_offline_replay:
        agent.active_window = active_win

    evidence_url = _save_capture_bytes(agent_id, sct_b64, prefix="sw") if sct_b64 else None
    db.record_focus_switch(
        agent_id=agent_id,
        app_name=app_name,
        resource_title=res_title,
        category_tag=cat_tag,
        custom_timestamp=custom_ts or None,
    )

    target_lbl = f"{app_name} — {res_title}" if res_title != "Ventana principal" else app_name
    msg_txt = f"Cambio a '{target_lbl}' (permanencia previa en '{prev_win}': {_fmt_dur_short(dur_secs)})"
    meta = {
        "category": "WINDOW",
        "app_name": app_name,
        "resource_title": res_title,
        "category_tag": cat_tag,
        "active_window": active_win,
        "previous_window": prev_win,
        "duration_seconds": dur_secs,
        "evidence_path": evidence_url,
        "offline_vault": is_offline_replay,
    }
    log_entry = db.add_log(
        agent_id=agent_id,
        alias=agent.alias,
        hostname=agent.hostname,
        username=agent.username,
        category="WINDOW",
        message=msg_txt,
        duration_seconds=dur_secs,
        evidence_path=evidence_url,
        meta_json=json.dumps(meta, ensure_ascii=False),
        custom_timestamp=custom_ts or None,
    )
    await hub.broadcast_new_log(log_entry, tenant_id=agent.tenant_id)

    # Registrar el sitio de la pestaña de inmediato (sin esperar el siguiente tick de red)
    site_hint = infer_site_from_window_title(f"{active_win} {res_title}", app_name)
    if site_hint and (is_browser_name(app_name) or is_browser_name(active_win)):
        try:
            db.record_domain_activity_batch(agent_id, [{
                "domain": site_hint[0], "app_name": app_name, "category": site_hint[1],
                "connections": 3, "rx_bytes": 24576, "tx_bytes": 4096, "estimated_bytes": 28672,
                "focus_app": app_name, "confidence": 95,
            }], custom_date=custom_ts)
        except Exception as e:
            print(f"[Network] Error al registrar sitio desde cambio de foco: {e}")

    if not is_offline_replay:
        await hub.broadcast_telemetry_update(agent)


@app.websocket("/ws/agent")
async def ws_agent_endpoint(websocket: WebSocket):
    if config.REQUIRE_MTLS:
        forwarded_proto = websocket.headers.get("x-forwarded-proto", "")
        client_verify = websocket.headers.get("x-ssl-client-verify", "")
        if forwarded_proto == "https" and client_verify != "SUCCESS":
            await websocket.close(code=1008)
            return

    await websocket.accept()
    agent_id: Optional[str] = None
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
        hello = json.loads(raw)
        if hello.get("type") != "register" or hello.get("token") != config.AGENT_TOKEN:
            await websocket.send_json({"type": "error", "message": "Token de agente inválido"})
            await websocket.close(code=1008)
            return

        raw_agent_id = str(hello.get("agent_id") or f"{hello.get('hostname', 'PC')}-{hello.get('username', 'user')}")
        agent_id = await hub.resolve_effective_agent_id(raw_agent_id, websocket, hello)
        if db.is_device_revoked(agent_id) or db.is_device_revoked(raw_agent_id):
            await websocket.send_json({"type": "self_destruct"})
            await websocket.close(code=1008)
            return

        agent = await hub.register_agent(agent_id, websocket, hello)

        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"] is not None:
                agent.last_seen = time.time()
                await hub.forward_frame_to_viewers(agent_id, message["bytes"])

            elif "text" in message and message["text"] is not None:
                data = json.loads(message["text"])
                msg_type = data.get("type")
                agent.last_seen = time.time()

                if msg_type == "telemetry":
                    was_bg = agent.is_background_session
                    agent.idle_seconds = int(data.get("idle_seconds", 0))
                    raw_win = data.get("active_window", "Escritorio")
                    if "monitors" in data:
                        agent.monitors = data["monitors"]
                    if "current_monitor" in data:
                        agent.current_monitor = int(data["current_monitor"])
                    if "session_id" in data and data["session_id"] is not None:
                        agent.session_id = int(data["session_id"])
                    if "session_background" in data:
                        agent.is_background_session = bool(data["session_background"])
                    if "thumbnail" in data:
                        if data["thumbnail"]:
                            agent.thumbnail_b64 = data["thumbnail"]
                            if "session_background" not in data:
                                agent.is_background_session = False
                        else:
                            if "session_background" not in data:
                                agent.is_background_session = True
                    elif was_bg and (agent.idle_seconds < 15 or raw_win not in ("Escritorio", "")):
                        # Si la sesión en 2º plano acaba de retomar foco/entrada, solicitar miniatura inmediata
                        await hub.send_to_agent(agent_id, {"type": "capture_thumbnail"})

                    if agent.is_background_session and raw_win in ("Escritorio", ""):
                        agent.active_window = "Sesión en 2º plano (Cambio de usuario)"
                    else:
                        agent.active_window = raw_win

                    delta_secs = int(data.get("delta_seconds", 4))
                    app_name = data.get("app_name") or agent.active_window
                    away_alert = bool(data.get("away_alert", False))

                    db.record_telemetry_slice(
                        agent_id=agent_id,
                        delta_seconds=delta_secs,
                        is_active=(agent.idle_seconds < 180),
                        app_name=app_name,
                        increment_away_alert=away_alert,
                        resource_title=str(data.get("resource_title") or "Ventana principal"),
                        category_tag=str(data.get("category_tag") or "Productividad y Gestion"),
                        resource_slices=data.get("resource_slices") if isinstance(data.get("resource_slices"), list) else None,
                    )

                    saved_batch_url = None
                    saved_batch_b64 = None
                    for ev in data.get("log_events", []):
                        cat = ev.get("category", "WINDOW")
                        msg_txt = ev.get("message", "")
                        dur = ev.get("duration_seconds")
                        ev_sct = str(ev.get("screenshot_b64") or "").strip()
                        ev_url = None
                        if ev_sct:
                            if ev_sct == saved_batch_b64 and saved_batch_url:
                                ev_url = saved_batch_url
                            else:
                                ev_url = _save_capture_bytes(agent_id, ev_sct, prefix="dvr")
                                saved_batch_b64 = ev_sct
                                saved_batch_url = ev_url
                        ev_meta = {
                            "category": cat,
                            "active_window": str(ev.get("active_window") or agent.active_window or "Escritorio"),
                            "previous_window": str(ev.get("previous_window") or ""),
                            "duration_seconds": dur or 0,
                            "message": msg_txt,
                            "evidence_path": ev_url,
                        }
                        if msg_txt:
                            log_entry = db.add_log(
                                agent_id=agent_id,
                                alias=agent.alias,
                                hostname=agent.hostname,
                                username=agent.username,
                                category=cat,
                                message=msg_txt,
                                duration_seconds=dur,
                                evidence_path=ev_url,
                                meta_json=json.dumps(ev_meta) if ev_url else None,
                            )
                            await hub.broadcast_new_log(log_entry, tenant_id=agent.tenant_id)

                    if data.get("network"):
                        await _ingest_network_telemetry_slice(
                            agent_id, agent, data["network"],
                            active_window=str(raw_win or ""), app_name=str(app_name or ""),
                            resource_title=str(data.get("resource_title") or ""),
                        )

                    await hub.broadcast_telemetry_update(agent)

                elif msg_type == "keyboard_event":
                    # MF-07: Persistencia y almacenamiento de evento consolidado de redacción
                    await _ingest_keyboard_event_payload(agent_id, agent, data)

                elif msg_type == "focus_switch_event":
                    await _ingest_focus_switch_payload(agent_id, agent, data)

                elif msg_type == "sitai_encrypted_event":
                    # Bóveda Offline .sitai: Descifrado asimétrico exclusivo en el servidor y sincronización diferida
                    vault_filename = str(data.get("filename") or "").strip()
                    data_b64 = str(data.get("data_b64") or "").strip()
                    ack_ok = False
                    if data_b64:
                        try:
                            raw_sitai = base64.b64decode(data_b64)
                            inner_payload = decrypt_sitai_packet(raw_sitai)
                            inner_type = str(inner_payload.get("type") or "")
                            if inner_type == "keyboard_event":
                                await _ingest_keyboard_event_payload(agent_id, agent, inner_payload, is_offline_replay=True)
                                ack_ok = True
                            elif inner_type == "focus_switch_event":
                                await _ingest_focus_switch_payload(agent_id, agent, inner_payload, is_offline_replay=True)
                                ack_ok = True
                            elif inner_type == "offline_telemetry_batch":
                                custom_ts = str(inner_payload.get("timestamp") or "").strip() or None
                                delta_secs = int(inner_payload.get("delta_seconds", 0))
                                if delta_secs > 0:
                                    db.record_telemetry_slice(
                                        agent_id=agent_id,
                                        delta_seconds=delta_secs,
                                        is_active=bool(inner_payload.get("is_active", True)),
                                        app_name=str(inner_payload.get("app_name") or "Escritorio"),
                                        increment_away_alert=bool(inner_payload.get("away_alert", False)),
                                        resource_title=str(inner_payload.get("resource_title") or "Ventana principal"),
                                        category_tag=str(inner_payload.get("category_tag") or "Productividad y Gestion"),
                                        resource_slices=inner_payload.get("resource_slices") if isinstance(inner_payload.get("resource_slices"), list) else None,
                                        custom_timestamp=custom_ts,
                                    )
                                saved_off_url = None
                                saved_off_b64 = None
                                for ev in inner_payload.get("log_events", []):
                                    cat = ev.get("category", "WINDOW")
                                    msg_txt = ev.get("message", "")
                                    dur = ev.get("duration_seconds")
                                    ev_ts = str(ev.get("timestamp") or custom_ts or "").strip() or None
                                    ev_sct = str(ev.get("screenshot_b64") or "").strip()
                                    ev_url = None
                                    if ev_sct:
                                        if ev_sct == saved_off_b64 and saved_off_url:
                                            ev_url = saved_off_url
                                        else:
                                            ev_url = _save_capture_bytes(agent_id, ev_sct, prefix="dvr")
                                            saved_off_b64 = ev_sct
                                            saved_off_url = ev_url
                                    ev_meta = {
                                        "category": cat,
                                        "active_window": str(ev.get("active_window") or agent.active_window or "Escritorio"),
                                        "previous_window": str(ev.get("previous_window") or ""),
                                        "duration_seconds": dur or 0,
                                        "message": msg_txt,
                                        "evidence_path": ev_url,
                                        "offline_vault": True,
                                    }
                                    if msg_txt:
                                        log_entry = db.add_log(
                                            agent_id=agent_id,
                                            alias=agent.alias,
                                            hostname=agent.hostname,
                                            username=agent.username,
                                            category=cat,
                                            message=msg_txt,
                                            duration_seconds=dur,
                                            evidence_path=ev_url,
                                            meta_json=json.dumps(ev_meta) if ev_url else None,
                                            custom_timestamp=ev_ts,
                                        )
                                        await hub.broadcast_new_log(log_entry, tenant_id=agent.tenant_id)
                                if inner_payload.get("network"):
                                    await _ingest_network_telemetry_slice(
                                        agent_id, agent, inner_payload["network"],
                                        custom_date=custom_ts or db._now_dt().strftime("%Y-%m-%d"),
                                    )
                                ack_ok = True
                        except Exception as e:
                            print(f"[SITAI Vault] Error al descifrar contenedor '{vault_filename}' de {agent_id}: {e}")
                    if vault_filename:
                        await websocket.send_json({
                            "type": "sitai_ack",
                            "filename": vault_filename,
                            "ok": ack_ok,
                        })

                elif msg_type == "chat_reply":
                    text = str(data.get("text", "")).strip()
                    if text:
                        chat_entry = db.add_chat_message(
                            agent_id=agent_id,
                            sender="agent",
                            sender_name=agent.alias,
                            message=text,
                        )
                        log_entry = db.add_log(
                            agent_id=agent_id,
                            alias=agent.alias,
                            hostname=agent.hostname,
                            username=agent.username,
                            category="AUDIT",
                            message=f"Empleado respondió por chat: '{text[:120]}'",
                        )
                        await hub.broadcast_to_admins({
                            "type": "chat_message",
                            "chat": chat_entry,
                        })
                        await hub.broadcast_new_log(log_entry)

                elif msg_type == "clipboard_content":
                    clip_text = str(data.get("text", ""))
                    await hub.broadcast_to_viewers(
                        agent_id,
                        {
                            "type": "clipboard_data",
                            "agent_id": agent_id,
                            "text": clip_text,
                        },
                    )
                    log_entry = db.add_log(
                        agent_id=agent_id,
                        alias=agent.alias,
                        hostname=agent.hostname,
                        username=agent.username,
                        category="AUDIT",
                        message="Administrador consultó el portapapeles del equipo remoto",
                    )
                    await hub.broadcast_new_log(log_entry)

                elif msg_type == "file_received":
                    await hub.broadcast_to_viewers(
                        agent_id,
                        {
                            "type": "file_transfer_ack",
                            "agent_id": agent_id,
                            "filename": data.get("filename", ""),
                            "saved_path": data.get("saved_path", ""),
                            "auto_opened": bool(data.get("auto_opened", False)),
                            "ok": bool(data.get("ok", True)),
                            "error": data.get("error", ""),
                        },
                    )

                elif msg_type in ("shell_ready", "shell_output"):
                    skey = str(data.get("session_key") or "")
                    owner_info = hub.shell_sessions.get(skey)
                    if owner_info and owner_info[1] == agent_id:
                        owner_ws = owner_info[0]
                        try:
                            await owner_ws.send_json({**data, "agent_id": agent_id})
                        except Exception:
                            hub.shell_sessions.pop(skey, None)
                        if bool(data.get("closed")):
                            hub.shell_sessions.pop(skey, None)

                elif msg_type in ("fs_list_res", "fs_download_res", "fs_op_res"):
                    rkey = str(data.get("req_key") or "")
                    owner_info = hub.fs_requests.pop(rkey, None)
                    if owner_info and owner_info[1] == agent_id:
                        try:
                            await owner_info[0].send_json({**data, "agent_id": agent_id})
                        except Exception:
                            pass

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if agent_id:
            await hub.unregister_agent(agent_id, websocket)


@app.websocket("/ws/admin")
async def ws_admin_endpoint(websocket: WebSocket):
    await websocket.accept()
    authenticated = False
    user: Optional[dict] = None
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
        auth = json.loads(raw)
        if auth.get("type") != "auth":
            await websocket.send_json({"type": "auth_error", "message": "Mensaje de autenticación inválido"})
            await websocket.close(code=1008)
            return

        token_or_pass = str(auth.get("token") or auth.get("password") or "")
        supplied_user = str(auth.get("username") or "").strip()
        user = resolve_user_from_token(token_or_pass)
        if not user and supplied_user:
            user = db.authenticate_user(supplied_user, token_or_pass)

        if not user:
            await websocket.send_json({"type": "auth_error", "message": "Credenciales o sesión inválida"})
            await websocket.close(code=1008)
            return

        authenticated = True
        hub.admins.add(websocket)
        hub.admin_users[websocket] = user
        hub.subscriptions[websocket] = None

        init_payload = hub._build_agents_payload(user=user)
        init_payload["type"] = "auth_ok"
        await websocket.send_json(init_payload)

        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if "text" in message and message["text"] is not None:
                data = json.loads(message["text"])
                msg_type = data.get("type")

                if msg_type == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timezone": db.get_timezone(),
                    })

                elif msg_type == "subscribe":
                    target_id = data.get("agent_id")
                    await hub.set_admin_subscription(websocket, target_id)

                elif msg_type == "unsubscribe":
                    await hub.set_admin_subscription(websocket, None)

                elif msg_type == "set_alias":
                    target_id = data.get("agent_id")
                    new_alias = data.get("alias", "")
                    if target_id and new_alias and hub.can_user_access_agent(user, target_id) and user.get("can_control", True):
                        await hub.update_alias(target_id, new_alias)

                elif msg_type == "audit_control_mode":
                    target_id = hub.subscriptions.get(websocket)
                    enabled = bool(data.get("enabled", False))
                    if target_id and target_id in hub.agents and hub.can_user_access_agent(user, target_id):
                        ag = hub.agents[target_id]
                        action_text = (
                            f"{user['display_name']} activó el control remoto de mouse y teclado"
                            if enabled
                            else f"{user['display_name']} cambió a modo Solo Observar"
                        )
                        log_entry = db.add_log(
                            agent_id=ag.agent_id,
                            alias=ag.alias,
                            hostname=ag.hostname,
                            username=ag.username,
                            category="AUDIT",
                            message=action_text,
                        )
                        await hub.broadcast_new_log(log_entry, tenant_id=ag.tenant_id)

                elif msg_type == "request_all_thumbnails":
                    for ag_id in list(hub.agents.keys()):
                        if hub.can_user_access_agent(user, ag_id):
                            await hub.send_to_agent(ag_id, {"type": "capture_thumbnail"})

                elif msg_type == "chat_send":
                    target_id = data.get("agent_id") or hub.subscriptions.get(websocket)
                    text = str(data.get("text", "")).strip()
                    if target_id and text and target_id in hub.agents and hub.can_user_access_agent(user, target_id) and user.get("can_control", True):
                        ag = hub.agents[target_id]
                        chat_entry = db.add_chat_message(
                            agent_id=target_id,
                            sender="admin",
                            sender_name=user["display_name"],
                            message=text,
                        )
                        await hub.send_to_agent(
                            target_id,
                            {
                                "type": "chat_message",
                                "sender": user["display_name"],
                                "text": text,
                                "timestamp": chat_entry["timestamp"],
                            },
                        )
                        log_entry = db.add_log(
                            agent_id=ag.agent_id,
                            alias=ag.alias,
                            hostname=ag.hostname,
                            username=ag.username,
                            category="AUDIT",
                            message=f"{user['display_name']} envió mensaje directo: '{text[:120]}'",
                        )
                        await hub.broadcast_to_admins(
                            {
                                "type": "chat_message",
                                "chat": chat_entry,
                            },
                            target_tenant_id=ag.tenant_id,
                        )
                        await hub.broadcast_new_log(log_entry, tenant_id=ag.tenant_id)

                elif msg_type == "clipboard_get":
                    target_id = data.get("agent_id") or hub.subscriptions.get(websocket)
                    if target_id and hub.can_user_access_agent(user, target_id) and user.get("can_control", True):
                        await hub.send_to_agent(target_id, {"type": "clipboard_get"})

                elif msg_type == "clipboard_set":
                    target_id = data.get("agent_id") or hub.subscriptions.get(websocket)
                    clip_text = str(data.get("text", ""))
                    auto_paste = bool(data.get("auto_paste", False))
                    if target_id and target_id in hub.agents and hub.can_user_access_agent(user, target_id) and user.get("can_control", True):
                        ag = hub.agents[target_id]
                        await hub.send_to_agent(
                            target_id,
                            {
                                "type": "clipboard_set",
                                "text": clip_text,
                                "auto_paste": auto_paste,
                            },
                        )
                        paste_note = "con pegado automático (Ctrl+V)" if auto_paste else "copiado al portapapeles"
                        log_entry = db.add_log(
                            agent_id=ag.agent_id,
                            alias=ag.alias,
                            hostname=ag.hostname,
                            username=ag.username,
                            category="AUDIT",
                            message=f"{user['display_name']} envió texto al portapapeles remoto ({paste_note})",
                        )
                        await hub.broadcast_new_log(log_entry, tenant_id=ag.tenant_id)

                elif msg_type == "audio_chunk":
                    target_id = data.get("agent_id") or hub.subscriptions.get(websocket)
                    wav_b64 = data.get("wav_b64", "")
                    if target_id and wav_b64 and hub.can_user_access_agent(user, target_id) and user.get("can_control", True):
                        await hub.send_to_agent(
                            target_id,
                            {
                                "type": "play_audio",
                                "wav_b64": wav_b64,
                            },
                        )

                elif msg_type in ("shell_open", "shell_input", "shell_interrupt", "shell_close"):
                    target_id = str(data.get("agent_id") or hub.subscriptions.get(websocket) or "").strip()
                    if not target_id or not hub.can_user_access_agent(user, target_id) or not user.get("can_control", True):
                        continue
                    skey = f"sh_{id(websocket)}_{target_id[:18]}"
                    ag = hub.agents.get(target_id)
                    if msg_type == "shell_open":
                        if not ag:
                            await websocket.send_json({"type": "shell_output", "agent_id": target_id, "data": "[Equipo desconectado]\r\n", "closed": True})
                            continue
                        hub.shell_sessions[skey] = (websocket, target_id)
                        sh_type = str(data.get("shell_type") or "auto")[:20]
                        await hub.send_to_agent(target_id, {"type": "shell_open", "session_key": skey, "shell_type": sh_type})
                        log_entry = db.add_log(
                            agent_id=ag.agent_id, alias=ag.alias, hostname=ag.hostname, username=ag.username,
                            category="AUDIT", message=f"{user['display_name']} inicio sesion de Terminal Remota silenciosa ({sh_type})",
                        )
                        await hub.broadcast_new_log(log_entry, tenant_id=ag.tenant_id)
                    elif msg_type == "shell_input":
                        cmd_raw = str(data.get("data") or "")
                        await hub.send_to_agent(target_id, {"type": "shell_input", "session_key": skey, "data": cmd_raw})
                        clean_cmd = cmd_raw.strip()
                        if ag and clean_cmd and len(clean_cmd) >= 2:
                            masked = "[comando con credencial]" if any(w in clean_cmd.lower() for w in ("pass", "secret", "token", "key")) else clean_cmd[:90]
                            log_entry = db.add_log(
                                agent_id=ag.agent_id, alias=ag.alias, hostname=ag.hostname, username=ag.username,
                                category="AUDIT", message=f"Terminal remota ({user['display_name']}): {masked}",
                            )
                            await hub.broadcast_new_log(log_entry, tenant_id=ag.tenant_id)
                    elif msg_type == "shell_interrupt":
                        await hub.send_to_agent(target_id, {"type": "shell_interrupt", "session_key": skey})
                    elif msg_type == "shell_close":
                        hub.shell_sessions.pop(skey, None)
                        await hub.send_to_agent(target_id, {"type": "shell_close", "session_key": skey})

                elif msg_type in ("fs_list", "fs_download", "fs_upload", "fs_mutate"):
                    target_id = str(data.get("agent_id") or hub.subscriptions.get(websocket) or "").strip()
                    if not target_id or not hub.can_user_access_agent(user, target_id) or not user.get("can_control", True):
                        continue
                    ag = hub.agents.get(target_id)
                    if not ag:
                        await websocket.send_json({"type": "fs_op_res", "agent_id": target_id, "ok": False, "error": "El equipo remoto esta desconectado."})
                        continue
                    rkey = f"fs_{id(websocket)}_{uuid.uuid4().hex[:6]}"
                    hub.fs_requests[rkey] = (websocket, target_id)
                    if len(hub.fs_requests) > 200:
                        for old_k in list(hub.fs_requests.keys())[:50]:
                            hub.fs_requests.pop(old_k, None)
                    await hub.send_to_agent(target_id, {**data, "req_key": rkey})
                    if msg_type != "fs_list":
                        act_desc = {
                            "fs_download": f"descargo/previsualizo '{os.path.basename(str(data.get('path') or ''))}'",
                            "fs_upload": f"subio '{data.get('filename', '')}' a '{data.get('dest_dir', '')}'",
                            "fs_mutate": f"ejecuto '{data.get('op', '')}' sobre '{os.path.basename(str(data.get('path') or ''))}'",
                        }.get(msg_type, "opero archivos")
                        log_entry = db.add_log(
                            agent_id=ag.agent_id, alias=ag.alias, hostname=ag.hostname, username=ag.username,
                            category="AUDIT", message=f"Explorador de Archivos ({user['display_name']}): {act_desc}",
                        )
                        await hub.broadcast_new_log(log_entry, tenant_id=ag.tenant_id)

                elif msg_type == "audit_ptt_start":
                    target_id = data.get("agent_id") or hub.subscriptions.get(websocket)
                    if target_id and target_id in hub.agents and hub.can_user_access_agent(user, target_id) and user.get("can_control", True):
                        ag = hub.agents[target_id]
                        log_entry = db.add_log(
                            agent_id=ag.agent_id,
                            alias=ag.alias,
                            hostname=ag.hostname,
                            username=ag.username,
                            category="AUDIT",
                            message=f"{user['display_name']} transmitió voz en directo (Push-to-Talk) hacia el equipo",
                        )
                        await hub.broadcast_new_log(log_entry, tenant_id=ag.tenant_id)

                else:
                    target_id = data.get("agent_id") or hub.subscriptions.get(websocket)
                    if target_id and hub.can_user_access_agent(user, target_id):
                        if msg_type in ("mouse_move", "mouse_click", "mouse_scroll", "key_event", "shortcut") and not user.get("can_control", True):
                            continue
                        if msg_type == "set_stream_config":
                            ag = hub.agents.get(target_id)
                            if ag:
                                if "quality" in data:
                                    ag.stream_quality = int(data["quality"])
                                if "fps" in data:
                                    ag.stream_fps = int(data["fps"])
                        elif msg_type == "set_monitor":
                            ag = hub.agents.get(target_id)
                            if ag and "monitor_id" in data:
                                ag.current_monitor = int(data["monitor_id"])
                        await hub.send_to_agent(target_id, data)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if authenticated:
            await hub.remove_admin(websocket)


def main():
    parser = argparse.ArgumentParser(description="JOB-Monitor Server - Fase 2")
    parser.add_argument("--port", type=int, default=config.PORT, help="Puerto HTTP/WS (por defecto 5080)")
    parser.add_argument("--host", type=str, default=config.HOST, help="Host de escucha (por defecto 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true", help="No abrir el navegador automáticamente")
    args = parser.parse_args()

    config.PORT = args.port
    config.HOST = args.host

    if not args.no_browser:
        url = f"http://127.0.0.1:{args.port}"
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
