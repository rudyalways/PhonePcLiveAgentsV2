#!/usr/bin/env python3
"""Omni-agent — phone HTML (camera+mic) → Qwen Omni → optional core via tasks/.

Phases:
  P0  HTTPS + WSS ingest (PCM/JPEG)
  P1  Qwen VoiceTrigger (VAD) audio round-trip
  P2  Frame upload + PromptTrigger scene_change
  P3  work() → tasks/results → speak/inject result

Usage:
  .venv/bin/python src/omni-agent.py
  Open https://<host>:7090/omni on the phone (TLS cert in state/).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from omni_provider_qwen import QwenOmniSession  # noqa: E402
from omni_scene import SceneChangeSensor  # noqa: E402
from omni_turn_gate import TurnGate, TurnRequest  # noqa: E402

load_dotenv(REPO / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("omni-agent")

PORT = int(os.environ.get("OMNI_PORT", "7090"))
SRC_DIR = Path(__file__).resolve().parent
STATE_DIR = REPO / "state"
CERT_FILE = STATE_DIR / "server.crt"
KEY_FILE = STATE_DIR / "server.key"
USERS_FILE = SRC_DIR / "users.json"
CLIENT_HTML = SRC_DIR / "omni-client.html"

TASKS_DIR = REPO / "tasks"
RESULTS_DIR = REPO / "results"
TASKS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)
STATE_DIR.mkdir(exist_ok=True)

SCENE_CHANGE_ENABLED = os.environ.get("OMNI_SCENE_CHANGE", "1").lower() in ("1", "true", "yes")
SCENE_COOLDOWN_MS = int(os.environ.get("OMNI_SCENE_COOLDOWN_MS", "10000"))
AUTH_REQUIRED = os.environ.get("OMNI_AUTH_REQUIRED", "1").lower() in ("1", "true", "yes")

INSTRUCTIONS = os.environ.get(
    "OMNI_INSTRUCTIONS",
    (
        "You are Sutando omni assistant on the user's phone camera and mic. "
        "Answer briefly by voice. When given a [Proactive: scene_change] prompt, "
        "briefly introduce what is clearly visible; if nothing notable or unclear, "
        "reply with exactly [[NO_SPEAK]] and nothing else. "
        "For non-trivial research or system work, say you will ask the core, "
        "and the client may send a work request."
    ),
)

SCENE_PROMPT = (
    "[Proactive: scene_change] Briefly introduce what is now clearly visible "
    "in the camera. If nothing notable or the same as before, reply exactly [[NO_SPEAK]]."
)


def load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    data = json.loads(USERS_FILE.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def verify_user(username: str, secret: str) -> bool:
    users = load_users()
    if not users:
        return not AUTH_REQUIRED
    user = users.get(username)
    if not user:
        return False
    expected = user.get("secret_sha256", "")
    actual = hashlib.sha256(secret.encode()).hexdigest()
    return actual == expected


class PhoneSession:
    def __init__(self, ws: web.WebSocketResponse, username: str) -> None:
        self.ws = ws
        self.username = username
        self.gate = TurnGate()
        self.gate.cooldowns_ms["scene_change"] = SCENE_COOLDOWN_MS
        self.scene = SceneChangeSensor()
        self.qwen: QwenOmniSession | None = None
        self._pending_work: dict[str, float] = {}
        self._closed = False
        self._assistant_text = ""
        self._audio_buf: list[str] = []

    async def send(self, payload: dict[str, Any]) -> None:
        if self.ws.closed or self._closed:
            return
        await self.ws.send_json(payload)

    async def status(self, state: str) -> None:
        await self.send({"type": "status", "state": state})

    async def start_qwen(self) -> None:
        api_key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
        if not api_key:
            await self.send({"type": "error", "message": "DASHSCOPE_API_KEY not set on server"})
            return
        self.qwen = QwenOmniSession(
            api_key=api_key,
            on_event=self._on_qwen_event,
            instructions=INSTRUCTIONS,
        )
        await self.qwen.connect()
        await self.send({"type": "session.ready", "provider": "qwen"})
        await self.status("listening")

    async def _on_qwen_event(self, data: dict[str, Any]) -> None:
        et = data.get("type", "")
        if et == "input_audio_buffer.speech_started":
            self.gate.voice_active = True
            await self.status("user_speaking")
        elif et == "input_audio_buffer.speech_stopped":
            self.gate.voice_active = False
            await self.status("listening")
        elif et == "response.created":
            self.gate.begin_response()
            self._assistant_text = ""
            self._audio_buf = []
            await self.status("responding")
        elif et == "response.done":
            self.gate.end_response()
            text = self._assistant_text.strip()
            suppress = "[[NO_SPEAK]]" in text
            if text:
                await self.send(
                    {
                        "type": "transcript",
                        "role": "assistant",
                        "text": "(no speak)" if suppress else text,
                        "suppressed": suppress,
                    }
                )
            if not suppress:
                for chunk in self._audio_buf:
                    await self.send({"type": "audio.out", "format": "pcm16le_24k", "data": chunk})
            self._audio_buf = []
            await self.status("listening")
        elif et in ("response.audio.delta", "response.output_audio.delta"):
            delta = data.get("delta") or data.get("audio") or ""
            if delta:
                self._audio_buf.append(delta)
        elif et in (
            "response.audio_transcript.delta",
            "response.output_audio_transcript.delta",
            "response.text.delta",
            "response.output_text.delta",
        ):
            self._assistant_text += str(data.get("delta", ""))
        elif et == "conversation.item.input_audio_transcription.completed":
            tx = str(data.get("transcript", "")).strip()
            if tx:
                await self.send({"type": "transcript", "role": "user", "text": tx})
        elif et == "error":
            await self.send({"type": "error", "message": json.dumps(data.get("error", data))})

    async def handle_audio(self, b64: str) -> None:
        if not self.qwen:
            return
        try:
            pcm = base64.b64decode(b64)
        except Exception:
            return
        await self.qwen.append_audio(pcm)

    async def handle_image(self, b64: str) -> None:
        if not self.qwen:
            return
        try:
            jpeg = base64.b64decode(b64)
        except Exception:
            return
        if self.scene.should_upload(jpeg):
            await self.qwen.append_image(jpeg)
        if SCENE_CHANGE_ENABLED and self.scene.observe(jpeg):
            await self._prompt_scene()

    async def _prompt_scene(self) -> None:
        req = TurnRequest(kind="prompt", reason="scene_change", prompt_text=SCENE_PROMPT)
        ok, why = self.gate.allow(req)
        if not ok:
            logger.info("scene_change skipped: %s", why)
            return
        assert self.qwen
        self.gate.mark_fired(req)
        await self.status("proactive")
        await self.qwen.prompt_turn(SCENE_PROMPT)

    async def handle_manual_prompt(self, text: str) -> None:
        req = TurnRequest(kind="prompt", reason="manual", prompt_text=text)
        ok, why = self.gate.allow(req)
        if not ok:
            await self.send({"type": "error", "message": f"prompt blocked: {why}"})
            return
        assert self.qwen
        self.gate.mark_fired(req)
        await self.qwen.prompt_turn(text)

    async def enqueue_work(self, task: str) -> str:
        task_id = f"task-{int(time.time() * 1000)}"
        content = (
            f"id: {task_id}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"task: {task}\n"
            f"source: omni\n"
            f"channel_id: omni-phone\n"
            f"username: {self.username}\n"
            f"access_tier: owner\n"
            f"priority: normal\n"
        )
        path = TASKS_DIR / f"{task_id}.txt"
        path.write_text(content)
        self._pending_work[task_id] = time.time()
        await self.status("working")
        await self.send({"type": "transcript", "role": "system", "text": f"Core task queued: {task_id}"})
        return task_id

    async def poll_results_once(self) -> None:
        for task_id in list(self._pending_work):
            result = RESULTS_DIR / f"{task_id}.txt"
            if not result.exists():
                if time.time() - self._pending_work[task_id] > 600:
                    del self._pending_work[task_id]
                continue
            text = result.read_text().strip()
            del self._pending_work[task_id]
            try:
                result.unlink(missing_ok=True)
                (TASKS_DIR / f"{task_id}.txt").unlink(missing_ok=True)
            except Exception:
                pass
            await self.send({"type": "transcript", "role": "assistant", "text": text[:2000]})
            if self.qwen:
                req = TurnRequest(
                    kind="prompt",
                    reason="work_result",
                    prompt_text=f"[System: Core finished. Speak this result to the user briefly.]\n\n{text[:1500]}",
                )
                ok, _ = self.gate.allow(req)
                if ok:
                    self.gate.mark_fired(req)
                    await self.qwen.prompt_turn(req.prompt_text)
            await self.status("listening")


async def result_poller(app: web.Application) -> None:
    while True:
        sessions: list[PhoneSession] = list(app["sessions"])
        for s in sessions:
            try:
                await s.poll_results_once()
            except Exception as e:
                logger.warning("result poll: %s", e)
        await asyncio.sleep(0.5)


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=8 * 1024 * 1024)
    await ws.prepare(request)
    session: PhoneSession | None = None
    app = request.app
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "invalid json"})
                continue
            typ = data.get("type")
            if typ == "session.start":
                user = str(data.get("user") or "default").strip()
                secret = str(data.get("auth") or data.get("secret") or "")
                if AUTH_REQUIRED and load_users() and not verify_user(user, secret):
                    await ws.send_json({"type": "error", "message": "auth failed"})
                    await ws.close()
                    break
                if not load_users() and AUTH_REQUIRED:
                    logger.warning("No users.json — accepting user=%s without auth", user)
                session = PhoneSession(ws, user)
                app["sessions"].add(session)
                await session.start_qwen()
            elif not session:
                await ws.send_json({"type": "error", "message": "send session.start first"})
            elif typ == "audio":
                await session.handle_audio(str(data.get("data") or ""))
            elif typ == "image":
                await session.handle_image(str(data.get("data") or ""))
            elif typ == "control":
                action = data.get("action")
                if action == "prompt_manual":
                    await session.handle_manual_prompt(str(data.get("text") or "Describe what you see."))
                elif action == "work":
                    await session.enqueue_work(str(data.get("task") or ""))
                elif action == "ping":
                    await session.send({"type": "pong", "ts": time.time()})
            else:
                await session.send({"type": "error", "message": f"unknown type {typ}"})
    finally:
        if session:
            session._closed = True
            app["sessions"].discard(session)
            if session.qwen:
                await session.qwen.close()
        if not ws.closed:
            await ws.close()
    return ws


async def index(_request: web.Request) -> web.StreamResponse:
    if not CLIENT_HTML.exists():
        return web.Response(text="omni-client.html missing", status=404)
    return web.FileResponse(CLIENT_HTML)


async def on_startup(app: web.Application) -> None:
    app["sessions"] = set()
    app["poller"] = asyncio.create_task(result_poller(app))


async def on_cleanup(app: web.Application) -> None:
    app["poller"].cancel()
    try:
        await app["poller"]
    except asyncio.CancelledError:
        pass
    for s in list(app["sessions"]):
        if s.qwen:
            await s.qwen.close()


def make_app() -> web.Application:
    app = web.Application(client_max_size=16 * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/omni", index)
    app.router.add_get("/omni-client.html", index)
    app.router.add_get("/ws", ws_handler)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    app = make_app()
    ssl_ctx = None
    proto = "http"
    if CERT_FILE.exists() and KEY_FILE.exists():
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
        proto = "https"
    else:
        logger.warning(
            "No TLS cert at %s — phone getUserMedia needs HTTPS. "
            "Generate: openssl req -x509 -newkey rsa:2048 -keyout %s -out %s "
            "-days 365 -nodes -subj /CN=sutando-local",
            CERT_FILE,
            KEY_FILE,
            CERT_FILE,
        )
    print(f"Omni agent at {proto}://0.0.0.0:{PORT}/omni  (ws: {proto.replace('http','ws')}://…/ws)", flush=True)
    web.run_app(app, host="0.0.0.0", port=PORT, ssl_context=ssl_ctx, print=None)


if __name__ == "__main__":
    main()
