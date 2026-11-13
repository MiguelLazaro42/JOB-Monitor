import argparse
import asyncio
import base64
import ctypes
import ctypes.util
import getpass
import hashlib
import hmac
import io
import json
import os
import platform
import queue
import re
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import warnings
import zlib
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

warnings.filterwarnings("ignore", category=DeprecationWarning, module="mss")

import mss
import websockets
from PIL import Image, ImageDraw
from pynput.keyboard import Controller as KeyboardController, Key, KeyCode, Listener
from pynput.mouse import Button, Controller as MouseController

try:
    import pyperclip
except Exception:
    pyperclip = None

if platform.system() == "Windows":
    try:
        import winsound
    except Exception:
        winsound = None
else:
    winsound = None

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config_agent.json"
OFFLINE_VAULT_DIR = BASE_DIR / "offline_vault"

# Clave Pública Asimétrica RSA-2048 de SITAI para Cifrado Unidireccional de Bóveda Offline (.sitai)
# El cliente SOLO posee (SITAI_RSA_PUB_N, SITAI_RSA_PUB_E) y jamás puede descifrar los archivos .sitai.
# Únicamente el servidor central posee el exponente privado D necesario para abrirlos.
SITAI_MAGIC_HEADER = b"SITAI01\x00"
SITAI_RSA_PUB_E = 65537
SITAI_RSA_PUB_N = int(
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
SITAI_VAULT_MAX_FILES = 350
SITAI_MAX_OFFLINE_DAYS = 30
SITAI_MAX_OFFLINE_SECONDS = SITAI_MAX_OFFLINE_DAYS * 86400  # 30 días (2,592,000 segundos)


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


def encrypt_sitai_packet(payload_dict: dict) -> bytes:
    """
    Comprime (zlib) y cifra un diccionario de evento con una llave efímera aleatoria de 256 bits,
    encapsulando la semilla mediante RSA-2048 (PKCS#1 v1.5) con la clave pública del servidor.
    """
    plain_json = json.dumps(payload_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(plain_json, level=6)

    master_seed = os.urandom(32)
    nonce = os.urandom(16)
    enc_key = hashlib.sha256(master_seed + b":sitai-enc:" + nonce).digest()
    mac_key = hashlib.sha256(master_seed + b":sitai-mac:" + nonce).digest()

    ciphertext = _sitai_keystream_xor(compressed, enc_key, nonce)
    mac_tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()

    # Relleno aleatorio no nulo tipo PKCS#1 v1.5 para bloque de 256 bytes (2048 bits)
    pad_len = 256 - 3 - len(master_seed)
    pad = bytearray()
    while len(pad) < pad_len:
        b = os.urandom(pad_len - len(pad))
        pad.extend(x for x in b if x != 0)
    padded_block = b"\x00\x02" + bytes(pad[:pad_len]) + b"\x00" + master_seed
    m_int = int.from_bytes(padded_block, "big")
    c_int = pow(m_int, SITAI_RSA_PUB_E, SITAI_RSA_PUB_N)
    rsa_cipher = c_int.to_bytes(256, "big")

    return SITAI_MAGIC_HEADER + rsa_cipher + nonce + mac_tag + ciphertext


DEFAULT_SERVER_URL = os.environ.get("RA_SERVER_URL", "ws://127.0.0.1:5080/ws/agent")
DEFAULT_TOKEN = os.environ.get("RA_AGENT_TOKEN", "ra-secret-token-2026")

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"


def hide_macos_dock_icon():
    """
    Oculta completamente el ícono de Python del Dock y del selector Cmd+Tab en macOS
    usando TransformProcessType de ApplicationServices (nivel WindowServer, sin tocar NSApplication).
    """
    if not IS_MAC:
        return
    try:
        lib_path = ctypes.util.find_library("ApplicationServices")
        if lib_path:
            app_services = ctypes.cdll.LoadLibrary(lib_path)

            class ProcessSerialNumber(ctypes.Structure):
                _fields_ = [("highLongOfPSN", ctypes.c_uint32), ("lowLongOfPSN", ctypes.c_uint32)]

            psn = ProcessSerialNumber(0, 2)  # kCurrentProcess = 2
            # kProcessTransformToUIElementApplication = 4
            app_services.TransformProcessType(ctypes.byref(psn), 4)
    except Exception:
        pass


hide_macos_dock_icon()

# 1. Corrección obligatoria de Escalado DPI en Windows (125% / 150% / Multi-monitor)
if IS_WINDOWS:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# 2. Lectura de Inactividad y Ventana Activa (API nativa de Windows / macOS)
if IS_WINDOWS:
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint),
            ("dwTime", ctypes.c_uint),
        ]

    def get_idle_seconds() -> int:
        try:
            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                return max(0, int(millis / 1000))
        except Exception:
            pass
        return 0

    def get_active_window_title() -> str:
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return "Escritorio"
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return "Escritorio"
            buff = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value.strip()
            return title[:95] if title else "Escritorio"
        except Exception:
            return "Escritorio"

    def get_windows_session_context() -> tuple[Optional[int], str, bool]:
        """
        Obtiene (session_id, username_real, is_background_session) de la sesión interactiva de Windows
        usando WTSQuerySessionInformationW, incluso cuando el proceso fue iniciado con token UAC elevado.
        """
        sess_id_val: Optional[int] = None
        real_user = ""
        is_bg = False
        try:
            sess_dword = ctypes.c_uint32(0)
            if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sess_dword)):
                sess_id_val = int(sess_dword.value)
                wts = ctypes.windll.wtsapi32
                buf_ptr = ctypes.c_void_p()
                bytes_ret = ctypes.c_uint32(0)
                # WTSUserName = 5
                if wts.WTSQuerySessionInformationW(0, sess_id_val, 5, ctypes.byref(buf_ptr), ctypes.byref(bytes_ret)):
                    if buf_ptr.value:
                        real_user = ctypes.wstring_at(buf_ptr.value).strip()
                    wts.WTSFreeMemory(buf_ptr)
                # WTSConnectState = 8 (0 == WTSActive)
                buf_state = ctypes.c_void_p()
                if wts.WTSQuerySessionInformationW(0, sess_id_val, 8, ctypes.byref(buf_state), ctypes.byref(bytes_ret)):
                    if buf_state.value:
                        state_code = ctypes.cast(buf_state.value, ctypes.POINTER(ctypes.c_int)).contents.value
                        is_bg = state_code != 0
                    wts.WTSFreeMemory(buf_state)
        except Exception:
            pass
        if not real_user:
            try:
                real_user = getpass.getuser()
            except Exception:
                real_user = "usuario"
        return sess_id_val, real_user, is_bg

elif IS_MAC:
    def get_idle_seconds() -> int:
        try:
            out = subprocess.check_output(
                ["/usr/sbin/ioreg", "-c", "IOHIDSystem"],
                stderr=subprocess.DEVNULL,
                timeout=1.5,
            ).decode("utf-8", errors="ignore")
            for line in out.splitlines():
                if "HIDIdleTime" in line:
                    ns = int(line.split("=")[-1].strip())
                    return max(0, int(ns / 1_000_000_000))
        except Exception:
            pass
        return 0

    _OBJC_LIB = None
    _MSG_SEND_VOID = None
    _MSG_SEND_UTF8 = None
    _CLS_NSWORKSPACE = None
    _SEL_SHARED_WS = None
    _SEL_FRONTMOST_APP = None
    _SEL_LOCALIZED_NAME = None
    _SEL_UTF8_STRING = None

    def _init_macos_objc_workspace():
        global _OBJC_LIB, _MSG_SEND_VOID, _MSG_SEND_UTF8, _CLS_NSWORKSPACE
        global _SEL_SHARED_WS, _SEL_FRONTMOST_APP, _SEL_LOCALIZED_NAME, _SEL_UTF8_STRING
        if _OBJC_LIB is not None:
            return
        try:
            objc_path = ctypes.util.find_library("objc")
            if not objc_path:
                return
            ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
            objc = ctypes.cdll.LoadLibrary(objc_path)
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            _MSG_SEND_VOID = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
            _MSG_SEND_UTF8 = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
            _CLS_NSWORKSPACE = objc.objc_getClass(b"NSWorkspace")
            _SEL_SHARED_WS = objc.sel_registerName(b"sharedWorkspace")
            _SEL_FRONTMOST_APP = objc.sel_registerName(b"frontmostApplication")
            _SEL_LOCALIZED_NAME = objc.sel_registerName(b"localizedName")
            _SEL_UTF8_STRING = objc.sel_registerName(b"UTF8String")
            _OBJC_LIB = objc
        except Exception:
            _OBJC_LIB = False

    def get_active_window_title() -> str:
        try:
            _init_macos_objc_workspace()
            if _OBJC_LIB and _MSG_SEND_VOID and _MSG_SEND_UTF8:
                ws = _MSG_SEND_VOID(_CLS_NSWORKSPACE, _SEL_SHARED_WS)
                if ws:
                    front = _MSG_SEND_VOID(ws, _SEL_FRONTMOST_APP)
                    if front:
                        ns_name = _MSG_SEND_VOID(front, _SEL_LOCALIZED_NAME)
                        if ns_name:
                            raw = _MSG_SEND_UTF8(ns_name, _SEL_UTF8_STRING)
                            if raw:
                                decoded = raw.decode("utf-8", errors="ignore").strip()
                                if decoded:
                                    return decoded[:95]
        except Exception:
            pass
        return "macOS Finder"

    def check_macos_screen_permission(request_if_missing: bool = False) -> bool:
        try:
            cg = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
            if hasattr(cg, 'CGPreflightScreenCaptureAccess'):
                cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
                granted = cg.CGPreflightScreenCaptureAccess()
                if not granted and request_if_missing and hasattr(cg, 'CGRequestScreenCaptureAccess'):
                    cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
                    cg.CGRequestScreenCaptureAccess()
                return bool(granted)
        except Exception:
            pass
        return True

    def check_macos_accessibility_permission(request_if_missing: bool = False) -> bool:
        try:
            app_services = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
            app_services.AXIsProcessTrusted.restype = ctypes.c_bool
            trusted = bool(app_services.AXIsProcessTrusted())
            if not trusted and request_if_missing:
                try:
                    core_foundation = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
                    kAXTrustedCheckOptionPrompt = ctypes.c_void_p.in_dll(app_services, 'kAXTrustedCheckOptionPrompt')
                    kCFBooleanTrue = ctypes.c_void_p.in_dll(core_foundation, 'kCFBooleanTrue')
                    CFDictionaryCreate = core_foundation.CFDictionaryCreate
                    CFDictionaryCreate.restype = ctypes.c_void_p
                    CFDictionaryCreate.argtypes = [
                        ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(ctypes.c_void_p),
                        ctypes.c_long,
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                    ]
                    keys = (ctypes.c_void_p * 1)(kAXTrustedCheckOptionPrompt)
                    values = (ctypes.c_void_p * 1)(kCFBooleanTrue)
                    kCFTypeDictionaryKeyCallBacks = ctypes.c_void_p.in_dll(core_foundation, 'kCFTypeDictionaryKeyCallBacks')
                    kCFTypeDictionaryValueCallBacks = ctypes.c_void_p.in_dll(core_foundation, 'kCFTypeDictionaryValueCallBacks')
                    options = CFDictionaryCreate(None, keys, values, 1, ctypes.byref(kCFTypeDictionaryKeyCallBacks), ctypes.byref(kCFTypeDictionaryValueCallBacks))
                    ax_func = getattr(app_services, 'AXIsProcessTrustedWithOptions', None)
                    if ax_func:
                        ax_func.restype = ctypes.c_bool
                        ax_func.argtypes = [ctypes.c_void_p]
                        trusted = bool(ax_func(options))
                except Exception:
                    pass
            return trusted
        except Exception:
            return False

    def get_windows_session_context() -> tuple[Optional[int], str, bool]:
        try:
            u = getpass.getuser()
        except Exception:
            u = "usuario"
        return None, u, False

else:
    def get_idle_seconds() -> int:
        return 0

    def get_active_window_title() -> str:
        return platform.system()

    def check_macos_screen_permission(request_if_missing: bool = False) -> bool:
        return True

    def check_macos_accessibility_permission(request_if_missing: bool = False) -> bool:
        return True

    def get_windows_session_context() -> tuple[Optional[int], str, bool]:
        try:
            u = getpass.getuser()
        except Exception:
            u = "usuario"
        return None, u, False


def build_macos_permission_frame(width: int = 1280, height: int = 720) -> Image.Image:
    """
    Genera un cuadro informativo nítido cuando macOS aún no tiene activado el interruptor
    de Grabación de Pantalla y Audio del Sistema, evitando mostrar una pantalla gris vacía.
    """
    w = max(640, min(1920, int(width)))
    h = max(360, min(1080, int(height)))
    img = Image.new("RGB", (w, h), (15, 29, 44))
    draw = ImageDraw.Draw(img)

    card_w = min(w - 80, 860)
    card_h = min(h - 80, 340)
    x0 = (w - card_w) // 2
    y0 = (h - card_h) // 2
    x1 = x0 + card_w
    y1 = y0 + card_h

    draw.rectangle([x0, y0, x1, y1], fill=(23, 45, 66), outline=(56, 189, 248), width=2)
    draw.rectangle([x0, y0, x1, y0 + 48], fill=(19, 52, 71))

    lines = [
        ("SITAI JOB-Monitor  |  Permiso requerido en este Mac", (125, 211, 252), y0 + 16),
        ("Para ver el escritorio en vivo y evitar pantalla gris, autoriza en el Mac:", (255, 255, 255), y0 + 74),
        ("1. Abre Ajustes del Sistema -> Privacidad y Seguridad", (226, 232, 240), y0 + 120),
        ("2. Entra en 'Grabacion de pantalla y audio del sistema'", (226, 232, 240), y0 + 154),
        ("3. Activa el interruptor de 'JOB-Monitor Agent' (y tambien en 'Accesibilidad')", (52, 211, 153), y0 + 188),
        ("La senal de video en vivo iniciara automaticamente en 3 segundos tras activarlo.", (148, 163, 184), y0 + 244),
    ]
    for text, color, py in lines:
        if py + 20 < y1:
            draw.text((x0 + 32, py), text, fill=color)
    return img


def build_windows_background_session_frame(
    width: int = 1280,
    height: int = 720,
    username: str = "usuario",
    session_id: Optional[int] = None,
) -> Image.Image:
    """
    Genera un cuadro informativo nítido cuando una sesión de usuario de Windows se encuentra
    en segundo plano (Cambio rápido de usuario / Sesión bloqueada sin GPU activa).
    """
    w = max(640, min(1920, int(width)))
    h = max(360, min(1080, int(height)))
    img = Image.new("RGB", (w, h), (15, 23, 42))
    draw = ImageDraw.Draw(img)

    card_w = min(w - 80, 820)
    card_h = min(h - 80, 290)
    x0 = (w - card_w) // 2
    y0 = (h - card_h) // 2
    x1 = x0 + card_w
    y1 = y0 + card_h

    draw.rectangle([x0, y0, x1, y1], fill=(30, 41, 59), outline=(96, 165, 250), width=2)
    draw.rectangle([x0, y0, x1, y0 + 48], fill=(15, 32, 57))

    sess_lbl = f" (Sesion ID {session_id})" if session_id is not None else ""
    lines = [
        (f"SITAI JOB-Monitor  |  Sesion de Windows en 2o Plano{sess_lbl}", (147, 197, 253), y0 + 16),
        (f"El usuario '{username}' tiene la sesion iniciada pero no esta en pantalla.", (255, 255, 255), y0 + 76),
        ("Causas habituales en Windows Multi-Usuario:", (203, 213, 225), y0 + 122),
        ("- Otro usuario de este equipo tiene activa la consola fisica (Cambio de usuario).", (148, 163, 184), y0 + 154),
        ("- O bien la pantalla de inicio de sesion de Windows (Winlogon) esta bloqueada.", (148, 163, 184), y0 + 184),
        ("El video en vivo se reanudara automaticamente en cuanto esta sesion vuelva al frente.", (52, 211, 153), y0 + 234),
    ]
    for text, color, py in lines:
        if py + 20 < y1:
            draw.text((x0 + 32, py), text, fill=color)
    return img


_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")


def strip_zero_width(text: str) -> str:
    return _ZERO_WIDTH_RE.sub("", text or "")


def extract_app_name(window_title: str) -> str:
    title = strip_zero_width(window_title or "Escritorio").strip() or "Escritorio"
    if " - " in title:
        parts = [p.strip() for p in title.split(" - ") if p.strip()]
        if parts:
            return parts[-1][:45]
    if " — " in title:
        parts = [p.strip() for p in title.split(" — ") if p.strip()]
        if parts:
            return parts[-1][:45]
    return title[:45]


def format_duration_es(seconds: int) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    mins, secs = divmod(s, 60)
    if mins < 60:
        return f"{mins}m {secs}s" if secs else f"{mins}m"
    hrs, rem_mins = divmod(mins, 60)
    return f"{hrs}h {rem_mins}m"


SPECIAL_KEYS = {
    "Enter": Key.enter,
    "Backspace": Key.backspace,
    "Tab": Key.tab,
    "Escape": Key.esc,
    "Delete": Key.delete,
    " ": Key.space,
    "Space": Key.space,
    "ArrowUp": Key.up,
    "ArrowDown": Key.down,
    "ArrowLeft": Key.left,
    "ArrowRight": Key.right,
    "Home": Key.home,
    "End": Key.end,
    "PageUp": Key.page_up,
    "PageDown": Key.page_down,
    "Control": Key.ctrl,
    "Shift": Key.shift,
    "Alt": Key.alt,
    "Meta": Key.cmd,
    "CapsLock": Key.caps_lock,
    "F1": Key.f1,
    "F2": Key.f2,
    "F3": Key.f3,
    "F4": Key.f4,
    "F5": Key.f5,
    "F6": Key.f6,
    "F7": Key.f7,
    "F8": Key.f8,
    "F9": Key.f9,
    "F10": Key.f10,
    "F11": Key.f11,
    "F12": Key.f12,
}


def generate_hardware_agent_id(hostname: str, username: str) -> str:
    mac_num = uuid.getnode()
    raw = f"{mac_num}-{hostname}-{username}"
    short_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    inst_suffix = uuid.uuid4().hex[:4]
    return f"{hostname.upper()}-{short_hash}-{inst_suffix}"


def _sanitize_user_slug(username: str) -> str:
    slug = "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in (username or "user").lower()).strip("_")
    return slug or "user"


