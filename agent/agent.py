import argparse
import asyncio
import base64
import ctypes
import ctypes.util
import getpass
import hashlib
import io
import json
import os
import platform
import queue
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
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

warnings.filterwarnings("ignore", category=DeprecationWarning, module="mss")

import mss
import websockets
from PIL import Image, ImageDraw
from pynput.keyboard import Controller as KeyboardController, Key, KeyCode
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

else:
    def get_idle_seconds() -> int:
        return 0

    def get_active_window_title() -> str:
        return platform.system()

    def check_macos_screen_permission(request_if_missing: bool = False) -> bool:
        return True


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


def load_or_create_agent_config(cli_server: Optional[str], cli_token: Optional[str], cli_alias: Optional[str]) -> dict:
    hostname = socket.gethostname()
    try:
        username = getpass.getuser()
    except Exception:
        username = "usuario"

    cfg = {
        "agent_id": generate_hardware_agent_id(hostname, username),
        "alias": f"PC de {username} ({hostname})",
        "server_url": DEFAULT_SERVER_URL,
        "token": DEFAULT_TOKEN,
    }

    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg.update(loaded)
        except Exception:
            pass

    if cli_server:
        cfg["server_url"] = cli_server
    if cli_token:
        cfg["token"] = cli_token
    if cli_alias:
        cfg["alias"] = cli_alias

    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

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


