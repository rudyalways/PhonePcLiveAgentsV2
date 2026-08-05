#!/usr/bin/env python3
"""
Mobile Control Server for Sutando — receives input commands from the mobile app
and executes them on macOS via AppleScript / Quartz CGEvents.

Runs on port 7901, binds 0.0.0.0 for LAN access.
Auth reuses users.json (same SHA-256 scheme as livekit-token-server.py).

Usage: python3 src/mobile-control-server.py
"""

import hashlib
import json
import os
import re
import subprocess
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

USERS_FILE = Path(__file__).resolve().parent / "users.json"
PORT = int(os.environ.get("MOBILE_CONTROL_PORT", "7901"))

# ---------------------------------------------------------------------------
# Auth (mirrors livekit-token-server.py)
# ---------------------------------------------------------------------------

def load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    data = json.loads(USERS_FILE.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def verify_user(username: str, secret: str) -> dict | None:
    users = load_users()
    user = users.get(username)
    if not user:
        return None
    expected = user.get("secret_sha256", "")
    actual = hashlib.sha256(secret.encode()).hexdigest()
    if actual != expected:
        return None
    return user


# ---------------------------------------------------------------------------
# Screen info
# ---------------------------------------------------------------------------

_screen_size: tuple[int, int] | None = None


def get_screen_size() -> tuple[int, int]:
    """Return the main display's logical (UI-scale) resolution."""
    global _screen_size
    if _screen_size:
        return _screen_size

    # Try PyObjC (ships with macOS system Python)
    try:
        from AppKit import NSScreen  # type: ignore[import-not-found]
        frame = NSScreen.mainScreen().frame()
        _screen_size = (int(frame.size.width), int(frame.size.height))
        return _screen_size
    except Exception:
        pass

    # Fallback: parse system_profiler
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=10,
        )
        m = re.search(r"UI Looks like:\s*(\d+)\s*x\s*(\d+)", result.stdout)
        if m:
            _screen_size = (int(m.group(1)), int(m.group(2)))
            return _screen_size
        m = re.search(r"Resolution:\s*(\d+)\s*x\s*(\d+).*Retina", result.stdout)
        if m:
            _screen_size = (int(m.group(1)) // 2, int(m.group(2)) // 2)
            return _screen_size
        m = re.search(r"Resolution:\s*(\d+)\s*x\s*(\d+)", result.stdout)
        if m:
            _screen_size = (int(m.group(1)), int(m.group(2)))
            return _screen_size
    except Exception:
        pass

    _screen_size = (1920, 1080)
    return _screen_size


# ---------------------------------------------------------------------------
# Input actions (mirrors inline-tools.ts / browser-tools.ts)
# ---------------------------------------------------------------------------

def do_click(x: int, y: int, button: str = "left", double: bool = False) -> dict:
    try:
        import Quartz  # type: ignore[import-not-found]
        pt = (float(x), float(y))

        # Move mouse to target position first
        move = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, pt, Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
        time.sleep(0.02)

        if button == "right":
            down_type = Quartz.kCGEventRightMouseDown
            up_type = Quartz.kCGEventRightMouseUp
            btn = Quartz.kCGMouseButtonRight
        else:
            down_type = Quartz.kCGEventLeftMouseDown
            up_type = Quartz.kCGEventLeftMouseUp
            btn = Quartz.kCGMouseButtonLeft

        clicks = 2 if double else 1
        for i in range(clicks):
            if i > 0:
                time.sleep(0.05)
            down = Quartz.CGEventCreateMouseEvent(None, down_type, pt, btn)
            if double:
                Quartz.CGEventSetIntegerValueField(
                    down, Quartz.kCGMouseEventClickState, i + 1
                )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            time.sleep(0.02)
            up = Quartz.CGEventCreateMouseEvent(None, up_type, pt, btn)
            if double:
                Quartz.CGEventSetIntegerValueField(
                    up, Quartz.kCGMouseEventClickState, i + 1
                )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

        print(f"[MobileControl] click {button} at ({x},{y}) double={double}", flush=True)
        return {"status": "ok", "x": x, "y": y, "button": button, "double": double}
    except ImportError:
        # Fallback: AppleScript (less reliable — coordinates are app-relative)
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to click at {{{x}, {y}}}'],
                timeout=5, capture_output=True,
            )
            if double:
                time.sleep(0.05)
                subprocess.run(
                    ["osascript", "-e",
                     f'tell application "System Events" to click at {{{x}, {y}}}'],
                    timeout=5, capture_output=True,
                )
            print(f"[MobileControl] click {button} at ({x},{y}) double={double} (AppleScript fallback)", flush=True)
            return {"status": "ok", "x": x, "y": y, "button": button, "double": double, "method": "applescript"}
        except Exception as e:
            return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