def load_or_create_agent_config(cli_server: Optional[str], cli_token: Optional[str], cli_alias: Optional[str]) -> dict:
    hostname = socket.gethostname()
    sess_id, username, _ = get_windows_session_context()
    safe_user = _sanitize_user_slug(username)
    user_cfg_path = BASE_DIR / f"config_agent_{safe_user}.json"

    shared_loaded: dict = {}
    if CONFIG_PATH.exists():
        try:
            shared_loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            shared_loaded = {}

    user_loaded: dict = {}
    if user_cfg_path.exists():
        try:
            user_loaded = json.loads(user_cfg_path.read_text(encoding="utf-8"))
        except Exception:
            user_loaded = {}

    default_alias = f"PC de {username} ({hostname})"
    cfg = {
        "agent_id": generate_hardware_agent_id(hostname, username),
        "alias": default_alias,
        "server_url": str(shared_loaded.get("server_url") or DEFAULT_SERVER_URL),
        "token": str(shared_loaded.get("token") or DEFAULT_TOKEN),
        "tenant_id": str(shared_loaded.get("tenant_id") or ""),
        "owner_username": username,
    }
    for k in ("mtls_cert", "mtls_key"):
        if shared_loaded.get(k):
            cfg[k] = shared_loaded[k]

    if user_loaded.get("agent_id"):
        cfg["agent_id"] = user_loaded["agent_id"]
        cfg["alias"] = user_loaded.get("alias") or shared_loaded.get("alias") or default_alias
    else:
        shared_owner = str(shared_loaded.get("owner_username") or "").strip()
        if shared_loaded.get("agent_id") and (not shared_owner or shared_owner.lower() == username.lower()):
            cfg["agent_id"] = shared_loaded["agent_id"]
            cfg["alias"] = shared_loaded.get("alias") or default_alias
            shared_loaded["owner_username"] = username
            try:
                CONFIG_PATH.write_text(json.dumps(shared_loaded, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
        elif shared_loaded.get("alias"):
            other_auto = f"PC de {shared_owner} ({hostname})" if shared_owner else ""
            if shared_loaded["alias"] != other_auto:
                cfg["alias"] = shared_loaded["alias"]

    if cli_server:
        cfg["server_url"] = cli_server
    if cli_token:
        cfg["token"] = cli_token
    if cli_alias:
        cfg["alias"] = cli_alias

    try:
        user_cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    if not CONFIG_PATH.exists():
        try:
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    cfg["_user_config_path"] = str(user_cfg_path)
    cfg["_session_id"] = sess_id
    cfg["_username"] = username
    return cfg


def read_system_clipboard() -> str:
    if pyperclip is not None:
        try:
            val = pyperclip.paste()
            if val is not None:
                return str(val)
        except Exception:
            pass
    if IS_MAC:
        try:
            return subprocess.check_output(["pbpaste"], timeout=1.5).decode("utf-8", errors="ignore")
        except Exception:
            pass
    elif IS_WINDOWS:
        try:
            return subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).decode("utf-8", errors="ignore").rstrip("\r\n")
        except Exception:
            pass
    return ""


def write_system_clipboard(text: str) -> bool:
    if pyperclip is not None:
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            pass
    if IS_MAC:
        try:
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            p.communicate(input=text.encode("utf-8"), timeout=1.5)
            return True
        except Exception:
            pass
    elif IS_WINDOWS:
        try:
            p = subprocess.Popen(
                ["clip"],
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            p.communicate(input=text.encode("utf-16le"), timeout=2.0)
            return True
        except Exception:
            pass
    return False


class AudioPlayerWorker:
    """Reproduce paquetes WAV recibidos por Push-to-Talk en orden secuencial sin bloquear el bucle principal."""

    def __init__(self):
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=30)
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def enqueue_wav(self, wav_bytes: bytes):
        if not wav_bytes:
            return
        try:
            self._queue.put_nowait(wav_bytes)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(wav_bytes)
            except Exception:
                pass

    def _worker(self):
        while True:
            wav_bytes = self._queue.get()
            try:
                if IS_WINDOWS and winsound is not None:
                    winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
                elif IS_MAC:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                        tmp.write(wav_bytes)
                        tmp_path = tmp.name
                    try:
                        subprocess.run(["afplay", tmp_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except Exception:
                            pass
            except Exception:
                pass
            finally:
                self._queue.task_done()


class PopupChatManager:
    """
    Ventana emergente de chat bajo demanda (tkinter topmost).
    Permanece 100% inactiva e invisible mientras el administrador solo observa.
    Solo se abre cuando el administrador envía un mensaje de chat al empleado.
    """

    def __init__(self, on_reply_callback: Callable[[str], None]):
        self.on_reply = on_reply_callback
        self._cmd_queue: "queue.Queue[dict]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def show_incoming_message(self, sender: str, text: str, timestamp: str = ""):
        if IS_MAC:
            threading.Thread(
                target=self._show_macos_native_dialog,
                args=(sender, text),
                daemon=True,
            ).start()
            return
        if IS_WINDOWS and winsound is not None:
            try:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run_tk_loop, daemon=True)
                self._thread.start()
        self._cmd_queue.put({
            "action": "incoming",
            "sender": sender,
            "text": text,
            "timestamp": timestamp or datetime.now().strftime("%H:%M:%S"),
        })

    def _show_macos_native_dialog(self, sender: str, text: str):
        safe_msg = str(text or "").replace("\\", "\\\\").replace('"', '\\"')
        safe_sender = str(sender or "Soporte").replace("\\", "\\\\").replace('"', '\\"')
        script = (
            f'set dlg to display dialog "{safe_sender}:\\n\\n{safe_msg}\\n\\nEscribe tu respuesta (opcional):" '
            f'default answer "" with title "SITAI JOB-Monitor — Asistencia" '
            f'buttons {{"Cerrar", "Enviar"}} default button "Enviar"\n'
            f'if button returned of dlg is "Enviar" then\n'
            f'    return text returned of dlg\n'
            f'end if\n'
            f'return ""'
        )
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=300,
            )
            reply = (proc.stdout or "").strip()
            if reply:
                self.on_reply(reply)
        except Exception:
            pass

    def _run_tk_loop(self):
        try:
            import tkinter as tk
            from tkinter import scrolledtext
        except Exception:
            return

        try:
            root = tk.Tk()
            root.title("JOB-Monitor — Soporte Técnico Corporativo")
            root.geometry("430x380")
            root.configure(bg="#f8f9fb")
            root.attributes("-topmost", True)

            header = tk.Frame(root, bg="#ffffff", padx=14, pady=10, highlightthickness=1, highlightbackground="#e5e7eb")
            header.pack(fill=tk.X)

            title_lbl = tk.Label(
                header,
                text="Asistencia y Soporte Corporativo",
                bg="#ffffff",
                fg="#111827",
                font=("Segoe UI", 11, "bold"),
                anchor="w",
            )
            title_lbl.pack(fill=tk.X)

            sub_lbl = tk.Label(
                header,
                text="El administrador te ha enviado un mensaje directo. Puedes responder abajo.",
                bg="#ffffff",
                fg="#6b7280",
                font=("Segoe UI", 8),
                anchor="w",
            )
            sub_lbl.pack(fill=tk.X)

            chat_area = scrolledtext.ScrolledText(
                root,
                wrap=tk.WORD,
                state="disabled",
                bg="#ffffff",
                fg="#111827",
                font=("Segoe UI", 10),
                padx=10,
                pady=10,
                relief=tk.SOLID,
                bd=1,
                height=13,
            )
            chat_area.pack(fill=tk.BOTH, expand=True, padx=12, pady=(10, 6))
            chat_area.tag_config("admin_tag", foreground="#0066cc", font=("Segoe UI", 9, "bold"))
            chat_area.tag_config("user_tag", foreground="#047857", font=("Segoe UI", 9, "bold"))
            chat_area.tag_config("body_tag", foreground="#111827", font=("Segoe UI", 10))

            bottom_frame = tk.Frame(root, bg="#f8f9fb", padx=12, pady=10)
            bottom_frame.pack(fill=tk.X, side=tk.BOTTOM)

            entry = tk.Entry(
                bottom_frame,
                bg="#ffffff",
                fg="#111827",
                insertbackground="#111827",
                font=("Segoe UI", 10),
                relief=tk.SOLID,
                bd=1,
            )
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 8))

            def append_line(who_label: str, body: str, is_admin: bool):
                chat_area.configure(state="normal")
                tag = "admin_tag" if is_admin else "user_tag"
                chat_area.insert(tk.END, f"{who_label}:\n", tag)
                chat_area.insert(tk.END, f"{body}\n\n", "body_tag")
                chat_area.see(tk.END)
                chat_area.configure(state="disabled")

            def send_reply(event=None):
                txt = entry.get().strip()
                if not txt:
                    return
                entry.delete(0, tk.END)
                now_hm = datetime.now().strftime("%H:%M")
                append_line(f"Tú ({now_hm})", txt, is_admin=False)
                try:
                    self.on_reply(txt)
                except Exception:
                    pass

            send_btn = tk.Button(
                bottom_frame,
                text="Enviar",
                command=send_reply,
                bg="#0066cc",
                fg="#ffffff",
                activebackground="#0052a3",
                activeforeground="#ffffff",
                font=("Segoe UI", 9, "bold"),
                relief=tk.FLAT,
                padx=14,
                pady=5,
                cursor="hand2",
            )
            send_btn.pack(side=tk.RIGHT)
            entry.bind("<Return>", send_reply)

            def on_close_window():
                root.withdraw()

            root.protocol("WM_DELETE_WINDOW", on_close_window)

            def poll_queue():
                try:
                    while True:
                        item = self._cmd_queue.get_nowait()
                        if item.get("action") == "incoming":
                            root.deiconify()
                            root.attributes("-topmost", True)
                            root.lift()
                            ts = item.get("timestamp", "")[-8:-3] or datetime.now().strftime("%H:%M")
                            sender = item.get("sender", "Soporte")
                            append_line(f"{sender} ({ts})", item.get("text", ""), is_admin=True)
                            entry.focus_set()
                except queue.Empty:
                    pass
                root.after(200, poll_queue)

            root.after(100, poll_queue)
            root.mainloop()
        except Exception:
            pass


