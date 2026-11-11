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
import webbrowser
import zipfile
from typing import Dict, List, Optional, Set

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
from database import AVAILABLE_TIMEZONES, MASTER_TENANT_ID, db
from models import (
    AgentConnection,
    AliasUpdateRequest,
    ChatSendRequest,
    ClipboardSetRequest,
    DeviceTenantAssignRequest,
    UserCreateRequest,
    UserUpdateRequest,
)

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

    async def register_agent(self, agent_id: str, ws: WebSocket, info: dict) -> AgentConnection:
        old = self.agents.get(agent_id)
        if old and old.websocket != ws:
            try:
                await old.websocket.close()
            except Exception:
                pass

        saved_alias = db.get_saved_alias(agent_id)
        reported_tid = str(info.get("tenant_id") or "").strip()
        final_tid = db.upsert_device(
            agent_id=agent_id,
            alias=saved_alias or info.get("alias") or f"{info.get('hostname', 'PC')} ({info.get('username', 'usuario')})",
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
            saved_alias=saved_alias,
            tenant_id=final_tid,
            tenant_name=tenant_names.get(final_tid, final_tid),
        )
        self.agents[agent_id] = conn

        mon_count = max(1, len([m for m in conn.monitors if m.get("id", 0) > 0]))
        log_entry = db.add_log(
            agent_id=agent_id,
            alias=conn.alias,
            hostname=conn.hostname,
            username=conn.username,
            category="NETWORK",
            message=f"Agente inició sesión correctamente ({conn.os_name} • IP {conn.local_ip} • {mon_count} monitor{'es' if mon_count > 1 else ''})",
        )

        await self.broadcast_agents_list()
        await self.broadcast_new_log(log_entry, tenant_id=conn.tenant_id)

        if self.has_viewers(agent_id):
            await self.send_to_agent(agent_id, {"type": "start_stream"})
        return conn

    async def unregister_agent(self, agent_id: str, ws: WebSocket):
        current = self.agents.get(agent_id)
        if current and current.websocket == ws:
            tid = current.tenant_id
            del self.agents[agent_id]
            db.set_device_offline(agent_id)
            log_entry = db.add_log(
                agent_id=agent_id,
                alias=current.alias,
                hostname=current.hostname,
                username=current.username,
                category="NETWORK",
                message="El equipo se desconectó del servidor central",
            )
            await self.broadcast_agents_list()
            await self.broadcast_new_log(log_entry, tenant_id=tid)
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
async def admin_login(request: Request):
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
    mode_lbl = "con orden de autodestrucción y revocación" if self_destruct else "desvinculado del panel"
    log_entry = db.add_log(
        agent_id=agent_id,
        alias=alias_lbl,
        hostname=live_agent.hostname if live_agent else "Equipo",
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

        agent_id = str(hello.get("agent_id") or f"{hello.get('hostname', 'PC')}-{hello.get('username', 'user')}")
        if db.is_device_revoked(agent_id):
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
                    agent.idle_seconds = int(data.get("idle_seconds", 0))
                    agent.active_window = data.get("active_window", "Escritorio")
                    if "monitors" in data:
                        agent.monitors = data["monitors"]
                    if "current_monitor" in data:
                        agent.current_monitor = int(data["current_monitor"])
                    if "thumbnail" in data and data["thumbnail"]:
                        agent.thumbnail_b64 = data["thumbnail"]

                    delta_secs = int(data.get("delta_seconds", 4))
                    app_name = data.get("app_name") or agent.active_window
                    away_alert = bool(data.get("away_alert", False))

                    db.record_telemetry_slice(
                        agent_id=agent_id,
                        delta_seconds=delta_secs,
                        is_active=(agent.idle_seconds < 180),
                        app_name=app_name,
                        increment_away_alert=away_alert,
                    )

                    for ev in data.get("log_events", []):
                        cat = ev.get("category", "WINDOW")
                        msg_txt = ev.get("message", "")
                        dur = ev.get("duration_seconds")
                        if msg_txt:
                            log_entry = db.add_log(
                                agent_id=agent_id,
                                alias=agent.alias,
                                hostname=agent.hostname,
                                username=agent.username,
                                category=cat,
                                message=msg_txt,
                                duration_seconds=dur,
                            )
                            await hub.broadcast_new_log(log_entry)

                    await hub.broadcast_telemetry_update(agent)

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