KEY_MAP: dict[str, int] = {
    "enter": 36, "return": 36, "escape": 53, "esc": 53, "tab": 48,
    "delete": 51, "backspace": 51, "space": 49,
    "up": 126, "down": 125, "left": 123, "right": 124,
    "a": 0, "c": 8, "v": 9, "x": 7, "z": 6, "f": 3, "s": 1, "w": 13, "q": 12,
}


def do_key(key: str, modifiers: list[str] | None = None) -> dict:
    mods = modifiers or []
    mod_str = ""
    if mods:
        mod_str = f' using {{{", ".join(m + " down" for m in mods)}}}'
    key_code = KEY_MAP.get(key.lower())
    try:
        if key_code is not None:
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to key code {key_code}{mod_str}'],
                timeout=3, capture_output=True,
            )
        else:
            safe = key.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to keystroke "{safe}"{mod_str}'],
                timeout=3, capture_output=True,
            )
        print(f"[MobileControl] key {'+'.join(mods + [key])}", flush=True)
        return {"status": "ok", "key": key, "modifiers": mods}
    except Exception as e:
        return {"error": str(e)}


def do_type(text: str) -> dict:
    # Always use clipboard paste for non-ASCII (Chinese, emoji, etc.)
    # AppleScript keystroke only supports ASCII
    has_non_ascii = any(ord(c) > 127 for c in text)
    has_newline = "\n" in text or "\r" in text or "\\n" in text
    use_paste = has_non_ascii or has_newline or len(text) > 80

    try:
        if use_paste:
            paste_text = text.replace("\\n", "\n").replace("\\t", "\t")
            saved = ""
            try:
                saved = subprocess.run(
                    ["pbpaste"], capture_output=True, text=True, timeout=2
                ).stdout
            except Exception:
                pass
            tmp = f"/tmp/sutando-mobile-type-{int(time.time()*1000)}.txt"
            Path(tmp).write_text(paste_text, encoding='utf-8')
            subprocess.run(f"pbcopy < {tmp}", shell=True, timeout=2)
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to keystroke "v" using command down'],
                timeout=5, capture_output=True,
            )
            time.sleep(0.3)
            if saved:
                tmp_r = f"/tmp/sutando-mobile-restore-{int(time.time()*1000)}.txt"
                Path(tmp_r).write_text(saved, encoding='utf-8')
                subprocess.run(f"pbcopy < {tmp_r}", shell=True, timeout=2)
                try:
                    os.unlink(tmp_r)
                except Exception:
                    pass
            try:
                os.unlink(tmp)
            except Exception:
                pass
        else:
            # ASCII-only short text: use keystroke
            safe = text.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to keystroke "{safe}"'],
                timeout=5, capture_output=True,
            )
        print(f"[MobileControl] type ({len(text)} chars, paste={use_paste})", flush=True)
        return {"status": "ok", "length": len(text)}
    except Exception as e:
        return {"error": str(e)}