class SilentRemoteAgent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.agent_id: str = cfg["agent_id"]
        self.alias: str = cfg["alias"]
        self.server_url: str = cfg["server_url"]
        self.token: str = cfg["token"]
        self.hostname: str = socket.gethostname()
        try:
            self.username: str = getpass.getuser()
        except Exception:
            self.username = "usuario"

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

    def save_alias_locally(self, new_alias: str):
        self.alias = new_alias
        self.cfg["alias"] = new_alias
        try:
            CONFIG_PATH.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

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
                if not self.ws:
                    break
                tick += 1
                now_mono = time.monotonic()
                delta_secs = max(1, round(now_mono - last_tick_time))
                last_tick_time = now_mono

                idle_secs = get_idle_seconds()
                current_win = get_active_window_title()
                app_name = extract_app_name(current_win)

                log_events = []
                away_alert_triggered = False

                # 1. Detectar cambio de ventana activa
                if current_win != self.last_window_title:
                    prev_duration = max(1, round(now_mono - self.window_started_at))
                    if prev_duration >= 2:
                        log_events.append({
                            "category": "WINDOW",
                            "message": f"Cambió a '{current_win}' (permanencia previa en '{self.last_window_title}': {format_duration_es(prev_duration)})",
                            "duration_seconds": prev_duration,
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
                        })
                    if idle_secs >= 600 and not self.was_away_10m:
                        self.was_away_10m = True
                        away_alert_triggered = True
                        log_events.append({
                            "category": "IDLE",
                            "message": f"Alerta de ausencia prolongada: superó 10 minutos sin movimiento de mouse ni teclado (Ventana: {current_win})",
                            "duration_seconds": idle_secs,
                        })
                elif idle_secs < 15 and self.was_idle_3m:
                    log_events.append({
                        "category": "RESUME",
                        "message": f"El usuario retomó actividad después de {format_duration_es(self.max_idle_reached)} de inactividad",
                        "duration_seconds": self.max_idle_reached,
                    })
                    self.was_idle_3m = False
                    self.was_away_10m = False
                    self.max_idle_reached = 0

                try:
                    payload = {
                        "type": "telemetry",
                        "idle_seconds": idle_secs,
                        "active_window": current_win,
                        "app_name": app_name,
                        "delta_seconds": delta_secs,
                        "away_alert": away_alert_triggered,
                        "current_monitor": self.current_monitor,
                        "monitors": self.get_monitors_info(sct),
                        "log_events": log_events,
                    }
                    if tick % 3 == 1:
                        payload["thumbnail"] = self.capture_thumbnail_b64(sct)
                    await asyncio.wait_for(self.ws.send(json.dumps(payload)), timeout=8.0)
                except Exception:
                    try:
                        if self.ws:
                            await self.ws.close()
                    except Exception:
                        pass
                    break

    def handle_self_destruct(self):
        """
        Micro-Función W3: Autodestrucción remota del agente ordenada desde el panel.
        Elimina el Watchdog del sistema operativo, el arranque automático, los certificados
        locales y termina el proceso de forma definitiva (os._exit(0)).
        """
        try:
            if IS_WINDOWS:
                for task_name in ("ServicioMonitoreoCorporativoWatchdog", "ServicioMonitoreoCorporativo"):
                    subprocess.run(
                        ["schtasks", "/delete", "/tn", task_name, "/f"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                appdata = os.environ.get("APPDATA", "")
                if appdata:
                    vbs = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "ServicioMonitoreoCorporativo.vbs"
                    if vbs.exists():
                        vbs.unlink()
                for fname in ("config_agent.json", "agent_client.crt", "agent_client.key"):
                    fp = BASE_DIR / fname
                    if fp.exists():
                        fp.unlink()
                if "monitoreoempresa" in str(BASE_DIR).lower():
                    subprocess.Popen(
                        f'cmd.exe /c timeout /t 2 /nobreak >nul & rmdir /s /q "{BASE_DIR}"',
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            elif IS_MAC:
                import shutil
                plist_path = Path.home() / "Library" / "LaunchAgents" / "mx.sitai.jobmonitor.agent.plist"
                if plist_path.exists():
                    try:
                        plist_path.unlink()
                    except Exception:
                        pass
                app_bundle = Path.home() / "Applications" / "JOB-Monitor Agent.app"
                if app_bundle.exists():
                    try:
                        shutil.rmtree(app_bundle, ignore_errors=True)
                    except Exception:
                        pass
                if "monitoreoempresa" in str(BASE_DIR).lower():
                    for fname in ("config_agent.json", "agent_client.crt", "agent_client.key"):
                        fp = BASE_DIR / fname
                        if fp.exists():
                            fp.unlink()
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
        En macOS, si el agente arrancó antes de que el usuario activara Grabación de Pantalla,
        sondea cada 3s en un subproceso limpio. En cuanto detecta el permiso concedido,
        termina para que LaunchAgent (KeepAlive=true) lo relance en 1s con video real activo.
        """
        if not IS_MAC or self.macos_screen_ok:
            return
        exe = sys.executable
        is_app_bin = "JOBMonitorAgent" in os.path.basename(exe)
        cmd = [exe, "--check-screen-only"] if is_app_bin else [exe, str(Path(__file__).resolve()), "--check-screen-only"]
        while not self.macos_screen_ok:
            await asyncio.sleep(3.5)
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
                    self.macos_screen_ok = check_macos_screen_permission(request_if_missing=True) if IS_MAC else True

                    with mss.mss() as sct:
                        monitors = self.get_monitors_info(sct)
                        thumb = self.capture_thumbnail_b64(sct)

                    os_label = f"{platform.system()} {platform.release()}"
                    if IS_MAC and not self.macos_screen_ok:
                        os_label += " (Falta permiso de Pantalla)"

                    hello = {
                        "type": "register",
                        "token": self.token,
                        "agent_id": self.agent_id,
                        "alias": self.alias,
                        "tenant_id": str(self.cfg.get("tenant_id") or ""),
                        "hostname": self.hostname,
                        "username": self.username,
                        "os": os_label,
                        "local_ip": self.get_local_ip(),
                        "monitors": monitors,
                        "current_monitor": self.current_monitor,
                        "idle_seconds": get_idle_seconds(),
                        "active_window": get_active_window_title(),
                        "thumbnail": thumb,
                    }
                    await ws.send(json.dumps(hello))

                    stream_task = asyncio.create_task(self.stream_loop())
                    telem_task = asyncio.create_task(self.telemetry_loop())
                    perm_task = asyncio.create_task(self.macos_permission_heal_loop())

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
                                elif mtype == "self_destruct":
                                    self.handle_self_destruct()
                                elif mtype == "ping":
                                    await ws.send(json.dumps({"type": "pong"}))
                    finally:
                        stream_task.cancel()
                        telem_task.cancel()
                        perm_task.cancel()
                        self.ws = None
                        self.streaming = False

            except Exception:
                self.ws = None
                self.streaming = False
                await asyncio.sleep(3.0)


_SINGLE_INSTANCE_LOCK_FH = None


def acquire_single_instance_lock() -> bool:
    """
    Micro-Función W1: Evita instancias duplicadas del agente cuando el Watchdog
    del sistema operativo (schtasks / cron) verifica periódicamente el servicio.
    Si el agente ya está corriendo, retorna False y la segunda instancia termina en 0ms.
    Si el agente se cerró o cayó, el SO libera el candado automáticamente y el Watchdog lo levanta.
    """
    global _SINGLE_INSTANCE_LOCK_FH
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
    args = parser.parse_args()

    if args.check_screen_only:
        sys.exit(0 if check_macos_screen_permission(request_if_missing=False) else 2)

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
