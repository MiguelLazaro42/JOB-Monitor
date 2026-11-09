import argparse
import asyncio
import base64
import ctypes
import getpass
import hashlib
import io
import json
import os
import platform
import socket
import struct
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Optional

import mss
import websockets
from PIL import Image
from pynput.keyboard import Controller as KeyboardController, Key, KeyCode
from pynput.mouse import Button, Controller as MouseController

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config_agent.json"

DEFAULT_SERVER_URL = os.environ.get("RA_SERVER_URL", "ws://127.0.0.1:5060/ws/agent")
DEFAULT_TOKEN = os.environ.get("RA_AGENT_TOKEN", "ra-secret-token-2026")

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

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

    def get_active_window_title() -> str:
        try:
            out = subprocess.check_output(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to get name of first application process whose frontmost is true',
                ],
                stderr=subprocess.DEVNULL,
                timeout=1.5,
            ).decode("utf-8", errors="ignore").strip()
            return out or "macOS Finder"
        except Exception:
            return "macOS"

else:
    def get_idle_seconds() -> int:
        return 0

    def get_active_window_title() -> str:
        return platform.system()


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
    short_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{hostname.upper()}-{short_hash}"


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

        self.streaming: bool = False
        self.current_monitor: int = 1
        self.jpeg_quality: int = 65
        self.target_fps: int = 18
        self.scale_factor: float = 1.0
        self.ws: Optional[websockets.WebSocketClientProtocol] = None

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
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            img.thumbnail((420, 240), Image.Resampling.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=50)
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            return ""

    def capture_frame_packet(self, sct: mss.base.MSSBase) -> Optional[bytes]:
        try:
            if not (0 <= self.current_monitor < len(sct.monitors)):
                self.current_monitor = 1
            mon = sct.monitors[self.current_monitor]
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

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
            while True:
                await asyncio.sleep(4.0)
                if not self.ws:
                    break
                tick += 1
                try:
                    payload = {
                        "type": "telemetry",
                        "idle_seconds": get_idle_seconds(),
                        "active_window": get_active_window_title(),
                        "current_monitor": self.current_monitor,
                        "monitors": self.get_monitors_info(sct),
                    }
                    if tick % 3 == 1:
                        payload["thumbnail"] = self.capture_thumbnail_b64(sct)
                    await self.ws.send(json.dumps(payload))
                except Exception:
                    break

    async def run_forever(self):
        while True:
            try:
                async with websockets.connect(
                    self.server_url,
                    max_size=25 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    self.ws = ws
                    self.streaming = False

                    with mss.mss() as sct:
                        monitors = self.get_monitors_info(sct)
                        thumb = self.capture_thumbnail_b64(sct)

                    hello = {
                        "type": "register",
                        "token": self.token,
                        "agent_id": self.agent_id,
                        "alias": self.alias,
                        "hostname": self.hostname,
                        "username": self.username,
                        "os": f"{platform.system()} {platform.release()}",
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
                    finally:
                        stream_task.cancel()
                        telem_task.cancel()
                        self.ws = None
                        self.streaming = False

            except Exception:
                self.ws = None
                self.streaming = False
                await asyncio.sleep(4.0)


def main():
    parser = argparse.ArgumentParser(description="Agente Silencioso RemoteAssist - Fase 1")
    parser.add_argument("--server", type=str, default=None, help="URL WebSocket del Servidor Central")
    parser.add_argument("--token", type=str, default=None, help="Token secreto del servidor")
    parser.add_argument("--alias", type=str, default=None, help="Alias amigable de esta PC (ej. 'PC de Miguel')")
    args = parser.parse_args()

    cfg = load_or_create_agent_config(args.server, args.token, args.alias)
    agent = SilentRemoteAgent(cfg)
    try:
        asyncio.run(agent.run_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