def get_current_keyboard_layout() -> str:
    """Detecta el idioma o distribución del teclado activa en Windows y macOS."""
    if IS_WINDOWS:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            thread_id = user32.GetWindowThreadProcessId(hwnd, 0)
            klid = user32.GetKeyboardLayout(thread_id)
            lang_id = klid & 0xFFFF
            lcid_names = {
                0x080A: "Español (México)",
                0x040A: "Español (España)",
                0x2C0A: "Español (Argentina)",
                0x240A: "Español (Colombia)",
                0x280A: "Español (Chile)",
                0x0409: "Inglés (EE.UU.)",
                0x0809: "Inglés (Reino Unido)",
                0x0416: "Portugués (Brasil)",
                0x040C: "Francés",
            }
            if lang_id in lcid_names:
                return lcid_names[lang_id]
            if (lang_id & 0x00FF) == 0x0A:
                return "Español"
            elif (lang_id & 0x00FF) == 0x09:
                return "Inglés"
        except Exception:
            pass
    elif IS_MAC:
        try:
            plist_path = str(Path.home() / "Library" / "Preferences" / "com.apple.HIToolbox.plist")
            out = subprocess.check_output(
                ["defaults", "read", plist_path, "AppleSelectedInputSources"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                if "KeyboardLayout Name" in line:
                    raw_name = line.split("=")[-1].strip().strip(';"')
                    mac_names = {
                        "Latin American": "Español (Latinoamérica)",
                        "Spanish": "Español",
                        "Spanish - ISO": "Español (ISO)",
                        "U.S.": "Inglés (EE.UU.)",
                        "ABC": "Inglés (ABC)",
                    }
                    return mac_names.get(raw_name, raw_name)
        except Exception:
            pass
    try:
        import locale
        loc = locale.getdefaultlocale()[0]
        if loc and loc not in ("C", "POSIX"):
            return loc.replace("_", "-")
    except Exception:
        pass
    return "Español (Estándar)"


SPECIAL_KEYS_MAP = {}
def _init_special_keys_map():
    mapping = {
        "space": "[Espacio]",
        "enter": "[Enter]",
        "backspace": "[Borrar]",
        "tab": "[Tab]",
        "esc": "[Esc]",
        "delete": "[Supr]",
        "up": "[↑ Arriba]",
        "down": "[↓ Abajo]",
        "left": "[← Izquierda]",
        "right": "[→ Derecha]",
        "shift": "[Shift]",
        "shift_l": "[Shift]",
        "shift_r": "[Shift]",
        "ctrl": "[Ctrl]",
        "ctrl_l": "[Ctrl]",
        "ctrl_r": "[Ctrl]",
        "alt": "[Alt]",
        "alt_l": "[Alt]",
        "alt_r": "[Alt]",
        "alt_gr": "[AltGr]",
        "cmd": "[Cmd]",
        "cmd_l": "[Cmd]",
        "cmd_r": "[Cmd]",
        "caps_lock": "[BloqMayús]",
        "home": "[Inicio]",
        "end": "[Fin]",
        "page_up": "[RePág]",
        "page_down": "[AvPág]",
        "insert": "[Insert]",
    }
    for attr, label in mapping.items():
        k = getattr(Key, attr, None)
        if k is not None:
            SPECIAL_KEYS_MAP[k] = label

_init_special_keys_map()


# ==============================================================================
# FASE 1 - TELEMETRÍA DE RED E INSPECCIÓN DE DOMINIOS (ESTILO NTOPNG SILENCIOSO)
# ==============================================================================
def get_network_io_counters() -> tuple[int, int]:
    """
    Mini-Función NF-1.1: Obtiene los contadores acumulados nativos de bytes recibidos (RX)
    y transmitidos (TX) de las interfaces físicas de red activas en Windows y macOS,
    excluyendo interfaces loopback (lo0 / Software Loopback) y túneles virtuales vacíos.
    """
    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if IS_WINDOWS:
        try:
            out = subprocess.check_output(
                ["netstat", "-e"],
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                creationflags=no_win,
            ).decode("utf-8", errors="ignore")
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
                    return int(parts[1]), int(parts[2])
        except Exception:
            pass
    elif IS_MAC:
        try:
            out = subprocess.check_output(
                ["/usr/sbin/netstat", "-ibn"],
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).decode("utf-8", errors="ignore")
            rx_total = 0
            tx_total = 0
            seen_ifaces = set()
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 10:
                    continue
                iface = parts[0]
                if iface.startswith(("lo", "gif", "stf", "awdl", "llw", "ap")):
                    continue
                if "<Link#" not in parts[2]:
                    continue
                if iface in seen_ifaces:
                    continue
                try:
                    ibytes = int(parts[6])
                    obytes = int(parts[9])
                    rx_total += ibytes
                    tx_total += obytes
                    seen_ifaces.add(iface)
                except Exception:
                    continue
            return rx_total, tx_total
        except Exception:
            pass
    return 0, 0


# ---- Catálogo de servicios (sincronizado con server/service_catalog.py) ----
_SERVICE_RULES = {
    'youtube.com': ('STREAMING',
        'ggpht.cn ggpht.com googlevideo.com wide-youtube.l.google.com withyoutube.com youtu.be youtube- '
        'nocookie.com youtube-ui.l.google.com youtube.ae youtube.al youtube.am youtube.at youtube.az '
        'youtube.ba youtube.be youtube.bg youtube.bh youtube.bo youtube.by youtube.ca youtube.cat '
        'youtube.ch youtube.cl youtube.co youtube.co.ae youtube.co.at youtube.co.cr youtube.co.hu '
        'youtube.co.id youtube.co.il youtube.co.in youtube.co.jp youtube.co.ke youtube.co.kr '
        'youtube.co.ma youtube.co.nz youtube.co.th youtube.co.tz youtube.co.ug youtube.co.uk '
        'youtube.co.ve youtube.co.za youtube.co.zw youtube.com youtube.com.ar youtube.com.au '
        'youtube.com.az youtube.com.bd youtube.com.bh youtube.com.bo youtube.com.br youtube.com.by '
        'youtube.com.co youtube.com.do youtube.com.ec youtube.com.ee youtube.com.eg youtube.com.es '
        'youtube.com.gh youtube.com.gr youtube.com.gt youtube.com.hk youtube.com.hn youtube.com.hr '
        'youtube.com.jm youtube.com.jo youtube.com.kw youtube.com.lb youtube.com.lv youtube.com.ly '
        'youtube.com.mk youtube.com.mt youtube.com.mx youtube.com.my youtube.com.ng youtube.com.ni '
        'youtube.com.om youtube.com.pa youtube.com.pe youtube.com.ph youtube.com.pk youtube.com.pt '
        'youtube.com.py youtube.com.qa youtube.com.ro youtube.com.sa youtube.com.sg youtube.com.sv '
        'youtube.com.tn youtube.com.tr youtube.com.tw youtube.com.ua youtube.com.uy youtube.com.ve '
        'youtube.cr youtube.cz youtube.de youtube.dk youtube.ee youtube.es youtube.fi youtube.fr '
        'youtube.ge youtube.googleapis.com youtube.gr youtube.gt youtube.hk youtube.hr youtube.hu '
        'youtube.ie youtube.in youtube.iq youtube.is youtube.it youtube.jo youtube.jp youtube.kr '
        'youtube.kz youtube.la youtube.lk youtube.lt youtube.lu youtube.lv youtube.ly youtube.ma '
        'youtube.md youtube.me youtube.mk youtube.mn youtube.mx youtube.my youtube.ng youtube.ni '
        'youtube.nl youtube.no youtube.pa youtube.pe youtube.ph youtube.pk youtube.pl youtube.pr '
        'youtube.pt youtube.qa youtube.ro youtube.rs youtube.ru youtube.sa youtube.se youtube.sg '
        'youtube.si youtube.sk youtube.sn youtube.soy youtube.sv youtube.tn youtube.tv youtube.ua '
        'youtube.ug youtube.uy youtube.vn youtubeeducation.com youtubeembeddedplayer.googleapis.com '
        'youtubefanfest.com youtubegaming.com youtubego.co.id youtubego.co.in youtubego.com '
        'youtubego.com.br youtubego.id youtubego.in youtubei.googleapis.com youtubekids.com '
        'youtubemobilesupport.com yt.be yt3.ggpht.com yt3.googleusercontent.com ytimg.com'),
    'netflix.com': ('STREAMING',
        'fast.com netflix.ca netflix.com netflix.com.edgesuite.net netflix.net netflixdnstest1.com '
        'netflixdnstest10.com netflixdnstest2.com netflixdnstest3.com netflixdnstest4.com '
        'netflixdnstest5.com netflixdnstest6.com netflixdnstest7.com netflixdnstest8.com '
        'netflixdnstest9.com netflixinvestor.com netflixtechblog.com nflxext.com nflximg.com nflximg.net '
        'nflxsearch.net nflxso.net nflxvideo.net'),
    'tiktok.com': ('SOCIAL',
        'amemv.com bdurl.com bytecdn.cn bytedance.map.fastly.net bytedapm.com bytegoofy.com byteimg.com '
        'byteoversea.com bytescm.com douyin.com douyincdn.com douyinliving.com douyinpic.com '
        'douyinstatic.com douyinvod.com huoshan.com huoshanstatic.com huoshanzhibo.com ibytedtos.com '
        'ibyteimg.com muscdn.com musical.ly p16-tiktokcdn-com.akamaized.net pstatp.com snssdk.com '
        'tiktok.com tiktok.tps.dv.tech tiktokcdn-eu.com tiktokcdn-eu.com.ttdns3.com tiktokcdn-us.com '
        'tiktokcdn.com tiktokrow-cdn.com tiktokv.com tiktokv.eu ttlivecdn.com ttlivecdn.com.c.bytefcdn- '
        'oversea.com ttwstatic.com zijieapi.com'),
    'whatsapp.com': ('MESSAGING',
        'wa.me whatsapp-plus.info whatsapp-plus.me whatsapp-plus.net whatsapp.cc whatsapp.com '
        'whatsapp.info whatsapp.net whatsapp.org whatsapp.tv whatsappbrand.com'),
    'facebook.com': ('SOCIAL',
        'aboutfacebook.com accessfacebookfromschool.com accountkit.com acebooik.com acebook.com '
        'advancediddetection.com askfacebook.net askfacebook.org atdmt2.com atlasdmt.com '
        'atlasonepoint.com atscaleconference.com botorch.org buck.build buckbuild.com '
        'buyingfacebooklikes.com careersatfb.com celebgramme.com china-facebook.com click-url.com como- '
        'hackearfacebook.com componentkit.org crowdtangle.com dacebook.com dlfacebook.com dotfacebook.com '
        'dotfacebook.net draftjs.org expresswifi.com f8.com faacebok.com faacebook.com faasbook.com '
        'facbebook.com facbeok.com facboo.com facbook.com facbool.com facboox.com faccebook.com '
        'faccebookk.com facdbook.com facdebook.com face-book.com faceabook.com facebboc.com facebbook.com '
        'facebboook.com facebcook.com facebdok.com facebgook.com facebhook.com facebkkk.com facebo-ok.com '
        'faceboak.com facebock.com facebocke.com facebof.com faceboik.com facebok.com facebokbook.com '
        'facebokc.com facebokk.com facebokok.com faceboks.com facebol.com facebolk.com facebomok.com '
        'faceboo.com facebooa.com faceboob.com faceboobok.com facebooc.com faceboock.com facebood.com '
        'facebooe.com faceboof.com facebooi.com facebooik.com facebooik.org facebooj.com facebook- '
        'corp.com facebook-covid-19.com facebook-ebook.com facebook-forum.com facebook-hardware.com '
        'facebook-inc.com facebook-login.com facebook-newsroom.com facebook-newsroom.org facebook- '
        'pmdcenter.com facebook-pmdcenter.net facebook-pmdcenter.org facebook-privacy.com facebook- '
        'program.com facebook-studio.com facebook-support.org facebook-texas-holdem.com facebook-texas- '
        'holdem.net facebook.br facebook.ca facebook.cc facebook.com facebook.design facebook.hu '
        'facebook.in facebook.net facebook.nl facebook.org facebook.se facebook.shop facebook.tv '
        'facebook.us facebook.wang facebook123.org facebook30.com facebook30.net facebook30.org '
        'facebook4business.com facebookads.com facebookadvertisingsecrets.com facebookappcenter.info '
        'facebookappcenter.net facebookappcenter.org facebookatschool.com facebookawards.com '
        'facebookblueprint.net facebookbrand.com facebookbrand.net '
        'facebookcanadianelectionintegrityinitiative.com facebookcareer.com facebookcheats.com '
        'facebookck.com facebookclub.com facebookcom.com facebookconnect.com facebookconsultant.org '
        'facebookcoronavirus.com facebookcovers.org facebookcredits.info facebookdating.net '
        'facebookdevelopergarage.com facebookdusexe.org facebookemail.com facebookenespanol.com '
        'facebookexchange.com facebookexchange.net facebookfacebook.com facebookflow.com '
        'facebookgames.com facebookgraphsearch.com facebookgraphsearch.info facebookgroups.com '
        'facebookhome.cc facebookhome.com facebookhome.info facebookhub.com facebooki.com facebookinc.com '
        'facebookland.com facebooklikeexchange.com facebooklive.com facebooklivestaging.net '
        'facebooklivestaging.org facebooklogin.com facebooklogin.info facebookloginhelp.net '
        'facebooklogs.com facebookmail.com facebookmail.tv facebookmanager.info facebookmarketing.info '
        'facebookmarketingpartner.com facebookmarketingpartners.com facebookmobile.com facebookmsn.com '
        'facebooknews.com facebooknfl.com facebooknude.com facebookofsex.com facebookook.com '
        'facebookpaper.com facebookpay.com facebookphonenumber.net facebookphoto.com facebookphotos.com '
        'facebookpmdcenter.com facebookpoke.net facebookpoke.org facebookpoker.info '
        'facebookpokerchips.info facebookporn.net facebookporn.org facebookporno.net facebookportal.com '
        'facebooks.com facebooksafety.com facebooksecurity.net facebookshop.com facebooksignup.net '
        'facebooksite.net facebookstories.com facebookstudios.net facebookstudios.org '
        'facebooksupplier.com facebooksuppliers.com facebookswagemea.com facebookswagstore.com '
        'facebooksz.com facebookthreads.net facebooktv.net facebooktv.org facebookvacation.com '
        'facebookw.com facebookwork.com facebookworld.com facebool.com facebool.info facebooll.com '
        'faceboom.com faceboon.com faceboonk.com faceboooik.com faceboook.com faceboop.com faceboot.com '
        'faceboox.com facebopk.com facebpook.com facebuk.com facebuok.com facebvook.com facebyook.com '
        'facebzook.com facecbgook.com facecbook.com facecbook.org facecook.com facecook.org facedbook.com '
        'faceebok.com faceebook.com faceebot.com facegbok.com facegbook.com faceobk.com faceobok.com '
        'faceobook.com faceook.com facerbooik.com facerbook.com facesbooc.com facesounds.com facetook.com '
        'facevbook.com facewbook.co facewook.com facfacebook.com facfebook.com faciometrics.com '
        'fackebook.com facnbook.com facrbook.com facvebook.com facwebook.com facxebook.com fadebook.com '
        'faebok.com faebook.com faebookc.com faeboook.com faecebok.com faesebook.com fafacebook.com '
        'faicbooc.com fasebokk.com fasebook.com faseboox.com fasttext.cc favebook.com faycbok.com '
        'fb.careers fb.com fb.gg fb.me fb.watch fbacebook.com fbbmarket.com fbboostyourbusiness.com '
        'fbcdn-a.akamaihd.net fbcdn.com fbcdn.net fbf8.com fbfeedback.com fbhome.com fbidb.io fbinc.com '
        'fbinfer.com fbinnovation.com fblitho.com fbmarketing.com fbmessenger.com fbpigeon.com '
        'fbredex.com fbreg.com fbrell.com fbrpms.com fbsbx.com fbsbx.net fbsupport-covid.net '
        'fbthirdpartypixel.com fbthirdpartypixel.net fbthirdpartypixel.org fburl.com fbwat.ch '
        'fbworkmail.com fcacebook.com fcaebook.com fcebook.com fcebookk.com fcfacebook.com fdacebook.info '
        'feacboo.com feacbook.com feacbooke.com feacebook.com fecbbok.com fecbooc.com fecbook.com '
        'feceboock.com fecebook.net feceboox.com fececbook.com feook.com ferabook.com fescebook.com '
        'fesebook.com ffacebook.com fgacebook.com ficeboock.com flow.dev flow.org flowtype.org '
        'fmcebook.com fnacebook.com fosebook.com fpacebook.com fqcebook.com fracebook.com freeb.com '
        'freebasics.com freebasics.net freebs.com freefacebook.com freefacebook.net freefacebookads.net '
        'freefblikes.com freindfeed.com frescolib.org friendbook.info friendfed.com friendfeed-api.com '
        'friendfeed-media.com friendfeed.com friendfeedmedia.com fsacebok.com fscebook.com '
        'fundraisingwithfacebook.com funnyfacebook.org futureofbusinesssurvey.org gacebook.com '
        'gameroom.com gfacecbook.com groups.com hackerfacebook.com hackfacebook.com hackfacebookid.com '
        'hacklang.org hhvm.com hifacebook.info howtohackfacebook-account.com hsfacebook.com '
        'httpfacebook.com httpsfacebook.com httpwwwfacebook.com i.org internet.org klik.me liverail.com '
        'liverail.tv login-account.net makeitopen.com markzuckerberg.com mcrouter.net mcrouter.org '
        'messengerdevelopers.com midentsolutions.com mobilefacebook.com moneywithfacebook.com '
        'myfbfans.com nbabot.net newsfeed.com nextstop.com ogp.me online-deals.net opencreate.org '
        'opengraphprotocol.com opengraphprotocol.org parse.com pyrobot.org reachtheworldonfacebook.com '
        'react.com reactjs.com reactjs.org recoiljs.org redkix.com rocksdb.com rocksdb.net rocksdb.org '
        'shopfacebook.com sportsfacebook.com sportstream.com supportfacebook.com terragraph.com '
        'thefacebook.com thefacebook.net thefind.com toplayerserver.com viewpointsfromfacebook.com '
        'whyfacebook.com workplace.com workplaceusecases.com worldhack.com www-facebook.com '
        'wwwfacebok.com wwwfacebook.com wwwmfacebook.com yogalayout.com zuckerberg.com zuckerberg.net'),
    'instagram.com': ('SOCIAL',
        'achat-followers-instagram.com acheter-followers-instagram.com acheterdesfollowersinstagram.com '
        'acheterfollowersinstagram.com bookstagram.com carstagram.com cdninstagram.com chickstagram.com '
        'ig.me igcdn.com igsonar.com igtv.com imstagram.com imtagram.com instaadder.com instachecker.com '
        'instafallow.com instafollower.com instagainer.com instagda.com instagify.com instagmania.com '
        'instagor.com instagr.am instagram-brand.com instagram-engineering.com instagram-help.com '
        'instagram-press.com instagram-press.net instagram.com instagramci.com instagramcn.com '
        'instagramdi.com instagramhashtags.net instagramhilecim.com instagramhilesi.org instagramium.com '
        'instagramizlenme.com instagramkusu.com instagramlogin.com instagramm.com instagramn.com '
        'instagrampartners.com instagramphoto.com instagramq.com instagramsepeti.com '
        'instagramtakipcisatinal.net instagramtakiphilesi.com instagramtips.com instagramtr.com '
        'instagran.com instagranm.com instagrem.com instagrm.com instagtram.com instagy.com '
        'instamgram.com instangram.com instanttelegram.com instaplayer.net instastyle.tv instgram.com '
        'intagram.com intagrm.com intgram.com kingstagram.com lnstagram-help.com oninstagram.com online- '
        'instagram.com onlineinstagram.com theinstagramhack.com web-instagram.net wwwinstagram.com'),
    'threads.net': ('SOCIAL',
        'threads.com threads.net'),
    'messenger.com': ('MESSAGING',
        'm.me messenger.com'),
    'telegram.org': ('MESSAGING',
        'cdn-telegram.org comments.app contest.com graph.org quiz.directory t.me tdesktop.com telega.one '
        'telegra.ph telegram-cdn.org telegram.dog telegram.me telegram.org telegram.space telesco.pe '
        'tg.dev tx.me usercontent.dev'),
    'discord.com': ('MESSAGING',
        'airhorn.solutions airhornbot.com bigbeans.solutions dis.gd discord-activities.com discord.co '
        'discord.com discord.design discord.dev discord.gg discord.gift discord.gifts discord.media '
        'discord.new discord.store discord.tools discordactivities.com discordapp.com discordapp.io '
        'discordapp.net discordcdn.com discordmerch.com discordpartygames.com discordsays.com '
        'discordstatic.com discordstatus.com hammerandchisel.ssl.zendesk.com watchanimeattheoffice.com'),
    'spotify.com': ('STREAMING',
        'audio-ak-spotify-com.akamaized.net audio4-ak-spotify-com.akamaized.net byspotify.com eip- '
        'ntt.video-ak.cdn.spotify.com.akahost.net heads-ak-spotify-com.akamaized.net heads4-ak-spotify- '
        'com.akamaized.net pscdn.co scdn.co spoti.fi spotify-everywhere.com spotify.com '
        'spotify.com.edgesuite.net spotify.design spotify.map.fastly.net spotify.map.fastlylb.net '
        'spotifycdn.com spotifycdn.net spotifycharts.com spotifycodes.com spotifyforbrands.com '
        'spotifyjobs.com spotilocal.com video-ak.cdn.spotify.com video-akpcw-cdn-spotify- '
        'com.akamaized.net video-akpcw.spotifycdn.com video-akpcw.spotifycdn.com.edgesuite.net video- '
        'fa.scdn.co'),
    'twitch.tv': ('STREAMING',
        'ext-twitch.tv jtvnw.net ttvnw.net twitch.tv twitchcdn.net twitchsvc.net'),
    'kick.com': ('STREAMING',
        'kick.com'),
    'disneyplus.com': ('STREAMING',
        'bamgrid.com disney-plus.net disney.playback.edge.bamgrid.com disneynow.com disneyplus.com '
        'disneystreaming.com dssott.com hotstar.com media.dssott.com star.playback.edge.bamgrid.com '
        'starplus.com'),
    'max.com': ('STREAMING',
        'hbo.com hbogo.co.th hbogo.com hbogo.eu hbogoasia.com hbogoasia.id hbogoasia.ph hbomax- '
        'images.warnermediacdn.com hbomax.com hbomaxcdn.com hbonow.com max.com maxgo.com'),
    'primevideo.com': ('STREAMING',
        'aiv-cdn.net aiv-delivery.net amazonmusic.com amazonprimevideo.cn amazonprimevideo.com.cn '
        'amazonprimevideos.com amazonvideo.cc amazonvideo.com amazonvideodirect.com atv-ext-eu.amazon.com '
        'atv-ext-fe.amazon.com atv-ext.amazon.com atv-ps-eu.amazon.co.uk atv-ps-eu.amazon.com atv-ps- '
        'fe.amazon.co.jp atv-ps-fe.amazon.com atv-ps.amazon.com av-eu.amazon.com av-na.amazon.com '
        'music.a2z.com music.amazon.co.uk music.amazon.com music.amazon.in prime-video.com primevideo.cc '
        'primevideo.com primevideo.info primevideo.org primevideo.tv pv-cdn.net video.a2z.com'),
    'tv.apple.com': ('STREAMING',
        'applemusic.apple atv.apple.com hls-svod-aoc-ve.itunes.g.aaplimg.com itun.es itunes.apple.com '
        'itunes.ca itunes.co itunes.co.th itunes.com itunes.es itunes.g.aaplimg.com itunes.hk itunes.mx '
        'itunes.org itunes.us music.apple.com tv.apple.com tv.g.apple.com tv.v.aaplimg.com'),
    'chatgpt.com': ('AI',
        'chatgpt.com oaistatic.com oaiusercontent.com openai.com'),
    'claude.ai': ('AI',
        'anthropic.com claude.ai claude.com claudeusercontent.com'),
    'gemini.google.com': ('AI',
        'aistudio.google.com bard.google.com gemini.google.com generativelanguage.googleapis.com'),
    'copilot.microsoft.com': ('AI',
        'copilot.ai copilot.cloud.microsoft copilot.com copilot.microsoft.com copilotstudio.microsoft.com'),
    'perplexity.ai': ('AI',
        'perplexity.ai pplx.ai'),
    'deepseek.com': ('AI',
        'deepseek.com'),
    'canva.com': ('WORK',
        'canva.com'),
    'figma.com': ('WORK',
        'figma.com'),
    'notion.so': ('WORK',
        'notion-static.com notion.com notion.so'),
    'slack.com': ('WORK',
        'slack-edge.com slack-imgs.com slack-msgs.com slack.com slackb.com'),
    'zoom.us': ('WORK',
        'zoom.com zoom.us zoomgov.com'),
    'teams.microsoft.com': ('WORK',
        'teams.cdn.office.net teams.live.com teams.microsoft.com'),
    'meet.google.com': ('WORK',
        'meet.google.com'),
    'drive.google.com': ('CLOUD',
        'drive.google.com drive.usercontent.google.com'),
    'docs.google.com': ('WORK',
        'docs.google.com sheets.google.com slides.google.com'),
    'mail.google.com': ('WORK',
        'gmail.com mail.google.com'),
    'outlook.com': ('WORK',
        'outlook.com outlook.live.com outlook.office.com outlook.office365.com'),
    'office365.com': ('WORK',
        'microsoftonline.com office.com office.net office365.com'),
    'onedrive.live.com': ('CLOUD',
        '1drv.com 1drv.ms onedrive.com onedrive.live.com'),
    'sharepoint.com': ('CLOUD',
        'sharepoint.com svc.ms'),
    'dropbox.com': ('CLOUD',
        'addtodropbox.com app.hellosign.com dash.ai db.tt docsend.com dropbox-dns.com dropbox.com '
        'dropbox.tech dropbox.zendesk.com dropboxapi.com dropboxbusiness.com dropboxcaptcha.com '
        'dropboxforum.com dropboxforums.com dropboxinsiders.com dropboxlegal.com dropboxmail.com '
        'dropboxpartners.com dropboxstatic.com dropboxteam.com dropboxusercontent.com getdropbox.com'),
    'wetransfer.com': ('CLOUD',
        'wetransfer.com wetransfer.net'),
    'mega.nz': ('CLOUD',
        'mega.co.nz mega.io mega.nz'),
    'box.com': ('CLOUD',
        'box.com box.net boxcdn.net boxcloud.com'),
    'github.com': ('WORK',
        'github.com github.io githubassets.com githubusercontent.com'),
    'gitlab.com': ('WORK',
        'gitlab.com'),
    'bitbucket.org': ('WORK',
        'bbc.org bitbucket.org'),
    'stackoverflow.com': ('WORK',
        'sstatic.net stackexchange.com stackoverflow.com'),
    'atlassian.com': ('WORK',
        'atl-paas.net atlassian.com atlassian.net jira.com'),
    'trello.com': ('WORK',
        'trello.com'),
    'asana.com': ('WORK',
        'asana.com'),
    'monday.com': ('WORK',
        'monday.com'),
    'clickup.com': ('WORK',
        'clickup.com'),
    'linear.app': ('WORK',
        'linear.app'),
    'vercel.com': ('CLOUD',
        'vercel.app vercel.com'),
    'netlify.com': ('CLOUD',
        'netlify.app netlify.com'),
    'railway.app': ('CLOUD',
        'railway.app railway.com'),
    'digitalocean.com': ('CLOUD',
        'digitalocean.com digitaloceanspaces.com ondigitalocean.app'),
    'cloudflare.com': ('CLOUD',
        'argotunnel.com cf-ipfs.com cloudflare-dns.com cloudflare-ipfs.com cloudflare-quic.com '
        'cloudflare.com cloudflare.net cloudflare.tv cloudflareaccess.com cloudflareapps.com '
        'cloudflarebolt.com cloudflareclient.com cloudflareinsights.com cloudflareok.com '
        'cloudflarepreview.com cloudflareresolve.com cloudflaressl.com cloudflarestatus.com '
        'cloudflarestorage.com cloudflarestream.com cloudflaretest.com cloudflarewarp.com every1dns.net '
        'pacloudflare.com pages.dev trycloudflare.com videodelivery.net warp.plus workers.dev'),
    'aws.amazon.com': ('CLOUD',
        'amazonaws.com aws.amazon.com awsapps.com awsstatic.com'),
    'azure.com': ('CLOUD',
        'azure.com azure.net azureedge.net azurewebsites.net core.windows.net'),
    'cloud.google.com': ('CLOUD',
        'appspot.com cloud.google.com cloudfunctions.net firebaseapp.com run.app web.app'),
    'mercadolibre.com': ('SHOPPING',
        'mercadolibre.cl mercadolibre.co.cr mercadolibre.com mercadolibre.com.ar mercadolibre.com.bo '
        'mercadolibre.com.co mercadolibre.com.do mercadolibre.com.ec mercadolibre.com.gt '
        'mercadolibre.com.hn mercadolibre.com.mx mercadolibre.com.ni mercadolibre.com.pa '
        'mercadolibre.com.pe mercadolibre.com.py mercadolibre.com.sv mercadolibre.com.uy '
        'mercadolibre.com.ve mercadolivre.com.br mercadopago.com mercadopago.com.mx mlstatic.com'),
    'amazon.com': ('SHOPPING',
        'a2z.com a2z.org.cn aboutamazon.cn aboutamazon.co.uk aboutamazon.com aboutamazon.com.au '
        'aboutamazon.de aboutamazon.es aboutamazon.eu aboutamazon.fr aboutamazon.in aboutamazon.it '
        'aboutamazon.jp aboutamazon.pl acmvalidations.com acmvalidationsaws.com aesworkshops.com '
        'alexa.com alexafund.cn alexafund.com.cn amaaozn.com amazon-adsystem.com amazon-fashions.com '
        'amazon-jp-recruiting.com amazon-lantern.com amazon-launchpad.com amazon.ae amazon.ca amazon.cn '
        'amazon.co.jp amazon.co.uk amazon.com amazon.com.au amazon.com.be amazon.com.br amazon.com.mx '
        'amazon.com.tr amazon.de amazon.es amazon.fr amazon.in amazon.it amazon.jobs amazon.jp amazon.nl '
        'amazon.red amazon.se amazon.sg amazonalexavoxcon.com amazonauthorinsights.com amazonbusiness.cn '
        'amazonbusiness.com.cn amazonbusiness.org amazonbusinessblog.com amazonchoice.cn '
        'amazonchoice.com.cn amazonchoices.cn amazonchoices.com.cn amazondevicesupport.com '
        'amazonfctours.com amazonianblog.com amazonimages.com amazoninspire.cn amazoninspire.com.cn '
        'amazonlaunchpad.cn amazonlaunchpad.com amazonlaunchpad.com.cn amazonlending.com.cn '
        'amazonliterarypartnership.com amazonlumberyard.wang amazonnow.cn amazonnow.com.cn amazonpay.com '
        'amazonpay.in amazonsdi.com amazonses.com amazonstudiosguilds.com amazontrust.com '
        'amazonwebservices.com.cn amazonworkdocs.cn amazonworkdocs.com amazonworkdocs.com.cn '
        'amplifyapp.com amplifyframework.com amzn.asia amzn.com amzn.to amznl.com asfiovnxocqpcry.com.cn '
        'assoc-amazon.cn associates-amazon.com audible.com aws-border.cn aws-icp-domain-manager.cn aws- '
        'iot-hackathon.com awsapps.cn awsapps.com.cn awsautopilot.com awsautoscaling.com awsbraket.com '
        'awscommandlineinterface.com awsedstart.com awseducate.com awseducate.net awseducate.org '
        'awsglobalaccelerator.com awsloft-johannesburg.com awsloft-stockholm.com awssecworkshops.com '
        'awsstatic.cn awsthinkbox.com awstrack.me awstrust.com boxofficemojo.com cdkworkshop.com '
        'containersonaws.com createspace.com elasticbeanstalk.com gameon-masters.com gdansk-amazon.com '
        'images-amazon.com imdb.com imdb.to kindle.cn kindle.co.jp kindle.co.uk kindle.com kindle.de '
        'kindle.es kindle.fr kindle.in kindle.it kindle.jp kindleoasis.cn kindleoasis.com '
        'kindleoasis.com.cn kindleoasis.info kindleoasis.jp kindleoasis.org kindleoasis.us '
        'kindleoasisnews.com kindleproject.com media-amazon.com media-imdb.com nwcdcloud.cn '
        'nwcdcloud.com.cn nwcddns.cn nwcdinfosec.cn primeday.cn primeday.com.cn primeday.info route53.cn '
        'sagemaker.com.cn serving-sys.com siege-amazon.com ss2.us ssl-images-amazon.com '
        'thinkboxsoftware.com ueberamazon.de yamaxun.cn yamaxun.com z.cn'),
    'aliexpress.com': ('SHOPPING',
        'ae-rus.net ae-rus.ru alicdn.com aliexpress.com aliexpress.ru aliexpress.us'),
    'temu.com': ('SHOPPING',
        'kwcdn.com temu.com temucdn.com'),
    'shein.com': ('SHOPPING',
        'ltwebstatic.com shein.co.uk shein.com shein.se sheingsp.com sheinsz.ltwebstatic.com'),
    'shopee.com': ('SHOPPING',
        'shopee.cl shopee.cn shopee.co.id shopee.co.th shopee.com shopee.com.br shopee.com.co '
        'shopee.com.mx shopee.com.my shopee.es shopee.fr shopee.id shopee.in shopee.io shopee.ph '
        'shopee.sg shopee.tw shopee.vn shopeemobile.com shp.ee'),
    'liverpool.com.mx': ('SHOPPING',
        'liverpool.com.mx'),
    'walmart.com': ('SHOPPING',
        'walmart.com walmart.com.mx walmartimages.com walmartmexico.com'),
    'coppel.com': ('SHOPPING',
        'coppel.com'),
    'roblox.com': ('GAMING',
        'blox.com rbx.cn rbx.com rbxadder.com rbxcdn.com rbxcdn.net rbxinfra.com rbxinfra.net roblox.cn '
        'roblox.com roblox.qq.com robloxcdn.com robloxdev.cn robloxlabs.com'),
    'steampowered.com': ('GAMING',
        'csgo.wmsj.cn dl.steam.clngaa.com dl.steam.ksyna.com dota2.wmsj.cn playartifact.com s.team '
        'st.dl.bscstorage.net st.dl.eccdnx.com st.dl.pinyuncloud.com steam-api.com steam-chat.com '
        'steambroadcast.akamaized.net steamcdn-a.akamaihd.net steamchina.com '
        'steamcommunity-a.akamaihd.net steamcommunity.com steamcontent.com steamdeck.com steamgames.com '
        'steampipe.akamaized.net steampipe.steamcontent.tnkjmec.com steampowered.com '
        'steampowered.com.8686c.com steamserver.net steamstatic.com steamstatic.com.8686c.com '
        'steamstore-a.akamaihd.net steamusercontent-a.akamaihd.net steamusercontent.com '
        'steamuserimages-a.akamaihd.net steamvideo-a.akamaihd.net underlords.com valvesoftware.com '
        'wmsjsteam.com xz.pphimalayanrt.com'),
    'epicgames.com': ('GAMING',
        'eac-cdn.com easy.ac easyanticheat.net epicgames.com epicgames.dev unrealengine.com'),
    'riotgames.com': ('GAMING',
        'dradis-prod.rdatasrv.net pvp.net rdatasrv.net rgpub.io riotcdn.com riotcdn.net riotgames.com'),
    'leagueoflegends.com': ('GAMING',
        'leagueoflegends.co.kr leagueoflegends.com lol.riotgames.com lolstatic.com lolusercontent.com'),
    'battle.net': ('GAMING',
        'battle.net battlenet.com.cn blizzard.cn blizzard.com blizzardgames.cn blz-contentstack.com '
        'blzstatic.cn bnet.163.com bnet.cn'),
    'ea.com': ('GAMING',
        'accounts.ea.com cloudsync-prod.s3.amazonaws.com dawngate.com ea.com eamobile.com easports.com '
        'eastore.com lordofultima.com nearpolar.com origin-a.akamaihd.net origin.com origin.tv rtm.tnt- '
        'ea.com signin.ea.com ssl-lvlt.cdn.ea.com swtor.com tnt-ea.com'),
    'playstation.com': ('GAMING',
        'gaikai.com playstation-cloud.com playstation-cloud.net playstation.com playstation.net scea.com '
        'sonyentertainmentnetwork.com station.sony.com'),
    'xbox.com': ('GAMING',
        'gamepass.com xbox-global.ifs.windows.com xbox-guide-public.rec.mp.microsoft.com xbox.com '
        'xbox.ipv6.microsoft.com xboxab.com xboxab.net xboxlive.com xboxservices.com'),
    'nintendo.com': ('GAMING',
        'nintendo-europe.com nintendo.be nintendo.co.jp nintendo.co.uk nintendo.com nintendo.com.au '
        'nintendo.de nintendo.es nintendo.eu nintendo.fr nintendo.it nintendo.jp nintendo.net nintendo.nl '
        'nintendo.pt nintendoswitch.cn nintendowifi.net'),
    'x.com': ('SOCIAL',
        'ads-twitter.com cms-twdigitalassets.com periscope.tv pscp.tv t.co tellapart.com tweetdeck.com '
        'twimg.com twitpic.com twitter.biz twitter.com twitter.jp twittercommunity.com '
        'twitterflightschool.com twitterinc.com twitteroauth.com twitterstat.us twtrdns.net twttr.com '
        'twttr.net twvid.com vine.co x.com'),
    'google.com': ('WORK',
        '1e100.net google.com googleapis.com googleusercontent.com gstatic.com gvt1.com gvt2.com'),
    'icloud.com': ('SYSTEM',
        'apple-dns.net ess.apple.com icloud-content.com icloud.com mzstatic.com push.apple.com'),
    'windowsupdate.com': ('SYSTEM',
        'delivery.mp.microsoft.com mp.microsoft.com update.microsoft.com windowsupdate.com'),
    'anydesk.com': ('REMOTE',
        'anydesk.com net.anydesk.com'),
    'teamviewer.com': ('REMOTE',
        'teamviewer.com'),
    '4chan.org': ('SOCIAL',
        '4cdn.org 4chan.org 4channel.org'),
    '500px.com': ('SOCIAL',
        '500px.com 500px.org'),
    '9gag.com': ('SOCIAL',
        '9cache.com 9gag.com'),
    'activisionblizzard.com': ('GAMING',
        'activision.com activisionblizzard.com callofduty.com callofdutyleague.com codmwest.com '
        'demonware.net'),
    'aminoapps.com': ('SOCIAL',
        'aminoapps.com'),
    'betano.com': ('GAMING',
        'betano.bg betano.ca betano.com betano.cz betano.de betano.ng betano.pt'),
    'betfair.com': ('GAMING',
        'betfair.com betfair.com.au betfair.es betfair.it betfair.ro betfair.se'),
    'betway.com': ('GAMING',
        'betway.be betway.bet.ar betway.co.za betway.com betway.com.gh betway.com.ng betway.de betway.es '
        'betway.fr betway.it betway.mx betway.pl betway.se betwaygroup.com betwaysatta.com '
        'vietnambetway88.com'),
    'bigo.tv': ('STREAMING',
        'bigo.sg bigo.tv bigolive.tv bigovideo.tv'),
    'bilibili.com': ('STREAMING',
        'acg.tv acgvideo.com animetamashi.cn animetamashi.com anitama.cn anitama.net b23.tv bigfun.cn '
        'bili22.cn bili2233.cn bili23.cn bili33.cn biliapi.com biliapi.net bilibili.cc bilibili.cn '
        'bilibili.com bilibili.net bilibili.tv bilibiligame.cn bilibiligame.co bilibiligame.net '
        'bilibilipay.cn bilibilipay.com bilicdn1.com bilicdn2.com bilicdn3.com bilicdn4.com bilicdn5.com '
        'biligame.co biligame.com biligame.net biligo.com biliimg.com biliintl.com bilivideo.cn '
        'bilivideo.com bilivideo.net dreamcast.hk hdslb.com hdslb.org im9.com maoercdn.com mincdn.com '
        'upos-hz-mirrorakam.akamaized.net yo9.com'),
    'blaze.com': ('GAMING',
        'blaze.bet blaze.com blaze.com.br blazecareers.com'),
    'bsky.app': ('SOCIAL',
        'bsky.app bsky.social'),
    'globo.com': ('STREAMING',
        'canaisglobo.globo.com globosat.globo.com gsatmulti.globo.com'),
    'clarovideo.com': ('STREAMING',
        'claromusica.com clarovideo.com'),
    'clubhouse.com': ('SOCIAL',
        'clubhouse.com clubhouseapi.com'),
    'coolapk.com': ('SHOPPING',
        'coolapk.com coolapkmarket.com coolapkmarket.net'),
    'crunchyroll.com': ('STREAMING',
        'crunchyroll.com gccrunchyroll.com'),
    'dailymotion.com': ('STREAMING',
        'dailymotion.com dm-event.net dmcdn.net'),
    'deezer.com': ('STREAMING',
        'deezer.com dzcdn.net'),
    'directvgo.com': ('STREAMING',
        'directvgo.com'),
    'discoveryplus.com': ('STREAMING',
        'disco-api.com discoveryplus.com'),
    'dola.com': ('AI',
        'cici.com ciciai.com dola.com'),
    'douban.com': ('SOCIAL',
        'douban.com douban.fm doubanio.com'),
    'ebay.com': ('SHOPPING',
        '21centuryaccess.com 4ebaytraders.com adcommerce.cn adcommerce.tv appforebay.cn appsonebay.net '
        'asebay.com baazee.com bidbay.com bidorbuyindia.com billpoint.com billpoint.info billpoint.tv '
        'billpoint.us billpointnewzealand.com blogebay.com bookclubcorner.com builtfromebay.com '
        'buyitnow.com buyitnow.net buyitnow.org buyitnow.tv buyitnowshop.net cafr.ca carebay.com '
        'cargigileads.com cebay.com collective99.com commerceos.com connectcommerce.cn '
        'connectcommerce.com.cn connectcommerce.hk connectcommerce.info connectcommerce.tv '
        'connectedcommerce.cn connectedcommerce.com connectedcommerce.tv crececonebay.com '
        'creditcardsbay.com cyber-bay.cn cyber-bay.com.cn cyber-bay.info cyber-bay.org dba.dk dealbay.com '
        'dealtime.com didce.com douya.org dreamtoplay.com e-bay.com e-bay.it e-bay.net eachpay.com '
        'eachpay.net ebahy.com ebay-authenticate.net ebay-confirm.com ebay-course.com ebay-cz.com ebay- '
        'delivery.com ebay-discoveries.com ebay-fashion.com ebay-inc.com ebay-inc.net ebay-inc.org ebay- '
        'online.com ebay-sales.com ebay-stories.com ebay-us.com ebay-vacation.com ebay.at ebay.be ebay.ca '
        'ebay.ch ebay.cn ebay.co.nz ebay.co.uk ebay.co.ve ebay.co.za ebay.com ebay.com.ar ebay.com.au '
        'ebay.com.cn ebay.com.ec ebay.com.hk ebay.com.mt ebay.com.my ebay.com.ph ebay.com.sg ebay.de '
        'ebay.es ebay.fr ebay.ie ebay.in ebay.it ebay.jp ebay.lt ebay.mn ebay.net.cn ebay.nl ebay.org '
        'ebay.org.cn ebay.ph ebay.pk ebay.pl ebay.sg ebay.us ebay.vn ebay.yn.cn ebay.zj.cn ebay25.com '
        'ebay68.com ebaya.com ebayads.com ebayads.net ebayadvertising.cn ebayadvertising.com '
        'ebayanunsios.net ebayauction.com ebayaustralia.com ebayauthenticate.com.cn ebaybags.com '
        'ebaybank.com ebaybenefits.com ebayboutique.com ebayca.com ebayca.org ebaycafe.com ebaycar.com '
        'ebaycareers.com ebaycbt.co.kr ebaycdn.net ebaychina.net ebayclassifieds.cn ebayclassifieds.com '
        'ebayclassifieds.com.cn ebayclassifieds.info ebayclassifieds.org ebayclassifieds.tv '
        'ebayclassifiedsgroup.com ebayclassifiedsgroup.info ebayclassifiedsgroup.org ebayclassifies.com '
        'ebayclub.com ebaycoins.com ebaycom.com ebaycommercenetwork.com ebaycourse.com ebayd.com '
        'ebayde.com ebaydesc.cn ebaydesc.com.cn ebaydlassifieds.com ebaydns.cn ebaydts.com ebayedu.com '
        'ebayeletro.com ebayenterprise.cn ebayenterprise.com ebayenterprise.com.cn ebayenterprise.info '
        'ebayenterprise.net ebayenterprise.tv ebayetc.com ebayexpress.sg ebayfashion.com ebayfashion.net '
        'ebayforcharity.org ebayforeclosure.org ebayfrance.com ebayglobalshipping.com ebaygroup.com '
        'ebayhabit.com ebayheels.com ebayhots.com ebayimg.com ebayinc.com ebayinc.net ebayinc.org '
        'ebayincconnectedcommerce.net ebayinkblog.com ebayinternetsalestax.com ebayit.com ebayjewelry.com '
        'ebayjob.com ebayla.org ebaylisting.com ebaylocal.net ebaylocationsdevacances.com ebaymag.com '
        'ebaymainstreet.com ebaymall.com ebaymarketplace.net ebaymotors.ca ebaymotors.cn ebaymotors.com '
        'ebaymotors.com.cn ebaymotors.org ebaymotorsblog.com ebaynow.com ebaynyc.com ebayon.com '
        'ebayon.net ebayoncampus.com ebayopen.com ebayopensource.com ebayopensource.net ebaypakistan.net '
        'ebaypark.com ebayparts.com ebaypedia.cn ebaypedia.com.cn ebayprivacycenter.com ebayqq.com '
        'ebayradio.com ebayrtm.com ebayseller.com ebayshoesstore.com ebayshop.com ebayshop111.com '
        'ebayshopping.cn ebayshopping.com.cn ebayshopping.org ebaysocial.com ebaysocial.ru ebaysoho.com '
        'ebaysohos.com ebaystatic.cn ebaystatic.com ebaystore.com ebaystore77.com ebaystores.cn '
        'ebaystyle.com ebaysweden.com ebayt.com ebaytechblog.com ebaytopratedseller.net ebaytrading.com '
        'ebaytradingassistant.com ebaytv.org ebayuae.net ebayvakantiehuizen.com ebayvalet.com '
        'ebayvietnam.net ebayworlds.com ebayy.com edisebay.com eebay.com epinions.com eu-consumer- '
        'empowerment.com expertmaker.com fairmarket.com fragrancebay.com francemail.com half.com '
        'half.com.cn half.tv halfcanada.com halfjapan.com handbagsoutletebay.com iebay.com irribay.com '
        'itsbetterwhenyouwinit.com liketwice.com liveauction.com milofetch.com musicbay.net '
        'myconstructionworld.net myebay.com nebay.net paisapay.cc paisapay.info paisapay.tv premobay.com '
        'privatemarketplaces.net privatemarketplaces.us prostores.cn prostores.com prostores.com.cn '
        'rethink.net shopibay.net shoping.com sourcingforebay.com.cn sourcingforebay.net '
        'sourcingforebay.tv speybay.com storesense.com svpply.com telebay.com telesell.com texttobuy.org '
        'theebayshop.com theopportunityproject.org towerauction.com vendu.com watch-ebay.org '
        'weareebay.com wwwdecide.com wwwebay.com wwwebay.net wwwwebay.com xindelu.com xn-- '
        '3et96bj49ahpq.com xn--4vq475g.com xn--4vq477m.com xn--7hv594h.com xn--7hvy28f.cn xn--hb4aw0g.com '
        'xn--q41am8x.com xn--qoq462m.com xn--tkry91n.com xn--ubt498knmf.com xn--xsq421m.com xn-- '
        'xsq605n.com xn--xsq959n.com xn--yf1at58a.com xxbay.com yibei.org'),
    'espn.com': ('STREAMING',
        'es.pn espn.cl espn.co.uk espn.com espn.com.ar espn.com.au espn.com.co espn.com.ec espn.com.mx '
        'espn.com.pa espn.com.pe espn.com.uy espn.com.ve espn.in espn.net espncdn.com espncricinfo.com'),
    'fdjunited.com': ('GAMING',
        '32red.co.uk 32red.com aleda.fr bingo.com fdj.fr fdjunited.com fdjunitedaffiliates.com '
        'laddition.com lottery.ie mariacasino.dk mariacasino.ee mariacasino.se nirio.fr ottocasino.com '
        'sportinggroup.com sportingindex.com storspelare.se unibet.be unibet.co.uk unibet.dk unibet.ee '
        'unibet.fr unibet.it unibet.nl unibet.ro unibet.se vladcazino.ro zeturf.fr'),
    'fifa.com': ('STREAMING',
        'fifa.com fifaplus.com'),
    'flickr.com': ('CLOUD',
        'flic.kr flickr.com flickr.net flickrprints.com flickrpro.com staticflickr.com'),
    'globoplay.globo.com': ('STREAMING',
        'cloud-jarvis.globo.com globoplay.com globoplay.com.br globoplay.globo.com'),
    'gog.com': ('GAMING',
        'gog-cdn-lumen.secure2.footprint.net gog-statics.com gog.com gogalaxy.com'),
    'grindr.com': ('SOCIAL',
        'grindr.com grindr.mobi'),
    'grok.com': ('AI',
        'grok.com x.ai'),
    'hulu.com': ('STREAMING',
        'hulu.com'),
    'iheart.com': ('STREAMING',
        '937theriver.com iheart.com iheart.mx iheartmedia.com iheartradio.ca iheartradio.co.nz '
        'iheartradio.com ihrdev.com ihrhls.com ihrint.com ihrstage.com'),
    'imgur.com': ('CLOUD',
        'imgur.com'),
    'ioi.dk': ('GAMING',
        'hitman.com hitman.io ioi.dk'),
    'iqiyi.com': ('STREAMING',
        'iq.com iqiyi.com iqiyipic.com pps.tv ppsimg.com qiyi.com qiyipic.com qy.net'),
    'kakao.com': ('MESSAGING',
        'kakao.com kgslb.com'),
    'kik.com': ('MESSAGING',
        'kik.com'),
    'kookapp.cn': ('SOCIAL',
        'kaiheila.cn kookapp.cn'),
    'lazada.com': ('SHOPPING',
        'k1-lazadasg-oversea.gslb.ksyuncdn.com lazada.co.id lazada.co.th lazada.com lazada.com.my '
        'lazada.com.ph lazada.sg lazada.vn slatic.net'),
    'line.me': ('SOCIAL',
        'gcld-line.com lin.ee line-apps-beta.com line-apps-rc.com line-apps.com line-cdn.net line- '
        'scdn.net line.biz line.me line.naver.jp linecorp.com linefriends.com linefriends.com.tw '
        'linegame.jp linemobile.com linemyshop.com lineshoppingseller.com linetv.tw'),
    'linkedin.com': ('SOCIAL',
        'bizographics.com cs1404.wpc.epsiloncdn.net cs767.wpc.epsiloncdn.net l-0005.dc-msedge.net '
        'l-0005.l-dc-msedge.net l-0005.l-msedge.net l-0015.l-msedge.net licdn.cn licdn.com linkedin.at '
        'linkedin.be linkedin.cn linkedin.com linkedin.nl linkedin.qtlcdn.com lnkd.in'),
    'lionsgateplus.com': ('STREAMING',
        'lionsgateplus.com starz.com'),
    'looke.com.br': ('STREAMING',
        'looke.com.br ottvs.com.br'),
    'mail.ru': ('SOCIAL',
        'imgsmail.ru mail.ru mycdn.me'),
    'manus.im': ('AI',
        'manus.im manuscdn.com'),
    'mastodon.social': ('SOCIAL',
        'aus.social awscommunity.social climatejustice.social cupoftea.social cyberplace.social '
        'defcon.social det.social glasgow.social h4.io hachyderm.io hessen.social hostux.social ieji.de '
        'indieweb.social infosec.exchange ioc.exchange kolektiva.social livellosegreto.it lor.sh lou.lt '
        'm.cmx.im mas.to masto.ai masto.es masto.nu masto.pt mastodon.au mastodon.bida.im mastodon.com.tr '
        'mastodon.eus mastodon.green mastodon.ie mastodon.iriseden.eu mastodon.nl mastodon.nu mastodon.nz '
        'mastodon.online mastodon.scot mastodon.sdf.org mastodon.social mastodon.top mastodon.uno '
        'mastodon.world mastodon.zaclys.com mastodonapp.uk mastodont.cat mastodontech.de mastodontti.fi '
        'mastouille.fr mathstodon.xyz metalhead.club mindly.social mstdn.ca mstdn.jp mstdn.party '
        'mstdn.plus mstdn.social muenchen.social muenster.im nerdculture.de noc.social norden.social '
        'nrw.social o3o.ca ohai.social piaille.fr pol.social ravenation.club rollenspiel.social '
        'ruby.social ruhr.social sfba.social socel.net social.anoxinon.de social.cologne social.dev- '
        'wiki.de social.linux.pizza social.politicaconciencia.org social.vivaldi.net stranger.social '
        'sueden.social tech.lgbt techhub.social theblower.au tkz.one todon.eu toot.aquilenet.fr '
        'toot.community toot.funami.tech toot.io toot.wales troet.cafe union.place universeodon.com '
        'urbanists.social wien.rocks wxw.moe'),
    'max.ru': ('MESSAGING',
        'max.ru'),
    'meta.ai': ('AI',
        'meta.ai'),
    'minecraft.net': ('GAMING',
        'cubecraft.net megasmp.gg minecraft-services.net minecraft.net minecraftservices.com mojang.com'),
    'nebula.tv': ('STREAMING',
        'nebula.app nebula.tv'),
    'nvidia.com': ('WORK',
        'geforce.com geforcenow.com nvidia.cn nvidia.com nvidia.com.global.ogslb.com nvidia.eu '
        'nvidia.partners nvidiagrid.net nvidianews.com tegrazone.com'),
    'odysee.com': ('SOCIAL',
        'odycdn.com odysee.com odysee.live odysee.tv'),
    'ok.ru': ('SOCIAL',
        'insideok.ru ok.games ok.ru okcdn.ru oktech.ru st.mycdn.me'),
    'olvid.io': ('MESSAGING',
        'olvid-attachment-chunks.s3.eu-west-3.amazonaws.com olvid.io'),
    'onlyfans.com': ('SOCIAL',
        'onlyfans.com'),
    'paramountplus.com': ('STREAMING',
        'paramountplus.com pplusstatic.com'),
    'peacocktv.com': ('STREAMING',
        'peacock.com peacocktv.com'),
    'pinterest.com': ('SOCIAL',
        'pin.it pinimg.com pinterest.at pinterest.be pinterest.ca pinterest.ch pinterest.cl pinterest.co '
        'pinterest.co.at pinterest.co.in pinterest.co.kr pinterest.co.nz pinterest.co.uk pinterest.com '
        'pinterest.com.au pinterest.com.bo pinterest.com.ec pinterest.com.mx pinterest.com.pe '
        'pinterest.com.py pinterest.com.uy pinterest.com.vn pinterest.de pinterest.dk pinterest.ec '
        'pinterest.engineering pinterest.es pinterest.fr pinterest.hu pinterest.id pinterest.ie '
        'pinterest.in pinterest.info pinterest.it pinterest.jp pinterest.kr pinterest.mx pinterest.nl '
        'pinterest.nz pinterest.pe pinterest.ph pinterest.pt pinterest.ru pinterest.se pinterest.th '
        'pinterest.tw pinterest.uk pinterest.vn pinterestmail.com'),
    'play.google.com': ('WORK',
        'play-fe.googleapis.com play-lh.googleusercontent.com prod-lt-playstoregatewayadapter- '
        'pa.googleapis.com'),
    'pof.com': ('SOCIAL',
        'pof.com'),
    'plex.tv': ('STREAMING',
        'plex.bz plex.direct plex.tv plexapp.com'),
    'pluto.tv': ('STREAMING',
        'pluto.tv'),
    'privacy.com.br': ('CLOUD',
        'privacy.com.br'),
    'proton.me': ('CLOUD',
        'pm.me proton.me protonmail.ch protonmail.com protonvpn.com'),
    'qq.com': ('STREAMING',
        'qq-video.cdn-go.cn url.cn'),
    'questionai.ai': ('AI',
        'questionai.ai questionai.com'),
    'qwen.ai': ('AI',
        'qwen.ai qwenlm.ai'),
    'viki.com': ('STREAMING',
        'm-content-viki.s.llnwi.net viki.com viki.io'),
    'reddit.com': ('SOCIAL',
        'redd.it reddit.com redditmail.com redditmedia.com redditstatic.com'),
    'rockstargames.com': ('GAMING',
        'rockstargames.com rsg.sc'),
    'samsungtvplus.com': ('STREAMING',
        'internetat.tv samsung.wurl.tv samsungcloud.tv samsungtvplus.com'),
    'shellshock.io': ('GAMING',
        'algebra.best algebra.monster algebra.vip biologyclass.club combateggs.com deadlyegg.com '
        'deathegg.life deathegg.world egg.dance eggbattle.com eggboy.club eggboy.me eggboy.xyz '
        'eggcombat.com eggfacts.fun egggames.best egghead.institute eggisthenewblack.com eggsarecool.com '
        'eggshock.com eggshock.me eggshock.net eggshooter.best eggshooter.com eggwarfare.com eggwars.io '
        'geometry.best geometry.monster geometry.pw geometry.report hardboiled.life hardshell.life '
        'humanorganising.org math.international mathactivity.club mathactivity.xyz mathdrills.info '
        'mathdrills.life mathfun.rocks mathgames.world mathlete.fun mathlete.pro overeasy.club '
        'risenegg.com scrambled.tech scrambled.today scrambled.us scrambled.world shellgame.me '
        'shellplay.live shellshock.guru shellshock.io shellshockers.best shellshockers.ca '
        'shellshockers.club shellshockers.life shellshockers.site shellshockers.today shellshockers.us '
        'shellshockers.website shellshockers.wiki shellshockers.world shellshockers.xyz shellsocks.com '
        'softboiled.club urbanegger.com violentegg.club violentegg.fun yolk.best yolk.life yolk.monster '
        'yolk.quest yolk.rocks yolk.tech yolk.today zygote.cafe'),
    'signal.org': ('MESSAGING',
        'signal.org whispersystems.org'),
    'skype.com': ('MESSAGING',
        'edge-skype-com.s-0001.s-msedge.net skype-edf.akadns.net skype.com skype.net skypeassets.com '
        'skypeassets.net skypedata.akadns.net'),
    'snapchat.com': ('SOCIAL',
        'impala-media-production.s3.amazonaws.com sc-cdn.net snap-dev.net snapads.com snapchat.com '
        'snapkit.co'),
    'soundcloud.com': ('STREAMING',
        'sndcdn.com soundcloud.com'),
    'tidal.com': ('STREAMING',
        'tidal.com'),
    'tinder.com': ('SOCIAL',
        'gotinder.com tinder.com tindersparks.com'),
    'tumblr.com': ('SOCIAL',
        'tumblr.com'),
    'ubisoft.com': ('GAMING',
        'ubi.com ubisoft.com ubisoft.org ubisoftconnect.com'),
    'playvalorant.com': ('GAMING',
        'playvalorant.com valorant.scd.riotcdn.net valorant.secure.dyn.riotcdn.net'),
    'viber.com': ('MESSAGING',
        'viber.com'),
    'vimeo.com': ('STREAMING',
        'livestream.com vhx.tv vhxqa1.com vhxqa2.com vhxqa3.com vhxqa4.com vhxqa6.com vimeo-staging.com '
        'vimeo-staging2.com vimeo.com vimeo.fr vimeobusiness.com vimeocdn.com vimeogoods.com '
        'vimeoondemand.com vimeostatus.com'),
    'vivoplay.com.br': ('STREAMING',
        'vivoplay.com.br vivoplay.net vivotv.com.br'),
    'vk.com': ('SOCIAL',
        'mvk.com userapi.com vk-cdn.me vk-cdn.net vk-portal.net vk.cc vk.com vk.design vk.link vk.me '
        'vkcache.com vkgo.app vklive.app vkmessenger.app vkmessenger.com vkontakte.ru vkuseraudio.com '
        'vkuserlive.net vkuservideo.com vkuservideo.net'),
    'voot.com': ('STREAMING',
        'voot.com'),
    'wargaming.net': ('GAMING',
        'wargaming.com wargaming.net wgcdn.co wgcrowd.io worldoftanks.com worldofwarplanes.com '
        'worldofwarships.eu wotblitz.com'),
    'warnerbrosgames.com': ('GAMING',
        'warnerbrosgames.com'),
    'wechat.com': ('MESSAGING',
        'wechat.com weixin.qq.com weixin.qq.com.cn weixinbridge.com wx.qq.com'),
    'weibo.com': ('STREAMING',
        'wbimg.cn wbimg.com wcdn.cn weibo.cn weibo.com weibo.com.cn weibocdn.com'),
    'wizzapp.com': ('SOCIAL',
        'getwizz.io wizz.chat wizzapp.com'),
    'xiaohongshu.com': ('SHOPPING',
        'xhscdn.com xhscdn.net xiaohongshu.com xiaohongshu.com.my xiaohongshu.net'),
    'yy.com': ('STREAMING',
        'yy.com'),
    'zhihu.com': ('SOCIAL',
        'zhihu.com zhimg.com'),
}

# Dominio/categoría por regla: {sufijo_de_dominio: (dominio_canónico, CATEGORÍA)} — O(1) por etiqueta.
ADGUARD_DOMAIN_CATALOG: dict[str, tuple[str, str]] = {
    _dom: (_canon, _cat) for _canon, (_cat, _doms) in _SERVICE_RULES.items() for _dom in _doms.split()
}

# Prefijos IPv4 conocidos (cuando DoH oculta el DNS y solo se ve la IP remota). Clave = prefijo con punto final.
_NDPI_IP_GROUPS = (
    (("facebook.com", "SOCIAL"), "157.240. 31.13. 69.171. 69.63. 173.252. 185.60.216. 129.134. 179.60.192."),
    (("netflix.com", "STREAMING"), "23.246. 37.77.184. 45.57. 64.120.128. 66.197.128. 108.175.32. 185.2.220. 198.38.96. 198.45.48. 208.75.76."),
    (("telegram.org", "MESSAGING"), "149.154.160-175. 91.108.4-19. 91.108.56-59."),
    (("twitch.tv", "STREAMING"), "52.223.192. 99.181.64. 192.16.64. 199.9.248."),
    (("spotify.com", "STREAMING"), "35.186.224. 104.154.127."),
    (("roblox.com", "GAMING"), "128.116."),
    (("steampowered.com", "GAMING"), "155.133. 162.254.192. 103.28.54."),
    (("google.com", "WORK"), "142.250. 142.251. 172.217. 216.58. 74.125. 172.253. 209.85. 108.177."),
    (("microsoft.com", "WORK"), "13.107. 20.190. 40.96. 40.97. 40.98. 40.99. 40.100. 40.101. 40.102. 40.126. 52.96. 52.97. 52.98."),
    (("cloudflare.com", "CLOUD"),
     "104.16. 104.17. 104.18. 104.19. 104.20. 104.21. 104.22. 104.24. 104.25. 104.26. 104.27. 172.64. 172.67. 1.1.1. 1.0.0."),
)


def _expand_ip_prefix(prefix: str):
    """'149.154.160-175.' -> '149.154.160.', ..., '149.154.175.' (rango del último octeto del prefijo)."""
    head, _, last = prefix.rstrip(".").rpartition(".")
    if "-" not in last:
        yield prefix
        return
    lo, hi = last.split("-")
    for n in range(int(lo), int(hi) + 1):
        yield f"{head}.{n}."


NDPI_IP_PREFIX_CATALOG: dict[str, tuple[str, str]] = {
    _p: _svc for _svc, _prefixes in _NDPI_IP_GROUPS for _raw in _prefixes.split() for _p in _expand_ip_prefix(_raw)
}

# Categorías "de servicio" que nunca deben ser pisadas por entradas DNS genéricas (Google/Cloudflare/Akamai).
SERVICE_PRIORITY_CATEGORIES = ("STREAMING", "SOCIAL", "MESSAGING", "AI", "GAMING", "SHOPPING")


def lookup_service_catalog(raw_domain_or_ip: str):
    """Busca solo en los catálogos (sin heurísticas). Retorna (dominio_canónico, categoría) o None."""
    val = (raw_domain_or_ip or "").strip().lower().rstrip(".")
    if not val:
        return None
    parts = val.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        hit = NDPI_IP_PREFIX_CATALOG.get(".".join(parts[:3]) + ".")
        return hit or NDPI_IP_PREFIX_CATALOG.get(".".join(parts[:2]) + ".")
    for i in range(len(parts) - 1):  # a.b.c.com -> b.c.com -> c.com
        hit = ADGUARD_DOMAIN_CATALOG.get(".".join(parts[i:]))
        if hit:
            return hit
    return None


def match_service_domain_or_ip(raw_domain_or_ip: str, app_name: str = "") -> tuple[str, str]:
    """
    Normaliza un dominio/CDN/IP al servicio canónico y su categoría ejecutiva.
    Retorna (clean_domain, category_code). Orden: catálogo AdGuard (sufijos) -> prefijos IP nDPI ->
    dominio base + categoría por aplicación.
    """
    val = (raw_domain_or_ip or "").strip().lower().rstrip(".")
    if not val:
        return "", "OTHER"
    hit = lookup_service_catalog(val)
    if hit:
        return hit

    # Descartar ruido técnico puro de infraestructura inversa (PTR de ISP genéricos o ARPA)
    if val.endswith((".in-addr.arpa", ".ip6.arpa", ".local", ".lan", ".home", ".internal")):
        return "", "OTHER"

    # Dominio estándar (no IP): reducir subdominios excesivos al dominio base legible
    parts = [p for p in val.split(".") if p]
    is_ipv4 = len(parts) == 4 and all(p.isdigit() for p in parts)
    if not is_ipv4 and ":" not in val and len(parts) >= 3:
        if len(parts[-1]) == 2 and parts[-2] in ("com", "net", "org", "gob", "edu", "co"):
            val = ".".join(parts[-3:])
        elif parts[-3] in ("web", "mail", "chat", "drive", "docs", "meet", "teams", "api", "portal"):
            val = ".".join(parts[-3:])
        else:
            val = ".".join(parts[-2:])

    app_low = (app_name or "").lower()
    if any(k in app_low for k in ("anydesk", "teamviewer", "rustdesk", "mstsc")):
        return val, "REMOTE"
    if any(k in app_low for k in ("spotify", "vlc", "music")):
        return val, "STREAMING"
    if any(k in app_low for k in ("onedrive", "dropbox", "googledrive")):
        return val, "CLOUD"
    if any(k in app_low for k in ("code", "cursor", "antigravity", "pycharm", "terminal", "iterm", "git", "ssh")):
        return val, "WORK"

    if any(k in val for k in ("video", "stream", "media", "cdn")):
        return val, "STREAMING"
    if any(k in val for k in ("google.com", "gstatic.com", "googleapis.com", "microsoft.com", "bing.com", "cloudflare.com", "amazonaws.com", "akamai")):
        return val, "WORK"
    if any(k in val for k in ("apple.com", "digicert.com", "ocsp", "msn.com", "live.com")):
        return val, "SYSTEM"
    return val, "OTHER"


def classify_domain_ndpi(raw_domain_or_ip: str, app_name: str = "") -> tuple[str, str]:
    """Mini-Función NF-1.5: compat; delega al catálogo AdGuard + nDPI."""
    return match_service_domain_or_ip(raw_domain_or_ip, app_name)


class NetworkTrafficCollector:
    """
    Mini-Funciones NF-1.2, NF-1.3 y NF-1.4:
    Colector silencioso de ancho de banda (RX/TX bytes y Kbps) e inspector de conexiones
    TCP/UDP y dominios DNS sin requerir drivers externos (Npcap) ni abrir puertos en el cliente.
    """

    def __init__(self, server_url: str = ""):
        self.server_host = ""
        try:
            # Extraer IP/host del propio servidor JOB-Monitor para excluirlo del conteo de dominios del usuario
            clean_u = server_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].strip()
            self.server_host = clean_u.lower()
        except Exception:
            self.server_host = ""

        rx0, tx0 = get_network_io_counters()
        self.last_rx_bytes: int = rx0
        self.last_tx_bytes: int = tx0
        self.last_sample_ts: float = time.monotonic()

        self._dns_ip_to_domain: dict[str, str] = {}
        self._ptr_cache: dict[str, tuple[str, float]] = {}
        self._pid_to_name_cache: dict[int, tuple[str, float]] = {}
        self._last_deep_scan_ts: float = 0.0
        self._cached_domain_hits: list[dict] = []

    def _is_private_or_ignored_ip(self, ip_str: str) -> bool:
        ip = (ip_str or "").strip().lower()
        if not ip or ip in ("*", "0.0.0.0", "127.0.0.1", "::1", "::", "[::]", "[::1]"):
            return True
        if self.server_host and ip == self.server_host:
            return True
        if ip.startswith(("127.", "10.", "192.168.", "169.254.", "224.", "239.", "255.", "fe80:", "fc00:", "fd00:")):
            return True
        if ip.startswith("172."):
            parts = ip.split(".")
            if len(parts) == 4 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
                return True
        return False

    _DNS_SPECIFIC = (
        "youtube.com", "googlevideo.com", "ytimg.com", "netflix.com", "whatsapp.com", "facebook.com",
        "instagram.com", "tiktok.com", "spotify.com", "chatgpt.com", "claude.ai", "mail.google.com",
        "meet.google.com", "drive.google.com",
    )
    _DNS_GENERIC = (
        "google.com", "googleusercontent.com", "gstatic.com", "1e100.net", "gvt1.com", "gvt2.com",
        "cloudflare.com", "akamai.net", "amazonaws.com",
    )

    @classmethod
    def _dns_priority(cls, domain: str) -> int:
        d = (domain or "").lower()
        hit = lookup_service_catalog(d)
        if hit and hit[1] in SERVICE_PRIORITY_CATEGORIES:
            return 2
        if any(d == k or d.endswith("." + k) for k in cls._DNS_SPECIFIC):
            return 2
        if any(d == k or d.endswith("." + k) for k in cls._DNS_GENERIC):
            return 0
        return 1

    def _refresh_windows_dns_cache(self):
        """Lee silenciosamente ipconfig /displaydns en Windows para mapear IPs a nombres de dominio exactos."""
        if not IS_WINDOWS:
            return
        no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            out = subprocess.check_output(
                ["ipconfig", "/displaydns"],
                stderr=subprocess.DEVNULL,
                timeout=2.5,
                creationflags=no_win,
            ).decode("utf-8", errors="ignore")
            current_domain = ""
            for raw_line in out.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if ":" in line:
                    k, v = [x.strip() for x in line.split(":", 1)]
                    kl = k.lower()
                    if ("record name" in kl or "nombre de registro" in kl) and v and "." in v:
                        current_domain = v.lower().rstrip(".")
                    elif ("a (host)" in kl or "aaaa" in kl or "dirección" in kl or "address" in kl) and v and current_domain:
                        ip_candidate = v.strip()
                        if "." in ip_candidate or ":" in ip_candidate:
                            prev = self._dns_ip_to_domain.get(ip_candidate)
                            if prev is None or self._dns_priority(current_domain) >= self._dns_priority(prev):
                                self._dns_ip_to_domain[ip_candidate] = current_domain
                elif "." in line and " " not in line and not line.startswith("-"):
                    current_domain = line.lower().rstrip(".")
        except Exception:
            pass

    def _resolve_ip_to_domain(self, ip_str: str) -> str:
        if ip_str in self._dns_ip_to_domain:
            return self._dns_ip_to_domain[ip_str]

        now = time.monotonic()
        cached = self._ptr_cache.get(ip_str)
        if cached and (now - cached[1]) < 1800.0:
            return cached[0]

        resolved = ""
        try:
            old_to = socket.getdefaulttimeout()
            socket.setdefaulttimeout(0.35)
            try:
                host, _, _ = socket.gethostbyaddr(ip_str)
                if host and "." in host:
                    resolved = host.lower().rstrip(".")
            finally:
                socket.setdefaulttimeout(old_to)
        except Exception:
            resolved = ""

        self._ptr_cache[ip_str] = (resolved, now)
        if len(self._ptr_cache) > 600:
            # Limpiar entradas antiguas de caché PTR
            cutoff = now - 900.0
            self._ptr_cache = {k: v for k, v in self._ptr_cache.items() if v[1] >= cutoff}
        return resolved

    def _refresh_windows_pid_map(self):
        if not IS_WINDOWS:
            return
        now = time.monotonic()
        no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            out = subprocess.check_output(
                ["tasklist", "/nh", "/fo", "csv"],
                stderr=subprocess.DEVNULL,
                timeout=2.5,
                creationflags=no_win,
            ).decode("utf-8", errors="ignore")
            for line in out.splitlines():
                parts = [p.strip('"') for p in line.strip().split('","')]
                if len(parts) >= 2 and parts[1].isdigit():
                    self._pid_to_name_cache[int(parts[1])] = (parts[0], now)
        except Exception:
            pass

    # --- Inferencia de sitio por título de ventana (YouTube vs Google genérico) ---
    _BROWSER_KEYS = (
        "chrome", "msedge", "edge", "brave", "safari", "firefox", "opera", "vivaldi",
        "arc", "webkit.networking", "webkit", "chromium", "google",
    )
    _TITLE_SITE_RULES = (
        (("youtube",), "youtube.com", "STREAMING"),
        (("netflix",), "netflix.com", "STREAMING"),
        (("twitch",), "twitch.tv", "STREAMING"),
        (("spotify",), "spotify.com", "STREAMING"),
        (("tiktok",), "tiktok.com", "SOCIAL"),
        (("facebook",), "facebook.com", "SOCIAL"),
        (("instagram",), "instagram.com", "SOCIAL"),
        (("whatsapp",), "whatsapp.com", "MESSAGING"),
        (("telegram",), "telegram.org", "MESSAGING"),
        (("discord",), "discord.com", "MESSAGING"),
        (("chatgpt", "openai"), "chatgpt.com", "AI"),
        (("claude",), "claude.ai", "AI"),
        (("gemini",), "gemini.google.com", "AI"),
        (("github",), "github.com", "WORK"),
        (("gmail", "correo"), "mail.google.com", "WORK"),
        (("google meet", "meet.google"), "meet.google.com", "WORK"),
        (("google drive",), "drive.google.com", "CLOUD"),
        (("onedrive",), "onedrive.live.com", "CLOUD"),
        (("wetransfer",), "wetransfer.com", "CLOUD"),
    )
    _GOOGLE_FAMILY_SITES = (
        "youtube.com", "mail.google.com", "drive.google.com", "gemini.google.com", "meet.google.com",
    )
    _GOOGLE_DOMAIN_KEYS = ("1e100.net", "google.com", "gstatic.com", "googlevideo.com", "gvt1.com", "gvt2.com", "googleapis.com",
                           "googleusercontent.com", "ggpht.com", "ytimg.com", "youtu.be")
    _GOOGLE_IP_PREFIXES = ("142.250.", "142.251.", "172.217.", "216.58.", "74.125.", "172.253.", "209.85.", "108.177.")
    _EXPLICIT_DOMAIN_RE = re.compile(
        r"\b((?:[a-z0-9-]+\.)+(?:com|net|org|io|tv|co|app|dev|ai|edu|gob|gov|mx|ar|es|info|me|us|tech|cloud|xyz))\b",
        re.IGNORECASE,
    )

    @classmethod
    def _is_browser_name(cls, name: str) -> bool:
        low = strip_zero_width(name).lower()
        return bool(low) and any(k in low for k in cls._BROWSER_KEYS)

    @classmethod
    def _infer_site_from_window_title(cls, window_title: str, app_name: str = "") -> Optional[tuple[str, str]]:
        """Devuelve (dominio, categoría) deducido del título de ventana, o None."""
        title = strip_zero_width(window_title).strip().lower()
        app_name = strip_zero_width(app_name)
        if not title:
            return None
        for keys, domain, cat in cls._TITLE_SITE_RULES:
            if any(k in title for k in keys):
                return domain, cat
        m = cls._EXPLICIT_DOMAIN_RE.search(title)
        if m:
            clean, cat = classify_domain_ndpi(m.group(1), app_name)
            if clean:
                return clean, cat
        return None

    def _is_google_remote(self, ip_str: str, raw_dom: str) -> bool:
        dom = (raw_dom or "").lower()
        if any(dom == k or dom.endswith("." + k) for k in self._GOOGLE_DOMAIN_KEYS):
            return True
        return (ip_str or "").startswith(self._GOOGLE_IP_PREFIXES)

    def _register_conn(self, conns: dict, remote_ip: str, proc_name: str, site_hint: Optional[tuple[str, str]], weight: int = 1):
        raw_dom = self._resolve_ip_to_domain(remote_ip) or remote_ip
        clean_dom, cat = classify_domain_ndpi(raw_dom, proc_name)
        # Anycast de Google: atribuir el flujo al sitio exacto que indica la ventana activa
        if (
            site_hint
            and site_hint[0] in self._GOOGLE_FAMILY_SITES
            and (self._is_browser_name(proc_name) or not (proc_name or "").strip())
            and self._is_google_remote(remote_ip, raw_dom)
        ):
            clean_dom, cat = site_hint
        if not clean_dom:
            return
        k = (clean_dom, proc_name[:40])
        if k not in conns:
            conns[k] = {"domain": clean_dom, "app_name": proc_name[:40], "category": cat, "connections": weight}
        else:
            conns[k]["connections"] += weight

    def _scan_active_connections(self, fallback_app: str, window_title: str = "") -> list[dict]:
        """
        Inspecciona las conexiones TCP/UDP (incl. QUIC/HTTP3 en 443) activas y las cruza con
        procesos, dominios y el sitio inferido de la ventana activa.
        """
        conns: dict[tuple[str, str], dict] = {}
        no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        site_hint = self._infer_site_from_window_title(window_title, fallback_app)
        quic_procs: set[str] = set()

        if IS_WINDOWS:
            self._refresh_windows_dns_cache()
            self._refresh_windows_pid_map()
            for proto in ("tcp", "udp"):
                try:
                    out = subprocess.check_output(
                        ["netstat", "-ano", "-p", proto],
                        stderr=subprocess.DEVNULL,
                        timeout=2.5,
                        creationflags=no_win,
                    ).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                for line in out.splitlines():
                    parts = line.split()
                    try:
                        if proto == "tcp":
                            if len(parts) < 5 or parts[0].upper() != "TCP":
                                continue
                            if parts[3].upper() not in ("ESTABLISHED", "SYN_SENT", "TIME_WAIT"):
                                continue
                            foreign, pid_s = parts[2], parts[4]
                        else:
                            if len(parts) < 4 or parts[0].upper() != "UDP":
                                continue
                            foreign, pid_s = parts[2], parts[-1]
                            local = parts[1]
                            pid = int(pid_s) if pid_s.isdigit() else 0
                            pname = self._pid_to_name_cache.get(pid, ("", 0.0))[0]
                            # QUIC: sockets UDP sin destino fijo de un navegador (excluye mDNS/SSDP)
                            if self._is_browser_name(pname) and not local.endswith((":5353", ":1900", ":137", ":138")) \
                                    and (foreign.startswith("*") or foreign.endswith(":443")):
                                quic_procs.add(pname[:40])
                            if ":" not in foreign or foreign.startswith("*"):
                                continue
                        if ":" not in foreign:
                            continue
                        remote_ip, remote_port = foreign.rsplit(":", 1)
                        remote_ip = remote_ip.strip("[]")
                        if self._is_private_or_ignored_ip(remote_ip):
                            continue
                        if proto == "udp" and remote_port != "443":
                            continue
                        pid = int(pid_s) if pid_s.isdigit() else 0
                        proc_name = self._pid_to_name_cache.get(pid, ("", 0.0))[0] or fallback_app or "Sistema"
                        if proc_name.lower() in ("python.exe", "pythonw.exe") and remote_port == "5080":
                            continue
                        self._register_conn(conns, remote_ip, proc_name, site_hint)
                    except Exception:
                        continue

        elif IS_MAC:
            try:
                out = subprocess.check_output(
                    ["/usr/sbin/lsof", "+c", "0", "-i4", "-i6", "-n", "-P"],
                    stderr=subprocess.DEVNULL,
                    timeout=3.0,
                ).decode("utf-8", errors="ignore")
            except subprocess.CalledProcessError as e:
                # lsof devuelve código 1 si algún archivo no es accesible pero imprime resultados válidos
                out = (e.output or b"").decode("utf-8", errors="ignore")
            except Exception:
                out = ""
            for line in out.splitlines()[1:]:
                try:
                    parts = line.split()
                    if len(parts) < 9:
                        continue
                    proc_name = parts[0].replace("\\x20", " ").strip()
                    if "JOBMonitorAgent" in proc_name:
                        continue
                    proto = parts[7].upper()
                    name_col = parts[8]
                    if proto == "TCP":
                        if "(ESTABLISHED)" not in line or "->" not in name_col:
                            continue
                    elif proto == "UDP":
                        local_part = name_col.split("->", 1)[0]
                        if "->" not in name_col:
                            # Socket UDP sin destino (típico de QUIC en navegadores)
                            if self._is_browser_name(proc_name) and not local_part.endswith((":5353", ":1900")):
                                quic_procs.add(proc_name[:40])
                            continue
                    else:
                        continue
                    remote_part = name_col.split("->", 1)[1]
                    if ":" not in remote_part:
                        continue
                    remote_ip, remote_port = remote_part.rsplit(":", 1)
                    remote_ip = remote_ip.strip("[]")
                    if self._is_private_or_ignored_ip(remote_ip):
                        continue
                    if proto == "UDP" and remote_port != "443":
                        continue
                    if proc_name.lower().startswith("python") and remote_port == "5080":
                        continue
                    self._register_conn(conns, remote_ip, proc_name, site_hint)
                except Exception:
                    continue

        # Navegadores con ventana activa en un sitio conocido (QUIC/HTTP3 o conexiones multiplexadas en Edge/Chrome/Safari)
        if site_hint and (quic_procs or self._is_browser_name(fallback_app) or self._is_browser_name(window_title)):
            target_procs = quic_procs or {(fallback_app or "Browser")[:40]}
            for pname in target_procs:
                k = (site_hint[0], pname)
                if k not in conns:
                    conns[k] = {"domain": site_hint[0], "app_name": pname, "category": site_hint[1], "connections": 4}
                else:
                    conns[k]["connections"] += 2

        results = sorted(conns.values(), key=lambda x: x["connections"], reverse=True)
        return results[:15]

    def collect_slice(self, active_app_name: str = "Escritorio", active_window_title: str = "", is_background: bool = False) -> dict:
        """
        Mini-Función NF-1.6: Calcula el diferencial de bytes (RX/TX), la velocidad en Kbps
        y las conexiones a dominios activas para inyectarlas en el paquete de telemetría o bóveda .sitai.
        """
        now = time.monotonic()
        elapsed = max(0.5, now - self.last_sample_ts)
        rx_now, tx_now = get_network_io_counters()

        if self.last_rx_bytes > 0 and rx_now >= self.last_rx_bytes:
            delta_rx = rx_now - self.last_rx_bytes
        else:
            delta_rx = 0

        if self.last_tx_bytes > 0 and tx_now >= self.last_tx_bytes:
            delta_tx = tx_now - self.last_tx_bytes
        else:
            delta_tx = 0

        self.last_rx_bytes = rx_now
        self.last_tx_bytes = tx_now
        self.last_sample_ts = now

        if is_background:
            # Sesión Windows en segundo plano: netstat/contadores son de toda la máquina; no duplicar tráfico
            return {
                "delta_rx_bytes": 0, "delta_tx_bytes": 0,
                "rx_kbps": 0.0, "tx_kbps": 0.0, "domains": [],
            }

        rx_kbps = round((delta_rx * 8.0) / (elapsed * 1000.0), 1)
        tx_kbps = round((delta_tx * 8.0) / (elapsed * 1000.0), 1)

        # Escaneo en cada ciclo (sin retardo) para reflejar el sitio activo al instante
        self._last_deep_scan_ts = now
        hits = [dict(h) for h in self._scan_active_connections(active_app_name, active_window_title)]

        total_slice_bytes = delta_rx + delta_tx

        # Inyectar siempre el sitio inferido de la ventana activa si es un navegador con tráfico
        site = self._infer_site_from_window_title(active_window_title, active_app_name)
        if site and self._is_browser_name(active_app_name) and (total_slice_bytes > 2048 or hits):
            weight = max(1, sum(max(1, int(h.get("connections", 1))) for h in hits))
            existing = next((h for h in hits if h["domain"] == site[0]), None)
            if existing:
                existing["connections"] = max(int(existing["connections"]), weight)
            else:
                hits.insert(0, {
                    "domain": site[0], "app_name": (active_app_name or "Navegador")[:40],
                    "category": site[1], "connections": weight,
                })
        self._cached_domain_hits = hits

        total_conns = sum(max(1, int(d.get("connections", 1))) for d in hits) or 1
        domain_hits_payload = []
        for item in hits:
            conns = max(1, int(item.get("connections", 1)))
            est_bytes = int(round(total_slice_bytes * (conns / float(total_conns))))
            domain_hits_payload.append({
                "domain": item["domain"],
                "app_name": item["app_name"],
                "category": item["category"],
                "connections": conns,
                "estimated_bytes": est_bytes,
            })

        return {
            "delta_rx_bytes": int(delta_rx),
            "delta_tx_bytes": int(delta_tx),
            "rx_kbps": rx_kbps,
            "tx_kbps": tx_kbps,
            "domains": domain_hits_payload,
        }


class KeyboardEventHook:
    """
    Mini-Función 01 (MF-01): KeyboardEventHook
    Especificación técnica EventosHUI1.md:
    - Escucha no bloqueante en hilo daemon independiente.
    - Normalización de caracteres y teclas especiales comprensibles.
    - Descarte de eventos duplicados on_release.
    - Ciclo de vida limpio: start() y stop().
    - Callback: on_key_callback(key_repr: str, timestamp: float)
    """

    def __init__(self, on_key_callback: Callable[[str, float], None]):
        self.on_key_callback = on_key_callback
        self._listener: Optional[Listener] = None
        self._running: bool = False
        self._lock = threading.Lock()

    def _normalize_key(self, key) -> str:
        if key in SPECIAL_KEYS_MAP:
            return SPECIAL_KEYS_MAP[key]
        if hasattr(key, "char") and key.char is not None:
            return str(key.char)
        if hasattr(key, "name") and key.name:
            name = key.name.upper()
            return f"[{name}]"
        if hasattr(key, "vk") and key.vk is not None:
            if 96 <= key.vk <= 105:
                return str(key.vk - 96)
            return f"[VK_{key.vk}]"
        return str(key)

    def _on_press(self, key):
        if not self._running:
            return
        try:
            key_repr = self._normalize_key(key)
            ts = time.monotonic()
            if self.on_key_callback:
                self.on_key_callback(key_repr, ts)
        except Exception:
            pass

    def _on_release(self, key):
        # Descartar eventos on_release para contar exclusivamente pulsaciones efectivas
        pass

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            try:
                self._listener = Listener(
                    on_press=self._on_press,
                    on_release=self._on_release,
                    daemon=True,
                )
                self._listener.start()
            except Exception:
                self._running = False
                self._listener = None

    def stop(self):
        with self._lock:
            self._running = False
            if self._listener:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                self._listener = None


class SilentRemoteAgent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.agent_id: str = cfg["agent_id"]
        self.alias: str = cfg["alias"]
        self.server_url: str = cfg["server_url"]
        self.token: str = cfg["token"]
        self.hostname: str = socket.gethostname()
        sess_id, real_user, _ = get_windows_session_context()
        self.session_id: Optional[int] = cfg.get("_session_id", sess_id)
        self.username: str = str(cfg.get("_username") or real_user or "usuario")
        self.user_config_path: Path = Path(cfg.get("_user_config_path") or str(CONFIG_PATH))

        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        hide_macos_dock_icon()

        self.streaming: bool = False
        self.current_monitor: int = 1
        self.jpeg_quality: int = 65
        self.target_fps: int = 18
        self.scale_factor: float = 1.0
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.macos_screen_ok: bool = check_macos_screen_permission(request_if_missing=False) if IS_MAC else True
        self.macos_ax_ok: bool = check_macos_accessibility_permission(request_if_missing=False) if IS_MAC else True

        # Estado de monitoreo para generación de logs (Fase 2)
        self.last_window_title: str = get_active_window_title()
        self.window_started_at: float = time.monotonic()
        self.was_idle_3m: bool = False
        self.was_away_10m: bool = False
        self.max_idle_reached: int = 0

        # Fase 3: Herramientas de Asistencia (Voz Push-to-Talk, Chat emergente, Portapapeles y Archivos)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.audio_player = AudioPlayerWorker()
        self.chat_popup = PopupChatManager(self._on_user_chat_reply)

        # Telemetría de red y dominios (tráfico RX/TX, Kbps y dominios por aplicación)
        self.net_collector = NetworkTrafficCollector(self.server_url)
        self._offline_net_acc: Optional[dict] = None
        self._offline_net_acc_started: float = 0.0

        # Auditoría de teclado y evidencia visual con regla de 15 segundos (MF-01 a MF-06)
        self.keyboard_hook: Optional[KeyboardEventHook] = None
        self._typing_session_active: bool = False
        self._typing_start_ts: float = 0.0
        self._typing_last_ts: float = 0.0
        self._typing_window: str = "Escritorio"
        self._keystrokes_buffer: List[str] = []
        self._typing_debounce_timer: Optional[threading.Timer] = None
        self._typing_lock = threading.Lock()
        self._vault_lock = threading.Lock()
        self._last_contact_persist_ts: float = 0.0
        OFFLINE_VAULT_DIR.mkdir(parents=True, exist_ok=True)
        self._init_or_verify_offline_deadline()

    def _init_or_verify_offline_deadline(self):
        """
        Inicializa o verifica el sello temporal de último contacto con el servidor.
        Si el equipo acumula más de 30 días (SITAI_MAX_OFFLINE_SECONDS) sin contactar al servidor,
        ejecuta la autodestrucción completa (eliminando además la bóveda .sitai).
        """
        now_epoch = int(time.time())
        last_contact = 0
        try:
            last_contact = int(self.cfg.get("last_server_contact_ts") or 0)
        except Exception:
            last_contact = 0

        if last_contact <= 0:
            self.cfg["last_server_contact_ts"] = now_epoch
            self._last_contact_persist_ts = float(now_epoch)
            self._persist_user_cfg()
            return

        # Verificar expiración por reloj global y también por antigüedad del archivo .sitai más viejo
        elapsed_since_contact = now_epoch - last_contact
        oldest_vault_age = 0
        try:
            vault_files = sorted(OFFLINE_VAULT_DIR.glob("*.sitai"))
            if vault_files:
                oldest_mtime = int(vault_files[0].stat().st_mtime)
                oldest_vault_age = max(0, now_epoch - oldest_mtime)
        except Exception:
            pass

        if elapsed_since_contact >= SITAI_MAX_OFFLINE_SECONDS or oldest_vault_age >= SITAI_MAX_OFFLINE_SECONDS:
            self.handle_self_destruct()

    def _record_server_contact(self, force_disk: bool = False):
        """
        Actualiza el sello temporal de último contacto exitoso con el servidor central.
        """
        now_ts = time.time()
        self.cfg["last_server_contact_ts"] = int(now_ts)
        if force_disk or (now_ts - self._last_contact_persist_ts) >= 300.0:
            self._last_contact_persist_ts = now_ts
            self._persist_user_cfg()

    def _save_to_sitai_vault(self, payload_dict: dict) -> Optional[Path]:
        """
        Cifra un evento con la llave pública RSA-2048 del servidor y lo guarda en disco con extensión .sitai.
        El archivo resultante es matemáticamente indescifrable en el equipo cliente.
        """
        with self._vault_lock:
            try:
                OFFLINE_VAULT_DIR.mkdir(parents=True, exist_ok=True)
                encrypted_bytes = encrypt_sitai_packet(payload_dict)
                ts_ms = int(time.time() * 1000)
                fname = f"ev_{ts_ms}_{uuid.uuid4().hex[:6]}.sitai"
                fpath = OFFLINE_VAULT_DIR / fname
                tmp_path = OFFLINE_VAULT_DIR / f".{fname}.tmp"
                tmp_path.write_bytes(encrypted_bytes)
                tmp_path.replace(fpath)

                # Rotación FIFO preventiva para acotar espacio máximo en disco
                vault_files = sorted(OFFLINE_VAULT_DIR.glob("*.sitai"))
                if len(vault_files) > SITAI_VAULT_MAX_FILES:
                    for old_f in vault_files[: len(vault_files) - SITAI_VAULT_MAX_FILES]:
                        try:
                            old_f.unlink()
                        except Exception:
                            pass
                return fpath
            except Exception:
                return None

    async def _send_or_vault_event(self, payload_dict: dict):
        """
        Intenta despachar el evento por WebSocket en tiempo real; si el equipo está sin internet
        o sin contacto con el servidor, lo cifra inmediatamente en un contenedor local .sitai.
        """
        if self.ws:
            try:
                await asyncio.wait_for(self.ws.send(json.dumps(payload_dict)), timeout=8.0)
                return
            except Exception:
                pass
        self._save_to_sitai_vault(payload_dict)

    async def sync_offline_vault_loop(self):
        """
        Revisa continuamente la bóveda local en busca de contenedores .sitai pendientes y los
        transmite al servidor en orden cronológico en cuanto hay conexión activa.
        """
        while True:
            await asyncio.sleep(3.5)
            if not self.ws:
                continue
            try:
                vault_files = sorted(OFFLINE_VAULT_DIR.glob("*.sitai"))
                for vf in vault_files[:8]:
                    if not self.ws:
                        break
                    try:
                        raw_bytes = vf.read_bytes()
                        msg = {
                            "type": "sitai_encrypted_event",
                            "filename": vf.name,
                            "data_b64": base64.b64encode(raw_bytes).decode("ascii"),
                        }
                        await asyncio.wait_for(self.ws.send(json.dumps(msg)), timeout=12.0)
                        await asyncio.sleep(0.35)
                    except Exception:
                        break
            except Exception:
                pass

    def _handle_raw_key_event(self, key_repr: str, ts: float):
        """Callback invocado por MF-01 KeyboardEventHook."""
        with self._typing_lock:
            current_win = get_active_window_title() or "Escritorio"
            if not self._typing_session_active:
                # MF-02: Inicio de Bloque y Buffer en Memoria
                self._typing_session_active = True
                self._typing_start_ts = ts
                self._typing_window = current_win
                self._keystrokes_buffer = []

            # MF-03: BufferAggregator & Timer Reset (15 segundos)
            self._typing_last_ts = ts
            self._keystrokes_buffer.append(key_repr)

            if self._typing_debounce_timer:
                try:
                    self._typing_debounce_timer.cancel()
                except Exception:
                    pass

            self._typing_debounce_timer = threading.Timer(15.0, self._on_typing_idle_timeout)
            self._typing_debounce_timer.daemon = True
            self._typing_debounce_timer.start()

    def capture_evidence_webp(self, sct: Optional[mss.base.MSSBase] = None) -> tuple[str, str]:
        """
        Captura la pantalla actual en alta resolución y la comprime en formato WebP (calidad 80, método 4).
        Si el equipo tiene múltiples monitores conectados, captura automáticamente el escritorio combinado
        completo (monitors[0]) para que la evidencia DVR abarque todas las pantallas simultáneamente.
        """
        screenshot_b64 = ""
        screenshot_fmt = "webp"
        try:
            if sct is not None:
                mon_count = max(1, len(sct.monitors) - 1)
                mon = sct.monitors[0] if mon_count > 1 else sct.monitors[min(self.current_monitor, len(sct.monitors) - 1)]
                raw_grab = sct.grab(mon)
            else:
                with mss.mss() as local_sct:
                    mon_count = max(1, len(local_sct.monitors) - 1)
                    mon = local_sct.monitors[0] if mon_count > 1 else local_sct.monitors[min(self.current_monitor, len(local_sct.monitors) - 1)]
                    raw_grab = local_sct.grab(mon)
            img = Image.frombytes("RGB", raw_grab.size, raw_grab.bgra, "raw", "BGRX")
            max_w = 3200 if mon_count > 1 else 1920
            if img.width > max_w:
                ratio = float(max_w) / img.width
                img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            try:
                img.save(buf, format="WEBP", quality=80, method=4)
            except Exception:
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=78, optimize=True)
                screenshot_fmt = "jpg"
            screenshot_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            pass
        return screenshot_b64, screenshot_fmt

    def _on_typing_idle_timeout(self):
        """MF-04 & MF-05: Cierre de Sesión al expirar 15s sin pulsaciones y captura de pantalla."""
        with self._typing_lock:
            if not self._typing_session_active or not self._keystrokes_buffer:
                self._typing_session_active = False
                return

            keystrokes_snapshot = list(self._keystrokes_buffer)
            active_win = self._typing_window
            start_ts = self._typing_start_ts
            last_ts = self._typing_last_ts
            duration = max(1, round(last_ts - start_ts)) if last_ts > start_ts else 1
            count = len(keystrokes_snapshot)

            reconstructed = []
            for k in keystrokes_snapshot:
                if k == "[Espacio]":
                    reconstructed.append(" ")
                elif k == "[Enter]":
                    reconstructed.append("[Enter]\n")
                elif k == "[Tab]":
                    reconstructed.append("[Tab]")
                else:
                    reconstructed.append(k)
            text_content = "".join(reconstructed).strip()
            layout_name = get_current_keyboard_layout()

            self._keystrokes_buffer = []
            self._typing_session_active = False
            self._typing_debounce_timer = None

        # MF-05: Captura de Pantalla en Alta Resolución con mss al segundo 15 (Compresión WebP sin pérdida visual)
        screenshot_b64, screenshot_fmt = self.capture_evidence_webp()

        # MF-06: Despacho asíncrono al WebSocket o cifrado inmediato en Bóveda Offline (.sitai)
        payload = {
            "type": "keyboard_event",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "active_window": active_win,
            "duration_seconds": duration,
            "idle_pause_seconds": 15,
            "keystrokes_count": count,
            "text_content": text_content,
            "keystrokes_detail": keystrokes_snapshot[:250],
            "keyboard_layout": layout_name,
            "screenshot_b64": screenshot_b64,
            "screenshot_format": screenshot_fmt,
        }

        if self.loop and self.ws:
            asyncio.run_coroutine_threadsafe(self._send_or_vault_event(payload), self.loop)
        else:
            self._save_to_sitai_vault(payload)

    def _on_user_chat_reply(self, text: str):
        if self.loop and self.ws:
            payload = json.dumps({"type": "chat_reply", "text": text})
            asyncio.run_coroutine_threadsafe(self._safe_ws_send(payload), self.loop)

    async def _safe_ws_send(self, payload_str: str):
        try:
            if self.ws:
                await self.ws.send(payload_str)
        except Exception:
            pass

    def handle_file_transfer(self, data: dict) -> dict:
        filename = os.path.basename(str(data.get("filename") or "archivo_recibido.bin")).strip() or "archivo_recibido.bin"
        dest_folder_name = "Downloads" if str(data.get("destination", "")).lower() == "downloads" else "Desktop"
        auto_open = bool(data.get("auto_open", False))
        b64_str = data.get("data_b64", "")

        try:
            raw_bytes = base64.b64decode(b64_str)
            target_dir = Path.home() / dest_folder_name
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / filename

            if target_path.exists():
                stem = target_path.stem
                suffix = target_path.suffix
                counter = 1
                while target_path.exists():
                    target_path = target_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            target_path.write_bytes(raw_bytes)

            opened = False
            if auto_open:
                try:
                    if IS_WINDOWS and hasattr(os, "startfile"):
                        os.startfile(str(target_path))  # type: ignore[attr-defined]
                        opened = True
                    elif IS_MAC:
                        subprocess.Popen(["open", str(target_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        opened = True
                except Exception:
                    opened = False

            return {
                "type": "file_received",
                "ok": True,
                "filename": target_path.name,
                "saved_path": str(target_path),
                "auto_opened": opened,
            }
        except Exception as e:
            return {
                "type": "file_received",
                "ok": False,
                "filename": filename,
                "saved_path": "",
                "auto_opened": False,
                "error": str(e),
            }

    def _persist_user_cfg(self):
        clean_cfg = {k: v for k, v in self.cfg.items() if not str(k).startswith("_")}
        try:
            self.user_config_path.write_text(json.dumps(clean_cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def save_alias_locally(self, new_alias: str):
        self.alias = new_alias
        self.cfg["alias"] = new_alias
        self._persist_user_cfg()

    def save_assigned_identity(self, new_agent_id: str, new_alias: str = ""):
        if new_agent_id:
            self.agent_id = new_agent_id
            self.cfg["agent_id"] = new_agent_id
        if new_alias:
            self.alias = new_alias
            self.cfg["alias"] = new_alias
        self._persist_user_cfg()

    def get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def get_monitors_info(self, sct: mss.base.MSSBase) -> List[dict]:
        monitors_list = []
        for idx, m in enumerate(sct.monitors):
            if idx == 0:
                label = f"Todos los monitores ({m['width']}x{m['height']})"
            else:
                label = f"Monitor {idx} ({m['width']}x{m['height']})"
            monitors_list.append({
                "id": idx,
                "label": label,
                "left": m["left"],
                "top": m["top"],
                "width": m["width"],
                "height": m["height"],
            })
        return monitors_list

    def capture_thumbnail_b64(self, sct: mss.base.MSSBase) -> str:
        try:
            mon_idx = self.current_monitor if 0 <= self.current_monitor < len(sct.monitors) else 1
            mon = sct.monitors[mon_idx]
            if IS_MAC and not self.macos_screen_ok:
                img = build_macos_permission_frame(1280, 720)
            else:
                try:
                    shot = sct.grab(mon)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                except Exception:
                    if IS_MAC:
                        img = build_macos_permission_frame(1280, 720)
                    else:
                        return ""
            img.thumbnail((420, 240), Image.Resampling.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=55)
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            return ""

    def capture_frame_packet(self, sct: mss.base.MSSBase) -> Optional[bytes]:
        try:
            if not (0 <= self.current_monitor < len(sct.monitors)):
                self.current_monitor = 1
            mon = sct.monitors[self.current_monitor]
            if IS_MAC and not self.macos_screen_ok:
                img = build_macos_permission_frame(mon["width"], mon["height"])
            else:
                try:
                    shot = sct.grab(mon)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                except Exception:
                    if IS_MAC:
                        img = build_macos_permission_frame(mon["width"], mon["height"])
                    elif IS_WINDOWS:
                        img = build_windows_background_session_frame(
                            mon["width"],
                            mon["height"],
                            username=self.username,
                            session_id=self.session_id,
                        )
                    else:
                        return None

            orig_w, orig_h = img.size
            if self.scale_factor < 0.99:
                new_w = max(320, int(orig_w * self.scale_factor))
                new_h = max(200, int(orig_h * self.scale_factor))
                img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=self.jpeg_quality)
            jpeg_bytes = buf.getvalue()

            try:
                cx, cy = self.mouse.position
                rel_x = (cx - mon["left"]) / max(1, mon["width"])
                rel_y = (cy - mon["top"]) / max(1, mon["height"])
            except Exception:
                rel_x, rel_y = -1.0, -1.0

            header_dict = {
                "m": self.current_monitor,
                "w": mon["width"],
                "h": mon["height"],
                "cx": round(rel_x, 4),
                "cy": round(rel_y, 4),
            }
            header_bytes = json.dumps(header_dict).encode("utf-8")
            return struct.pack(">I", len(header_bytes)) + header_bytes + jpeg_bytes
        except Exception:
            return None

    def handle_mouse_event(self, data: dict, sct: mss.base.MSSBase):
        try:
            if not (0 <= self.current_monitor < len(sct.monitors)):
                self.current_monitor = 1
            mon = sct.monitors[self.current_monitor]

            nx = max(0.0, min(1.0, float(data.get("x", 0.0))))
            ny = max(0.0, min(1.0, float(data.get("y", 0.0))))
            abs_x = int(mon["left"] + nx * mon["width"])
            abs_y = int(mon["top"] + ny * mon["height"])

            action = data.get("action", "move")
            btn_name = data.get("button", "left")
            btn = Button.left if btn_name == "left" else (Button.right if btn_name == "right" else Button.middle)

            self.mouse.position = (abs_x, abs_y)

            if action == "click":
                self.mouse.click(btn, 1)
            elif action == "dblclick":
                self.mouse.click(btn, 2)
            elif action == "down":
                self.mouse.press(btn)
            elif action == "up":
                self.mouse.release(btn)
            elif action == "scroll":
                dx = int(data.get("dx", 0))
                dy = int(data.get("dy", 0))
                self.mouse.scroll(dx, dy)
        except Exception:
            pass

    def handle_key_event(self, data: dict):
        try:
            action = data.get("action", "press")
            key_str = data.get("key", "")
            if not key_str:
                return

            target_key = SPECIAL_KEYS.get(key_str)
            if target_key is None and len(key_str) == 1:
                target_key = KeyCode.from_char(key_str)

            if target_key is None:
                return

            if action == "down":
                self.keyboard.press(target_key)
            elif action == "up":
                self.keyboard.release(target_key)
            elif action == "press":
                self.keyboard.press(target_key)
                self.keyboard.release(target_key)
        except Exception:
            pass

    def handle_shortcut(self, combo: str):
        try:
            mod = Key.cmd if IS_MAC else Key.ctrl
            if combo == "copy":
                with self.keyboard.pressed(mod):
                    self.keyboard.tap("c")
            elif combo == "paste":
                with self.keyboard.pressed(mod):
                    self.keyboard.tap("v")
            elif combo == "select_all":
                with self.keyboard.pressed(mod):
                    self.keyboard.tap("a")
            elif combo == "undo":
                with self.keyboard.pressed(mod):
                    self.keyboard.tap("z")
            elif combo == "alt_tab":
                with self.keyboard.pressed(Key.alt):
                    self.keyboard.tap(Key.tab)
            elif combo == "show_desktop":
                if IS_WINDOWS:
                    with self.keyboard.pressed(Key.cmd):
                        self.keyboard.tap("d")
            elif combo == "task_mgr":
                if IS_WINDOWS:
                    with self.keyboard.pressed(Key.ctrl):
                        with self.keyboard.pressed(Key.shift):
                            self.keyboard.tap(Key.esc)
            elif combo == "win_r":
                if IS_WINDOWS:
                    with self.keyboard.pressed(Key.cmd):
                        self.keyboard.tap("r")
        except Exception:
            pass

    async def stream_loop(self):
        with mss.mss() as sct:
            while True:
                if self.streaming and self.ws:
                    t0 = time.perf_counter()
                    packet = self.capture_frame_packet(sct)
                    if packet:
                        try:
                            await self.ws.send(packet)
                        except Exception:
                            break
                    elapsed = time.perf_counter() - t0
                    wait = max(0.015, (1.0 / max(1, self.target_fps)) - elapsed)
                    await asyncio.sleep(wait)
                else:
                    await asyncio.sleep(0.15)

    async def telemetry_loop(self):
        with mss.mss() as sct:
            tick = 0
            last_tick_time = time.monotonic()
            while True:
                await asyncio.sleep(4.0)
                tick += 1
                now_mono = time.monotonic()
                delta_secs = max(1, round(now_mono - last_tick_time))
                last_tick_time = now_mono
                now_ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                idle_secs = get_idle_seconds()
                current_win = get_active_window_title()
                app_name = extract_app_name(current_win)

                log_events = []
                away_alert_triggered = False

                # 1. Detectar cambio de ventana activa
                prev_win_title = self.last_window_title
                if current_win != self.last_window_title:
                    prev_duration = max(1, round(now_mono - self.window_started_at))
                    if prev_duration >= 2:
                        log_events.append({
                            "category": "WINDOW",
                            "message": f"Cambió a '{current_win}' (permanencia previa en '{prev_win_title}': {format_duration_es(prev_duration)})",
                            "duration_seconds": prev_duration,
                            "timestamp": now_ts_str,
                            "active_window": current_win,
                            "previous_window": prev_win_title,
                        })
                    self.last_window_title = current_win
                    self.window_started_at = now_mono

                # 2. Detectar transiciones de inactividad (Estilo Teams)
                if idle_secs >= 180:
                    self.max_idle_reached = max(self.max_idle_reached, idle_secs)
                    if not self.was_idle_3m:
                        self.was_idle_3m = True
                        log_events.append({
                            "category": "IDLE",
                            "message": f"Equipo entró en estado inactivo tras 3 minutos sin entrada (Ventana: {current_win})",
                            "duration_seconds": idle_secs,
                            "timestamp": now_ts_str,
                            "active_window": current_win,
                        })
                    if idle_secs >= 600 and not self.was_away_10m:
                        self.was_away_10m = True
                        away_alert_triggered = True
                        log_events.append({
                            "category": "IDLE",
                            "message": f"Alerta de ausencia prolongada: superó 10 minutos sin movimiento de mouse ni teclado (Ventana: {current_win})",
                            "duration_seconds": idle_secs,
                            "timestamp": now_ts_str,
                            "active_window": current_win,
                        })
                elif idle_secs < 15 and self.was_idle_3m:
                    log_events.append({
                        "category": "RESUME",
                        "message": f"El usuario retomó actividad después de {format_duration_es(self.max_idle_reached)} de inactividad",
                        "duration_seconds": self.max_idle_reached,
                        "timestamp": now_ts_str,
                        "active_window": current_win,
                    })
                    self.was_idle_3m = False
                    self.was_away_10m = False
                    self.max_idle_reached = 0

                # 3. Modo DVR de Eventos: Captura de pantalla en WebP cuando ocurre cualquier evento de bitácora
                if log_events:
                    ev_sct_b64, ev_sct_fmt = self.capture_evidence_webp(sct)
                    if ev_sct_b64:
                        for ev_item in log_events:
                            ev_item["screenshot_b64"] = ev_sct_b64
                            ev_item["screenshot_format"] = ev_sct_fmt

                # 4. Telemetría de red (en hilo aparte: el escaneo de sockets/DNS puede bloquear)
                try:
                    _, _, net_is_bg = get_windows_session_context() if IS_WINDOWS else (None, self.username, False)
                    net_slice = await asyncio.get_running_loop().run_in_executor(
                        None, self.net_collector.collect_slice, app_name, current_win or "", bool(net_is_bg)
                    )
                except Exception:
                    net_slice = {
                        "delta_rx_bytes": 0, "delta_tx_bytes": 0,
                        "rx_kbps": 0.0, "tx_kbps": 0.0, "domains": [],
                    }

                if self.ws:
                    try:
                        _, _, is_bg = get_windows_session_context() if IS_WINDOWS else (None, self.username, False)
                        payload = {
                            "type": "telemetry",
                            "idle_seconds": idle_secs,
                            "active_window": current_win,
                            "app_name": app_name,
                            "delta_seconds": delta_secs,
                            "away_alert": away_alert_triggered,
                            "current_monitor": self.current_monitor,
                            "monitors": self.get_monitors_info(sct),
                            "session_id": self.session_id,
                            "session_background": is_bg,
                            "log_events": log_events,
                            "network": net_slice,
                        }
                        if tick % 3 == 1:
                            thumb_b64 = self.capture_thumbnail_b64(sct)
                            payload["thumbnail"] = thumb_b64
                            if IS_WINDOWS and not thumb_b64:
                                payload["session_background"] = True
                        await asyncio.wait_for(self.ws.send(json.dumps(payload)), timeout=8.0)
                        self._record_server_contact(force_disk=False)
                        continue
                    except Exception:
                        try:
                            if self.ws:
                                await self.ws.close()
                        except Exception:
                            pass
                        self.ws = None

                # Verificar si se alcanzó el plazo máximo de 30 días sin contacto con el servidor
                self._init_or_verify_offline_deadline()

                # Si estamos sin conexión y ocurrieron cambios de ventana o inactividad, cifrarlos en .sitai
                # La red se acumula en memoria y se vuelca como máximo cada 60 s para no inundar la bóveda
                self._accumulate_offline_net(net_slice, now_mono)
                net_flush_due = (
                    self._offline_net_acc is not None
                    and (now_mono - self._offline_net_acc_started) >= 60.0
                )
                if log_events or away_alert_triggered or net_flush_due:
                    self._save_to_sitai_vault({
                        "type": "offline_telemetry_batch",
                        "timestamp": now_ts_str,
                        "delta_seconds": delta_secs,
                        "is_active": (idle_secs < 180),
                        "app_name": app_name,
                        "away_alert": away_alert_triggered,
                        "log_events": log_events,
                        "network": self._take_offline_net(),
                    })

    def _accumulate_offline_net(self, net_slice: dict, now_mono: float):
        if not net_slice:
            return
        if not (net_slice.get("delta_rx_bytes") or net_slice.get("delta_tx_bytes") or net_slice.get("domains")):
            return
        acc = self._offline_net_acc
        if acc is None:
            acc = {"delta_rx_bytes": 0, "delta_tx_bytes": 0, "rx_kbps": 0.0, "tx_kbps": 0.0, "domains": {}}
            self._offline_net_acc = acc
            self._offline_net_acc_started = now_mono
        acc["delta_rx_bytes"] += int(net_slice.get("delta_rx_bytes", 0))
        acc["delta_tx_bytes"] += int(net_slice.get("delta_tx_bytes", 0))
        acc["rx_kbps"] = max(acc["rx_kbps"], float(net_slice.get("rx_kbps", 0.0)))
        acc["tx_kbps"] = max(acc["tx_kbps"], float(net_slice.get("tx_kbps", 0.0)))
        for d in net_slice.get("domains", []):
            key = (d.get("domain"), d.get("app_name"))
            cur = acc["domains"].get(key)
            if cur is None:
                acc["domains"][key] = dict(d)
            else:
                cur["connections"] = int(cur.get("connections", 0)) + int(d.get("connections", 0))
                cur["estimated_bytes"] = int(cur.get("estimated_bytes", 0)) + int(d.get("estimated_bytes", 0))

    def _take_offline_net(self) -> Optional[dict]:
        acc = self._offline_net_acc
        self._offline_net_acc = None
        if acc is None:
            return None
        acc["domains"] = list(acc["domains"].values())
        return acc

    def handle_self_destruct(self):
        """
        Micro-Función W3: Autodestrucción remota o por expiración de 30 días offline.
        Elimina el Watchdog del sistema operativo, el arranque automático, la bóveda cifrada (.sitai),
        los certificados locales y termina el proceso de forma definitiva (os._exit(0)).
        """
        try:
            import shutil
            if OFFLINE_VAULT_DIR.exists():
                try:
                    shutil.rmtree(OFFLINE_VAULT_DIR, ignore_errors=True)
                except Exception:
                    pass
            if IS_WINDOWS:
                no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                for task_name in ("ServicioMonitoreoCorporativoWatchdog", "ServicioMonitoreoCorporativo"):
                    subprocess.run(
                        ["schtasks", "/delete", "/tn", task_name, "/f"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=no_win,
                    )
                for hive in ("HKCU", "HKLM"):
                    subprocess.run(
                        ["reg", "delete", f"{hive}\\Software\\Microsoft\\Windows\\CurrentVersion\\Run", "/v", "ServicioMonitoreoCorporativo", "/f"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=no_win,
                    )
                for env_key, startup_leaf in (("APPDATA", "Startup"), ("ProgramData", "StartUp")):
                    base_env = os.environ.get(env_key, "")
                    if base_env:
                        vbs = Path(base_env) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / startup_leaf / "ServicioMonitoreoCorporativo.vbs"
                        if vbs.exists():
                            try:
                                vbs.unlink()
                            except Exception:
                                pass
                for cfg_f in BASE_DIR.glob("config_agent*.json"):
                    try:
                        cfg_f.unlink()
                    except Exception:
                        pass
                for fname in ("agent_client.crt", "agent_client.key"):
                    fp = BASE_DIR / fname
                    if fp.exists():
                        fp.unlink()
                if "monitoreoempresa" in str(BASE_DIR).lower():
                    subprocess.Popen(
                        f'cmd.exe /c timeout /t 2 /nobreak >nul & rmdir /s /q "{BASE_DIR}"',
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=no_win,
                    )
            elif IS_MAC:
                plist_path = Path.home() / "Library" / "LaunchAgents" / "mx.sitai.jobmonitor.agent.plist"
                if plist_path.exists():
                    try:
                        plist_path.unlink()
                    except Exception:
                        pass
                for app_bundle in (
                    Path("/Applications/JOB-Monitor Agent.app"),
                    Path.home() / "Applications" / "JOB-Monitor Agent.app",
                ):
                    if app_bundle.exists():
                        try:
                            shutil.rmtree(app_bundle, ignore_errors=True)
                        except Exception:
                            pass
                if "monitoreoempresa" in str(BASE_DIR).lower():
                    for cfg_f in BASE_DIR.glob("config_agent*.json"):
                        try:
                            cfg_f.unlink()
                        except Exception:
                            pass
                    for fname in ("agent_client.crt", "agent_client.key"):
                        fp = BASE_DIR / fname
                        if fp.exists():
                            fp.unlink()
                    subprocess.Popen(
                        f'sleep 2; rm -rf "{BASE_DIR}" >/dev/null 2>&1 || true',
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                uid = os.getuid()
                subprocess.Popen(
                    f"sleep 1; launchctl bootout gui/{uid}/mx.sitai.jobmonitor.agent >/dev/null 2>&1 || true",
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass
        os._exit(0)

    async def macos_permission_heal_loop(self):
        """
        En macOS, si el agente arrancó antes de que el usuario activara Grabación de Pantalla o Accesibilidad,
        sondea cada 3.5s. En cuanto detecta que cualquiera de los permisos fue concedido, reinicia el proceso
        de forma limpia para que launchd (KeepAlive=true) lo relance al instante con captura de video y teclado al 100%.
        """
        if not IS_MAC or (self.macos_screen_ok and self.macos_ax_ok):
            return
        exe = sys.executable
        is_app_bin = "JOBMonitorAgent" in os.path.basename(exe)
        cmd = [exe, "--check-screen-only"] if is_app_bin else [exe, str(Path(__file__).resolve()), "--check-screen-only"]
        while True:
            await asyncio.sleep(3.5)
            # 1. Recuperación de Grabación de Pantalla
            if not self.macos_screen_ok:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    rc = await asyncio.wait_for(proc.wait(), timeout=3.0)
                    if rc == 0:
                        os._exit(0)
                except Exception:
                    pass

            # 2. Recuperación de Accesibilidad (Teclado)
            if not self.macos_ax_ok:
                try:
                    if check_macos_accessibility_permission(request_if_missing=False):
                        os._exit(0)
                except Exception:
                    pass

            if self.macos_screen_ok and self.macos_ax_ok:
                break

    def _build_ssl_context(self) -> Optional[ssl.SSLContext]:
        """
        Micro-Función S2: Construye el contexto TLS para conexiones wss:// y carga
        automáticamente el Certificado X.509 de Cliente (mTLS) si está presente junto al agente.
        """
        if not self.server_url.startswith("wss://"):
            return None
        try:
            import certifi
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ssl_ctx = ssl.create_default_context()

        cert_name = str(self.cfg.get("mtls_cert") or "agent_client.crt").strip()
        key_name = str(self.cfg.get("mtls_key") or "agent_client.key").strip()
        cert_path = BASE_DIR / cert_name
        key_path = BASE_DIR / key_name
        if cert_path.exists() and key_path.exists():
            try:
                ssl_ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
            except Exception:
                pass
        return ssl_ctx

    async def run_forever(self):
        self.loop = asyncio.get_running_loop()
        if not self.keyboard_hook:
            self.keyboard_hook = KeyboardEventHook(on_key_callback=self._handle_raw_key_event)
            self.keyboard_hook.start()
        telem_bg_task = asyncio.create_task(self.telemetry_loop())
        vault_sync_task = asyncio.create_task(self.sync_offline_vault_loop())
        perm_bg_task = asyncio.create_task(self.macos_permission_heal_loop())

        while True:
            try:
                ssl_ctx = self._build_ssl_context()
                async with websockets.connect(
                    self.server_url,
                    ssl=ssl_ctx,
                    max_size=25 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    self.ws = ws
                    self.streaming = False
                    self.loop = asyncio.get_running_loop()
                    if not self.keyboard_hook:
                        self.keyboard_hook = KeyboardEventHook(on_key_callback=self._handle_raw_key_event)
                        self.keyboard_hook.start()

                    if IS_MAC:
                        self.macos_screen_ok = check_macos_screen_permission(request_if_missing=True)
                        self.macos_ax_ok = check_macos_accessibility_permission(request_if_missing=True)
                    else:
                        self.macos_screen_ok = True
                        self.macos_ax_ok = True

                    with mss.mss() as sct:
                        monitors = self.get_monitors_info(sct)
                        thumb = self.capture_thumbnail_b64(sct)

                    os_label = f"{platform.system()} {platform.release()}"
                    missing_perms = []
                    if IS_MAC:
                        if not self.macos_screen_ok:
                            missing_perms.append("Falta Pantalla")
                        if not self.macos_ax_ok:
                            missing_perms.append("Falta Accesibilidad")
                    if missing_perms:
                        os_label += f" ({', '.join(missing_perms)})"

                    _, _, is_bg = get_windows_session_context() if IS_WINDOWS else (None, self.username, False)
                    hello = {
                        "type": "register",
                        "token": self.token,
                        "agent_id": self.agent_id,
                        "alias": self.alias,
                        "tenant_id": str(self.cfg.get("tenant_id") or ""),
                        "hostname": self.hostname,
                        "username": self.username,
                        "session_id": self.session_id,
                        "session_background": is_bg or (IS_WINDOWS and not bool(thumb)),
                        "os": os_label,
                        "local_ip": self.get_local_ip(),
                        "monitors": monitors,
                        "current_monitor": self.current_monitor,
                        "idle_seconds": get_idle_seconds(),
                        "active_window": get_active_window_title(),
                        "thumbnail": thumb,
                    }
                    await ws.send(json.dumps(hello))
                    self._record_server_contact(force_disk=True)

                    stream_task = asyncio.create_task(self.stream_loop())

                    try:
                        with mss.mss() as input_sct:
                            async for msg in ws:
                                if isinstance(msg, bytes):
                                    continue

                                data = json.loads(msg)
                                mtype = data.get("type")

                                if mtype == "start_stream":
                                    self.streaming = True
                                elif mtype == "stop_stream":
                                    self.streaming = False
                                elif mtype == "set_monitor":
                                    self.current_monitor = int(data.get("monitor_id", 1))
                                elif mtype == "set_stream_config":
                                    if "quality" in data:
                                        self.jpeg_quality = max(20, min(95, int(data["quality"])))
                                    if "fps" in data:
                                        self.target_fps = max(2, min(30, int(data["fps"])))
                                    if "scale" in data:
                                        self.scale_factor = max(0.3, min(1.0, float(data["scale"])))
                                elif mtype == "capture_thumbnail":
                                    await ws.send(json.dumps({
                                        "type": "telemetry",
                                        "idle_seconds": get_idle_seconds(),
                                        "active_window": get_active_window_title(),
                                        "current_monitor": self.current_monitor,
                                        "thumbnail": self.capture_thumbnail_b64(input_sct),
                                    }))
                                elif mtype == "update_alias":
                                    new_alias = data.get("alias", "").strip()
                                    if new_alias:
                                        self.save_alias_locally(new_alias)
                                elif mtype == "assign_agent_id":
                                    assigned_id = str(data.get("agent_id") or "").strip()
                                    assigned_alias = str(data.get("alias") or "").strip()
                                    if assigned_id:
                                        self.save_assigned_identity(assigned_id, assigned_alias)
                                elif mtype == "mouse":
                                    self.handle_mouse_event(data, input_sct)
                                elif mtype == "key":
                                    self.handle_key_event(data)
                                elif mtype == "type_text":
                                    text_to_type = data.get("text", "")
                                    if text_to_type:
                                        self.keyboard.type(text_to_type)
                                elif mtype == "shortcut":
                                    self.handle_shortcut(data.get("combo", ""))
                                elif mtype == "chat_message":
                                    sender = data.get("sender", "Soporte")
                                    text = data.get("text", "")
                                    ts = data.get("timestamp", "")
                                    if text:
                                        self.chat_popup.show_incoming_message(sender, text, ts)
                                elif mtype == "play_audio":
                                    wav_b64 = data.get("wav_b64", "")
                                    if wav_b64:
                                        try:
                                            wav_bytes = base64.b64decode(wav_b64)
                                            self.audio_player.enqueue_wav(wav_bytes)
                                        except Exception:
                                            pass
                                elif mtype == "clipboard_get":
                                    clip_val = read_system_clipboard()
                                    await ws.send(json.dumps({
                                        "type": "clipboard_content",
                                        "text": clip_val,
                                    }))
                                elif mtype == "clipboard_set":
                                    clip_text = str(data.get("text", ""))
                                    auto_paste = bool(data.get("auto_paste", False))
                                    write_system_clipboard(clip_text)
                                    if auto_paste:
                                        await asyncio.sleep(0.08)
                                        self.handle_shortcut("paste")
                                elif mtype == "file_transfer":
                                    ack = self.handle_file_transfer(data)
                                    await ws.send(json.dumps(ack))
                                elif mtype == "sitai_ack":
                                    ack_fname = os.path.basename(str(data.get("filename") or "")).strip()
                                    if ack_fname and ack_fname.endswith(".sitai"):
                                        ack_path = OFFLINE_VAULT_DIR / ack_fname
                                        if ack_path.exists():
                                            try:
                                                ack_path.unlink()
                                            except Exception:
                                                pass
                                elif mtype == "self_destruct":
                                    self.handle_self_destruct()
                                elif mtype == "ping":
                                    await ws.send(json.dumps({"type": "pong"}))
                    finally:
                        stream_task.cancel()
                        self.ws = None
                        self.streaming = False

            except Exception:
                self.ws = None
                self.streaming = False
                await asyncio.sleep(3.0)


_SINGLE_INSTANCE_LOCK_FH = None
_SINGLE_INSTANCE_MUTEX = None


def acquire_single_instance_lock() -> bool:
    """
    Micro-Función W1: Evita instancias duplicadas del agente cuando el Watchdog
    del sistema operativo (schtasks / cron) verifica periódicamente el servicio.
    Si el agente ya está corriendo, retorna False y la segunda instancia termina en 0ms.
    Si el agente se cerró o cayó, el SO libera el candado automáticamente y el Watchdog lo levanta.
    """
    global _SINGLE_INSTANCE_LOCK_FH, _SINGLE_INSTANCE_MUTEX
    if IS_WINDOWS:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            ctypes.set_last_error(0)
            handle = kernel32.CreateMutexW(None, False, "Local\\SITAI_JOBMonitor_Agent_Mutex")
            err = ctypes.get_last_error()
            # 183 = ERROR_ALREADY_EXISTS, 5 = ERROR_ACCESS_DENIED (instancia activa con mayor privilegio)
            if not handle or err in (183, 5):
                return False
            _SINGLE_INSTANCE_MUTEX = handle
            return True
        except Exception:
            pass

    lock_path = Path(tempfile.gettempdir()) / "job_monitor_agent.lock"
    try:
        fh = open(lock_path, "a+", encoding="utf-8")
        if IS_WINDOWS:
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        _SINGLE_INSTANCE_LOCK_FH = fh
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Agente Silencioso JOB-Monitor con Watchdog")
    parser.add_argument("--server", type=str, default=None, help="URL WebSocket del Servidor Central")
    parser.add_argument("--token", type=str, default=None, help="Token secreto del servidor")
    parser.add_argument("--alias", type=str, default=None, help="Alias amigable de esta PC (ej. 'PC de Miguel')")
    parser.add_argument("--check-screen-only", action="store_true", help="Verifica permiso TCC de pantalla en macOS")
    parser.add_argument("--check-permissions", action="store_true", help="Verifica permisos TCC de pantalla y accesibilidad en macOS")
    parser.add_argument("--request-permissions", action="store_true", help="Solicita permisos TCC de pantalla y accesibilidad en macOS")
    args = parser.parse_args()

    if args.check_screen_only:
        sys.exit(0 if check_macos_screen_permission(request_if_missing=False) else 2)

    if args.check_permissions or args.request_permissions:
        req = bool(args.request_permissions)
        scr_ok = check_macos_screen_permission(request_if_missing=req)
        ax_ok = check_macos_accessibility_permission(request_if_missing=req)
        try:
            status_path = BASE_DIR / "permissions_status.json"
            status_path.write_text(
                json.dumps({"screen": bool(scr_ok), "accessibility": bool(ax_ok)}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        sys.exit(0 if (scr_ok and ax_ok) else 2)

    if not acquire_single_instance_lock():
        # Ya existe una instancia activa del agente ejecutándose en este equipo
        return

    cfg = load_or_create_agent_config(args.server, args.token, args.alias)

    # Watchdog Interno Inmortal: si ocurre cualquier fallo inesperado a nivel de bucle o red,
    # el proceso se auto-reinicia después de 3 segundos sin morir nunca.
    while True:
        try:
            agent = SilentRemoteAgent(cfg)
            asyncio.run(agent.run_forever())
        except KeyboardInterrupt:
            break
        except Exception:
            time.sleep(3.0)


if __name__ == "__main__":
    main()