def do_scroll(delta_x: int = 0, delta_y: int = 0) -> dict:
    try:
        import Quartz  # type: ignore[import-not-found]
        ev = Quartz.CGEventCreateScrollWheelEvent(
            None, Quartz.kCGScrollEventUnitLine, 2, int(delta_y), int(delta_x),
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        return {"status": "ok", "deltaX": delta_x, "deltaY": delta_y}
    except ImportError:
        try:
            kc = 126 if delta_y > 0 else 125  # up / down
            for _ in range(abs(delta_y)):
                subprocess.run(
                    ["osascript", "-e",
                     f'tell application "System Events" to key code {kc}'],
                    timeout=2, capture_output=True,
                )
            return {"status": "ok", "deltaX": delta_x, "deltaY": delta_y, "method": "keys"}
        except Exception as e:
            return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def do_volume(level: int | None = None, mute: bool | None = None) -> dict:
    try:
        if mute is True:
            subprocess.run(
                ["osascript", "-e", "set volume with output muted"],
                timeout=5, capture_output=True,
            )
            return {"status": "ok", "muted": True}
        if mute is False:
            subprocess.run(
                ["osascript", "-e", "set volume without output muted"],
                timeout=5, capture_output=True,
            )
            return {"status": "ok", "muted": False}
        if level is not None:
            lvl = max(0, min(100, level))
            subprocess.run(
                ["osascript", "-e", f"set volume output volume {lvl}"],
                timeout=5, capture_output=True,
            )
            return {"status": "ok", "level": lvl}
        return {"error": "Specify level or mute"}
    except Exception as e:
        return {"error": str(e)}


def do_brightness(level: int) -> dict:
    lvl = max(0, min(100, level))
    try:
        subprocess.run(
            ["brightness", f"{lvl/100:.2f}"],
            timeout=5, capture_output=True, check=True,
        )
        return {"status": "ok", "level": lvl}
    except Exception:
        try:
            steps = round(lvl / 100 * 16)
            for _ in range(16):
                subprocess.run(
                    ["osascript", "-e",
                     'tell application "System Events" to key code 107'],
                    timeout=1, capture_output=True,
                )
            for _ in range(steps):
                subprocess.run(
                    ["osascript", "-e",
                     'tell application "System Events" to key code 113'],
                    timeout=1, capture_output=True,
                )
            return {"status": "ok", "level": lvl, "method": "key_codes"}
        except Exception as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class ControlHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._check_auth():
            return

        body = self._read_json()
        if body is None:
            return

        if path == "/input/click":
            x, y = body.get("x"), body.get("y")
            if x is None or y is None:
                return self._json(400, {"error": "x and y required"})
            self._json(200, do_click(
                int(x), int(y),
                body.get("button", "left"),
                body.get("double", False),
            ))

        elif path == "/input/key":
            key = body.get("key")
            if not key:
                return self._json(400, {"error": "key required"})
            self._json(200, do_key(key, body.get("modifiers")))

        elif path == "/input/type":
            text = body.get("text")
            if text is None:
                return self._json(400, {"error": "text required"})
            self._json(200, do_type(text))

        elif path == "/input/scroll":
            self._json(200, do_scroll(
                int(body.get("deltaX", 0)),
                int(body.get("deltaY", 0)),
            ))

        elif path == "/system/volume":
            self._json(200, do_volume(body.get("level"), body.get("mute")))

        elif path == "/system/brightness":
            lvl = body.get("level")
            if lvl is None:
                return self._json(400, {"error": "level required"})
            self._json(200, do_brightness(int(lvl)))

        else:
            self._json(404, {"error": "Not found"})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._json(200, {"status": "ok", "service": "mobile-control"})
        if path == "/screen/info":
            if not self._check_auth():
                return
            w, h = get_screen_size()
            return self._json(200, {"width": w, "height": h})
        self._json(404, {"error": "Not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # --- helpers ---

    def _check_auth(self) -> bool:
        user = self.headers.get("X-User", "")
        secret = self.headers.get("X-Secret", "")
        if not user or not secret:
            params = parse_qs(urlparse(self.path).query)
            user = params.get("user", [""])[0]
            secret = params.get("secret", [""])[0]
        if not user or not secret:
            self._json(401, {"error": "Missing credentials"})
            return False
        if not verify_user(user, secret):
            self._json(401, {"error": "Invalid credentials"})
            return False
        return True

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._json(400, {"error": "Invalid JSON"})
            return None

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-User, X-Secret")

    def log_message(self, fmt, *args):
        print(f"[MobileControl] {args[0]}", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    server = HTTPServer(("0.0.0.0", PORT), ControlHandler)
    users = load_users()
    w, h = get_screen_size()

    print(f"Mobile Control Server running on http://0.0.0.0:{PORT}", flush=True)
    print(f"  Screen size: {w}x{h}", flush=True)
    print(f"  Users loaded: {len(users)} ({', '.join(users.keys()) or 'none'})", flush=True)

    state_dir = Path(__file__).resolve().parent.parent / "state"
    state_dir.mkdir(exist_ok=True)
    heartbeat = state_dir / "mobile-control.heartbeat"

    try:
        heartbeat.write_text(str(int(time.time())))
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMobile Control Server stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
