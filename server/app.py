import argparse
import asyncio
import base64
import io
import json
import os
import re
import socket
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
from database import db
from models import AgentConnection, AliasUpdateRequest, ChatSendRequest, ClipboardSetRequest

app = FastAPI(title="JOB-Monitor — Centro de Monitoreo y Control Corporativo")
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))


class HubManager:
    def __init__(self):
        self.agents: Dict[str, AgentConnection] = {}
        self.admins: Set[WebSocket] = set()
        self.subscriptions: Dict[WebSocket, Optional[str]] = {}

    async def register_agent(self, agent_id: str, ws: WebSocket, info: dict) -> AgentConnection:
        old = self.agents.get(agent_id)
        if old and old.websocket != ws:
            try:
                await old.websocket.close()
            except Exception:
                pass

        saved_alias = db.get_saved_alias(agent_id)
        conn = AgentConnection(agent_id, ws, info, saved_alias=saved_alias)
        self.agents[agent_id] = conn

        db.upsert_device(
            agent_id=agent_id,
            alias=conn.alias,
            hostname=conn.hostname,
            username=conn.username,
            os_version=conn.os_name,
            local_ip=conn.local_ip,
            is_online=True,
        )

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
        await self.broadcast_new_log(log_entry)

        if self.has_viewers(agent_id):
            await self.send_to_agent(agent_id, {"type": "start_stream"})
        return conn

    async def unregister_agent(self, agent_id: str, ws: WebSocket):
        current = self.agents.get(agent_id)
        if current and current.websocket == ws:
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
            await self.broadcast_new_log(log_entry)
            await self.broadcast_to_viewers(
                agent_id,
                {"type": "agent_disconnected", "agent_id": agent_id},
            )

    def has_viewers(self, agent_id: str) -> bool:
        return any(sub_id == agent_id for sub_id in self.subscriptions.values())

    async def set_admin_subscription(self, admin_ws: WebSocket, new_agent_id: Optional[str]):
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

            log_entry = db.add_log(
                agent_id=agent.agent_id,
                alias=agent.alias,
                hostname=agent.hostname,
                username=agent.username,
                category="AUDIT",
                message="Administrador inició sesión de visualización silenciosa",
            )
            await self.broadcast_new_log(log_entry)

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
            await self.broadcast_new_log(log_entry)
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

    def _build_agents_payload(self) -> dict:
        known_devices = db.get_all_known_devices()
        offline_count = sum(1 for d in known_devices if d["agent_id"] not in self.agents)
        return {
            "type": "agents_list",
            "agents": [a.to_dict(include_thumbnail=True) for a in self.agents.values()],
            "known_devices": known_devices,
            "offline_count": offline_count,
        }

    async def broadcast_agents_list(self):
        payload = self._build_agents_payload()
        dead = []
        for ws in list(self.admins):
            try:
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
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.remove_admin(ws)

    async def broadcast_new_log(self, log_entry: dict):
        payload = {
            "type": "new_log",
            "log": log_entry,
        }
        await self.broadcast_to_admins(payload)

    async def broadcast_to_admins(self, payload: dict):
        dead = []
        for ws in list(self.admins):
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
        "index.html",
        {
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


def verify_admin_access(request: Request):
    """
    Micro-Función M1: Protege los endpoints HTTP REST mediante cabecera X-Admin-Password,
    parámetro query admin_pass o cookie ra_admin_pass para despliegue seguro en VPS.
    """
    supplied = (
        request.headers.get("x-admin-password")
        or request.query_params.get("admin_pass")
        or request.cookies.get("ra_admin_pass")
        or ""
    )
    if supplied != config.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="No autorizado: clave de administrador inválida")


@app.post("/api/login")
async def admin_login(request: Request):
    """Valida usuario y contraseña de administrador antes de desbloquear el panel."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if username != config.ADMIN_USERNAME or password != config.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    return {
        "ok": True,
        "username": config.ADMIN_USERNAME,
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


def generate_agent_config_payload(server_url: str, alias: str = "", token: str = "") -> dict:
    """
    Mini-Función A1: Genera el diccionario de configuración para config_agent.json.
    Omite agent_id para que cada PC Windows calcule su propio UUID único de hardware al iniciar.
    """
    payload = {
        "server_url": server_url,
        "token": (token or config.AGENT_TOKEN).strip(),
    }
    clean_alias = (alias or "").strip()
    if clean_alias:
        payload["alias"] = clean_alias
    return payload


def generate_install_readme(server_url: str, alias: str = "") -> str:
    """Genera las instrucciones de instalación incluidas dentro del paquete .zip."""
    alias_line = alias.strip() if alias.strip() else "(Automático según nombre de usuario y equipo)"
    return (
        "====================================================================\r\n"
        "  JOB-Monitor — Paquete Instalador de Agente Corporativo (Windows)\r\n"
        "====================================================================\r\n\r\n"
        f"Servidor configurado : {server_url}\r\n"
        f"Alias preconfigurado : {alias_line}\r\n\r\n"
        "REQUISITOS PREVIOS EN LA PC WINDOWS:\r\n"
        "1. Tener instalado Python 3.10 o superior desde https://www.python.org/downloads/\r\n"
        "   (marcando la casilla 'Add python.exe to PATH' durante la instalación).\r\n\r\n"
        "PASOS DE INSTALACIÓN:\r\n"
        "1. Extrae todos los archivos de este ZIP en una carpeta temporal.\r\n"
        "2. Haz clic derecho sobre 'install_windows.bat' y selecciona\r\n"
        "   'Ejecutar como administrador' (o doble clic).\r\n"
        "3. El instalador copiará el agente a C:\\ProgramData\\MonitoreoEmpresa,\r\n"
        "   instalará las librerías necesarias, registrará el inicio automático\r\n"
        "   con pythonw.exe y conectará el equipo de inmediato al panel.\r\n\r\n"
        "DESINSTALACIÓN:\r\n"
        "- Ejecuta 'uninstall_windows.bat' para detener el agente y eliminar\r\n"
        "  la tarea programada y los archivos del equipo.\r\n"
    )


def build_agent_package_zip(server_url: str, alias: str = "") -> bytes:
    """
    Mini-Función A2: Empaqueta en memoria (ZIP) el agente, dependencias,
    config_agent.json preconfigurado y scripts de instalación para Windows.
    """
    agent_dir = config.PROJECT_ROOT / "agent"
    cfg_payload = generate_agent_config_payload(server_url=server_url, alias=alias)
    cfg_json_str = json.dumps(cfg_payload, indent=2, ensure_ascii=False)
    readme_str = generate_install_readme(server_url=server_url, alias=alias)

    mem_zip = io.BytesIO()
    folder_prefix = "JOB-Monitor-Agent"

    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in ("agent.py", "requirements-agent.txt", "install_windows.bat", "uninstall_windows.bat"):
            fpath = agent_dir / fname
            if fpath.exists():
                zf.write(fpath, arcname=f"{folder_prefix}/{fname}")

        zf.writestr(f"{folder_prefix}/config_agent.json", cfg_json_str)
        zf.writestr(f"{folder_prefix}/LEEME_INSTALACION.txt", readme_str)

    mem_zip.seek(0)
    return mem_zip.read()


@app.get("/api/agent-package-info")
async def get_agent_package_info(request: Request):
    """Devuelve la URL WebSocket sugerida para prellenar el modal de descarga de agente."""
    verify_admin_access(request)
    return {
        "ok": True,
        "default_ws_url": resolve_default_ws_url(request),
        "lan_ip": get_local_lan_ip(),
        "port": config.PORT,
    }


@app.get("/api/download-agent")
async def download_agent_package(
    request: Request,
    server_url: Optional[str] = Query(None, description="URL WebSocket del servidor central"),
    alias: Optional[str] = Query("", description="Alias opcional preconfigurado para la PC"),
):
    """
    Mini-Función A3: Endpoint HTTP que construye y descarga el paquete .zip
    preconfigurado para instalar el agente en Windows.
    """
    verify_admin_access(request)
    default_ws = resolve_default_ws_url(request)
    final_ws_url = normalize_ws_url(server_url or "", default_ws)
    clean_alias = (alias or "").strip()

    zip_bytes = build_agent_package_zip(server_url=final_ws_url, alias=clean_alias)

    safe_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", clean_alias).strip("-") if clean_alias else "Windows"
    filename = f"JOB-Monitor-Agent-{safe_slug}.zip"

    alias_info = f"con alias '{clean_alias}'" if clean_alias else "con alias automático"
    log_entry = db.add_log(
        agent_id="SYSTEM-SERVER",
        alias="Consola de Administración",
        hostname="Servidor Central",
        username="Administrador",
        category="AUDIT",
        message=f"Paquete instalador '{filename}' generado ({alias_info} -> {final_ws_url})",
    )
    await hub.broadcast_new_log(log_entry)

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
    verify_admin_access(request)
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
    verify_admin_access(request)
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
        message=f"Archivo '{safe_filename}' ({size_kb} KB) enviado a {dest_label} ({open_note})",
    )
    await hub.broadcast_new_log(log_entry)

    return {
        "ok": True,
        "agent_id": agent_id,
        "filename": safe_filename,
        "size_bytes": len(raw_bytes),
        "destination": dest_clean,
        "auto_open": should_open,
    }


@app.get("/api/metrics")
async def get_metrics(request: Request, date: Optional[str] = Query(None, description="Fecha YYYY-MM-DD")):
    verify_admin_access(request)
    metrics = db.get_daily_metrics(date_str=date)
    # Adjuntar estado en vivo de los agentes actualmente conectados
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
):
    verify_admin_access(request)
    logs = db.query_logs(agent_id=agent_id, category=category, search_q=q, limit=limit)
    devices = db.get_all_known_devices()
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
):
    verify_admin_access(request)
    csv_text = db.export_logs_csv(agent_id=agent_id, category=category, search_q=q)
    return Response(
        content="\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="bitacora_equipos_job_monitor.csv"'
        },
    )


@app.post("/api/devices/{agent_id}/alias")
async def update_device_alias(request: Request, agent_id: str, body: AliasUpdateRequest):
    verify_admin_access(request)
    if not body.alias.strip():
        raise HTTPException(status_code=400, detail="El alias no puede estar vacío")
    await hub.update_alias(agent_id, body.alias)
    return {"ok": True, "agent_id": agent_id, "alias": body.alias.strip()}


@app.websocket("/ws/agent")
async def ws_agent_endpoint(websocket: WebSocket):
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
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
        auth = json.loads(raw)
        supplied_user = str(auth.get("username") or config.ADMIN_USERNAME).strip()
        if (
            auth.get("type") != "auth"
            or supplied_user != config.ADMIN_USERNAME
            or auth.get("password") != config.ADMIN_PASSWORD
        ):
            await websocket.send_json({"type": "auth_error", "message": "Credenciales de administrador inválidas"})
            await websocket.close(code=1008)
            return

        authenticated = True
        hub.admins.add(websocket)
        hub.subscriptions[websocket] = None

        init_payload = hub._build_agents_payload()
        init_payload["type"] = "auth_ok"
        await websocket.send_json(init_payload)

        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if "text" in message and message["text"] is not None:
                data = json.loads(message["text"])
                msg_type = data.get("type")

                if msg_type == "subscribe":
                    target_id = data.get("agent_id")
                    await hub.set_admin_subscription(websocket, target_id)

                elif msg_type == "unsubscribe":
                    await hub.set_admin_subscription(websocket, None)

                elif msg_type == "set_alias":
                    target_id = data.get("agent_id")
                    new_alias = data.get("alias", "")
                    if target_id and new_alias:
                        await hub.update_alias(target_id, new_alias)

                elif msg_type == "audit_control_mode":
                    target_id = hub.subscriptions.get(websocket)
                    enabled = bool(data.get("enabled", False))
                    if target_id and target_id in hub.agents:
                        ag = hub.agents[target_id]
                        action_text = (
                            "Administrador activó el control remoto de mouse y teclado"
                            if enabled
                            else "Administrador cambió a modo Solo Observar"
                        )
                        log_entry = db.add_log(
                            agent_id=ag.agent_id,
                            alias=ag.alias,
                            hostname=ag.hostname,
                            username=ag.username,
                            category="AUDIT",
                            message=action_text,
                        )
                        await hub.broadcast_new_log(log_entry)

                elif msg_type == "request_all_thumbnails":
                    for ag_id in list(hub.agents.keys()):
                        await hub.send_to_agent(ag_id, {"type": "capture_thumbnail"})

                elif msg_type == "chat_send":
                    target_id = data.get("agent_id") or hub.subscriptions.get(websocket)
                    text = str(data.get("text", "")).strip()
                    if target_id and text and target_id in hub.agents:
                        ag = hub.agents[target_id]
                        chat_entry = db.add_chat_message(
                            agent_id=target_id,
                            sender="admin",
                            sender_name="Soporte / Administrador",
                            message=text,
                        )
                        await hub.send_to_agent(
                            target_id,
                            {
                                "type": "chat_message",
                                "sender": "Soporte / Administrador",
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
                            message=f"Administrador envió mensaje directo: '{text[:120]}'",
                        )
                        await hub.broadcast_to_admins({
                            "type": "chat_message",
                            "chat": chat_entry,
                        })
                        await hub.broadcast_new_log(log_entry)

                elif msg_type == "clipboard_get":
                    target_id = data.get("agent_id") or hub.subscriptions.get(websocket)
                    if target_id:
                        await hub.send_to_agent(target_id, {"type": "clipboard_get"})

                elif msg_type == "clipboard_set":
                    target_id = data.get("agent_id") or hub.subscriptions.get(websocket)
                    clip_text = str(data.get("text", ""))
                    auto_paste = bool(data.get("auto_paste", False))
                    if target_id and target_id in hub.agents:
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
                            message=f"Administrador envió texto al portapapeles remoto ({paste_note})",
                        )
                        await hub.broadcast_new_log(log_entry)

                elif msg_type == "audio_chunk":
                    target_id = data.get("agent_id") or hub.subscriptions.get(websocket)
                    wav_b64 = data.get("wav_b64", "")
                    if target_id and wav_b64:
                        await hub.send_to_agent(
                            target_id,
                            {
                                "type": "play_audio",
                                "wav_b64": wav_b64,
                            },
                        )

                elif msg_type == "audit_ptt_start":
                    target_id = data.get("agent_id") or hub.subscriptions.get(websocket)
                    if target_id and target_id in hub.agents:
                        ag = hub.agents[target_id]
                        log_entry = db.add_log(
                            agent_id=ag.agent_id,
                            alias=ag.alias,
                            hostname=ag.hostname,
                            username=ag.username,
                            category="AUDIT",
                            message="Administrador transmitió voz en directo (Push-to-Talk) hacia el equipo",
                        )
                        await hub.broadcast_new_log(log_entry)

                else:
                    target_id = data.get("agent_id") or hub.subscriptions.get(websocket)
                    if target_id:
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
