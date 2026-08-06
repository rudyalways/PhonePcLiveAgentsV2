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
import re
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
from workspace_default import resolve_workspace  # noqa: E402

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

WORKSPACE = resolve_workspace()
TASKS_DIR = WORKSPACE / "tasks"
RESULTS_DIR = WORKSPACE / "results"
TASKS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(exist_ok=True)

SCENE_CHANGE_ENABLED = os.environ.get("OMNI_SCENE_CHANGE", "1").lower() in ("1", "true", "yes")
SCENE_COOLDOWN_MS = int(os.environ.get("OMNI_SCENE_COOLDOWN_MS", "10000"))
AUTH_REQUIRED = os.environ.get("OMNI_AUTH_REQUIRED", "1").lower() in ("1", "true", "yes")
WORK_HEARTBEAT_S = float(os.environ.get("OMNI_WORK_HEARTBEAT_S", "2"))
WORK_TIMEOUT_S = float(os.environ.get("OMNI_WORK_TIMEOUT_S", "600"))

# Tool name + description aligned with voice (task-bridge.ts workTool) and
# LiveKit (livekit-agent.py work) — same contract: name=work, param=task.
WORK_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "work",
    "description": (
        "Do the work. Call this for anything beyond simple greetings — questions, "
        "actions, research, writing, translation, file changes, system queries, "
        "explanations, analysis, open browser/URL, apps, email. "
        "This is how Sutando thinks and acts. Results are spoken back when ready. "
        "Also called core / submit a task / delegate to core — those all mean this tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Full description of the task to perform",
            }
        },
        "required": ["task"],
    },
}

# System prompt borrows voice/LiveKit DEFAULT BEHAVIOR + CRITICAL RULES
# (voice-agent-config.ts / livekit-agent.py), trimmed for phone camera+mic
# (no Zoom/meeting/inline keystroke tools on this surface).
INSTRUCTIONS = os.environ.get(
    "OMNI_INSTRUCTIONS",
    (
        "You are Sutando, a personal AI that belongs entirely to the user. "
        "You are on the user's phone camera and mic (omni). Keep spoken replies to 2–3 sentences.\n"
        "\n"
        "DEFAULT BEHAVIOR: Call work for almost everything.\n"
        "You are the voice/vision interface. The Sutando core (Claude Code) is the brain.\n"
        "Your job is to relay the user's requests to work and speak the results.\n"
        "\n"
        "ONLY answer directly (without calling work) for:\n"
        "- Simple greetings and yes/no acknowledgments\n"
        "- Self-introduction (who you are / what you can do)\n"
        "- Asking a clarifying question\n"
        "- Language switch requests (just switch and speak)\n"
        "- Describing what is clearly visible in the camera right now\n"
        "\n"
        "For EVERYTHING else, call work. This includes:\n"
        "- Open/close browser or apps, navigate, click, type, search\n"
        "- Questions about the system, code, files, email, calendar\n"
        "- Requests to do anything (write, read, change, create, delete, send)\n"
        "- Research, translation, analysis — anything you are not 100% certain about\n"
        "\n"
        "TOOLS:\n"
        "- work: THE default tool. Call it for any non-trivial request. "
        "Also called core, submit a task, send to core, ask the core, "
        "delegate to core — these all mean call this tool. "
        "Returns pending — say you started / are working on it, then wait for the result. "
        "Call work in the SAME turn before claiming any PC action is done.\n"
        "\n"
        "CRITICAL RULES:\n"
        "- NEVER pretend you called a tool. NEVER say done / already opened / 已经帮你 "
        "without actually calling work in this turn.\n"
        "- NEVER say you can't do that — call work and let the core handle it.\n"
        "- If a prior work call is still pending, say you are still waiting — do not invent success.\n"
        "- If you KNOW the answer from camera/context alone, answer directly; otherwise call work.\n"
        "- When in doubt, call work.\n"
        "\n"
        "SCENE CHANGE: When given a [Proactive: scene_change] prompt, briefly introduce "
        "what is clearly visible; if nothing notable or unclear, reply with exactly "
        "[[NO_SPEAK]] and nothing else.\n"
        "\n"
        "VOICE RULES: Keep responses short. Never read long file contents aloud — summarize."
    ),
)

# Spoken claims of PC action completion without a tool call this turn.
_FAKE_DONE_RE = re.compile(
    r"(已经帮你|已经打开|打开了|已打开|已经完成|弄好了|"
    r"already (opened|done|finished)|i (have |just )?(opened|closed|done)|"
    r"browser is open|opened the browser)",
    re.I,
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


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def _extract_function_call(raw: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Return (call_id, name, arguments_json)."""
    et = str(raw.get("type", ""))
    if et == "response.function_call_arguments.done":
        return (
            str(raw.get("call_id") or "") or None,
            str(raw.get("name") or "") or None,
            str(raw.get("arguments") or "{}"),
        )
    item = raw.get("item") if isinstance(raw.get("item"), dict) else None
    if item and item.get("type") == "function_call":
        return (
            str(item.get("call_id") or item.get("id") or "") or None,
            str(item.get("name") or "") or None,
            str(item.get("arguments") or "{}"),
        )
    resp = raw.get("response") if isinstance(raw.get("response"), dict) else {}
    for output in resp.get("output") or []:
        if isinstance(output, dict) and output.get("type") == "function_call":
            return (
                str(output.get("call_id") or output.get("id") or "") or None,
                str(output.get("name") or "") or None,
                str(output.get("arguments") or "{}"),
            )
    return None, None, None


def _parse_tool_parameters(arguments: str | None) -> tuple[dict[str, Any] | None, str]:
    """Return (params_dict_or_None, exact_json_or_raw_string) for logging."""
    raw = (arguments if arguments is not None else "").strip() or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, raw
    if isinstance(parsed, dict):
        return parsed, json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    return None, json.dumps(parsed, ensure_ascii=False)


class PhoneSession:
    def __init__(self, ws: web.WebSocketResponse, username: str) -> None:
        self.ws = ws
        self.username = username
        self.gate = TurnGate()
        self.gate.cooldowns_ms["scene_change"] = SCENE_COOLDOWN_MS
        self.scene = SceneChangeSensor()
        self.qwen: QwenOmniSession | None = None
        # task_id -> {started, task, last_hb, source, call_id?}
        self._pending_work: dict[str, dict[str, Any]] = {}
        self._closed = False
        self._assistant_text = ""
        self._audio_buf: list[str] = []
        self._tools_this_response = 0
        self._handled_call_ids: set[str] = set()

    async def send(self, payload: dict[str, Any]) -> None:
        if self.ws.closed or self._closed:
            return
        await self.ws.send_json(payload)

    async def status(self, state: str) -> None:
        await self.send({"type": "status", "state": state})

    async def activity(self, kind: str, message: str, **extra: Any) -> None:
        await self.send({"type": "activity", "kind": kind, "message": message, **extra})

    async def work_event(self, state: str, **extra: Any) -> None:
        await self.send({"type": "work", "state": state, **extra})

    async def start_qwen(self) -> None:
        api_key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
        if not api_key:
            await self.send({"type": "error", "message": "DASHSCOPE_API_KEY not set on server"})
            return
        self.qwen = QwenOmniSession(
            api_key=api_key,
            on_event=self._on_qwen_event,
            instructions=INSTRUCTIONS,
            tools=[WORK_TOOL],
        )
        await self.qwen.connect()
        await self.send(
            {
                "type": "session.ready",
                "provider": "qwen",
                "model": self.qwen.model,
                "scene_change": SCENE_CHANGE_ENABLED,
                "scene_cooldown_ms": SCENE_COOLDOWN_MS,
                "tools": ["work"],
            }
        )
        await self.status("listening")
        await self.activity("session", f"Qwen ready ({self.qwen.model}) · tool: work")
        await self.activity("session", f"Tasks dir: {TASKS_DIR}")

    async def _on_qwen_event(self, data: dict[str, Any]) -> None:
        et = data.get("type", "")
        if et == "input_audio_buffer.speech_started":
            self.gate.voice_active = True
            await self.status("user_speaking")
            await self.send({"type": "vad", "state": "speech_started"})
            await self.activity("vad", "VAD: speech started")
        elif et == "input_audio_buffer.speech_stopped":
            self.gate.voice_active = False
            await self.status("listening")
            await self.send({"type": "vad", "state": "speech_stopped"})
            await self.activity("vad", "VAD: speech stopped")
        elif et == "response.created":
            self.gate.begin_response()
            self._assistant_text = ""
            self._audio_buf = []
            self._tools_this_response = 0
            await self.status("responding")
            await self.send({"type": "stream", "state": "started"})
            await self.activity("stream", "Response streaming…")
        elif et == "response.done":
            self.gate.end_response()
            text = self._assistant_text.strip()
            suppress = "[[NO_SPEAK]]" in text
            fake_done = bool(
                text
                and self._tools_this_response == 0
                and _FAKE_DONE_RE.search(text)
            )
            if fake_done:
                # Don't play audio that claims a PC action that never ran.
                suppress = True
            await self.send(
                {
                    "type": "stream",
                    "state": "done",
                    "audio_chunks": len(self._audio_buf),
                    "suppressed": suppress,
                    "tools_called": self._tools_this_response,
                    "fake_done": fake_done,
                }
            )
            if text:
                await self.send(
                    {
                        "type": "transcript",
                        "role": "assistant",
                        "text": (
                            "(blocked: claimed action without work)"
                            if fake_done
                            else "(no speak)"
                            if suppress
                            else text
                        ),
                        "suppressed": suppress,
                        "final": True,
                    }
                )
            if not suppress:
                for chunk in self._audio_buf:
                    await self.send({"type": "audio.out", "format": "pcm16le_24k", "data": chunk})
            self._audio_buf = []
            if self._tools_this_response == 0 and text and not suppress:
                await self.activity(
                    "work",
                    "No work/tool delegated this turn (model spoke only)",
                )
            if fake_done:
                pending = len(self._pending_work)
                await self.activity(
                    "work",
                    "Blocked fake claim — spoke success without calling work"
                    + (f" ({pending} task(s) still pending)" if pending else ""),
                )
                if self.qwen:
                    try:
                        await self.qwen.prompt_turn(
                            "[System] You just claimed a PC action finished but did NOT "
                            "call the work tool in that turn — nothing ran. "
                            "If the user still wants it, call work now with a concrete task. "
                            "If a prior work call is still pending, say you are still waiting."
                        )
                    except Exception as e:
                        await self.activity("error", f"fake-done nudge failed: {e}")
            await self.status("listening" if not self._pending_work else "working")
            logger.info(
                "response.done tools=%s fake_done=%s chars=%s text=%s",
                self._tools_this_response,
                fake_done,
                len(text),
                text[:120],
            )
            await self.activity(
                "stream",
                "Response done"
                + (" (suppressed)" if suppress else f" · {len(text)} chars")
                + f" · tools={self._tools_this_response}",
            )
        elif et in ("response.audio.delta", "response.output_audio.delta"):
            delta = data.get("delta") or data.get("audio") or ""
            if delta:
                self._audio_buf.append(delta)
                if len(self._audio_buf) == 1 or len(self._audio_buf) % 8 == 0:
                    await self.send(
                        {"type": "stream", "state": "audio", "chunks": len(self._audio_buf)}
                    )
        elif et in (
            "response.audio_transcript.delta",
            "response.output_audio_transcript.delta",
            "response.text.delta",
            "response.output_text.delta",
        ):
            piece = str(data.get("delta", ""))
            self._assistant_text += piece
            if piece:
                await self.send(
                    {
                        "type": "transcript",
                        "role": "assistant",
                        "text": self._assistant_text,
                        "partial": True,
                    }
                )
        elif et == "conversation.item.input_audio_transcription.delta":
            piece = str(data.get("delta") or data.get("transcript") or "").strip()
            if piece:
                await self.send(
                    {"type": "transcript", "role": "user", "text": piece, "partial": True}
                )
        elif et == "conversation.item.input_audio_transcription.completed":
            tx = str(data.get("transcript", "")).strip()
            if tx:
                await self.send(
                    {"type": "transcript", "role": "user", "text": tx, "final": True}
                )
                await self.activity("asr", f"ASR: {tx[:120]}")
        elif et == "response.function_call_arguments.done" or (
            "function_call" in et and et.endswith(".done")
        ):
            _raw_args = str(
                data.get("arguments")
                or (data.get("item") or {}).get("arguments")
                or ""
            )
            logger.info(
                "Qwen tool event type=%s tool=%s parameters=%s call_id=%s",
                et,
                data.get("name") or (data.get("item") or {}).get("name"),
                _raw_args,
                data.get("call_id") or (data.get("item") or {}).get("call_id"),
            )
            await self._handle_tool_call(data)
        elif et in ("response.output_item.added", "conversation.item.created"):
            item = data.get("item") if isinstance(data.get("item"), dict) else None
            if item and item.get("type") == "function_call":
                logger.info(
                    "Qwen function_call item type=%s tool=%s parameters=%s status=%s",
                    et,
                    item.get("name"),
                    str(item.get("arguments") or ""),
                    item.get("status"),
                )
                if item.get("arguments") not in (None, "", "{}"):
                    # Fallback if provider never emits *.arguments.done
                    await self._handle_tool_call(data)
        elif et == "error":
            await self.send({"type": "error", "message": json.dumps(data.get("error", data))})
            await self.activity("error", "Qwen error", detail=str(data.get("error", data))[:200])

    async def _handle_tool_call(self, data: dict[str, Any]) -> None:
        call_id, name, arguments = _extract_function_call(data)
        if not call_id or not name:
            logger.warning("Tool event incomplete type=%s keys=%s", data.get("type"), list(data)[:20])
            await self.activity("work", f"Tool event incomplete: {data.get('type')}")
            return
        if call_id in self._handled_call_ids:
            return
        self._handled_call_ids.add(call_id)
        self._tools_this_response += 1
        params, params_exact = _parse_tool_parameters(arguments)
        # Exact name + full parameter JSON (no truncation) for audit.
        logger.info(
            "TOOL_CALL tool=%s parameters=%s call_id=%s",
            name,
            params_exact,
            call_id,
        )
        await self.activity(
            "work",
            f"Tool call: {name}({params_exact})",
        )
        await self.work_event(
            "tool_called",
            tool=name,
            call_id=call_id,
            parameters=params if params is not None else {"_raw": params_exact},
            arguments=params_exact,
        )

        if name != "work":
            if self.qwen:
                await self.qwen.send_function_output(
                    call_id, {"ok": False, "error": f"unknown tool {name}"}
                )
            logger.warning("Unknown tool rejected: tool=%s parameters=%s", name, params_exact)
            await self.activity("work", f"Unknown tool rejected: {name}({params_exact})")
            return

        task = ""
        if params is not None:
            task = str(params.get("task") or "").strip()
        else:
            task = (arguments or "").strip()
        if not task:
            if self.qwen:
                await self.qwen.send_function_output(
                    call_id, {"ok": False, "error": "missing task"}
                )
            logger.warning(
                "work tool called with empty task call_id=%s parameters=%s",
                call_id,
                params_exact,
            )
            await self.activity("work", f"work tool empty task: {name}({params_exact})")
            return

        task_id = await self.enqueue_work(task, source="tool", call_id=call_id)
        if self.qwen:
            await self.qwen.send_function_output(
                call_id,
                {
                    "ok": True,
                    "task_id": task_id,
                    "status": "queued",
                    "message": (
                        "Task queued for Sutando core — NOT finished yet. "
                        "Tell the user you started it; do not claim success until a later result arrives."
                    ),
                },
            )
        logger.info(
            "TOOL_CALL tool=%s → queued %s parameters=%s",
            name,
            task_id,
            params_exact,
        )
        await self.activity("work", f"Tool {name}({params_exact}) → queued {task_id}")

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
            await self.send(
                {"type": "trigger", "reason": "scene_change", "state": "skipped", "why": why}
            )
            await self.activity("trigger", f"Scene change skipped: {why}")
            return
        assert self.qwen
        self.gate.mark_fired(req)
        await self.status("proactive")
        await self.send({"type": "trigger", "reason": "scene_change", "state": "fired"})
        await self.activity("trigger", "Auto trigger: scene_change")
        await self.qwen.prompt_turn(SCENE_PROMPT)

    async def handle_manual_prompt(self, text: str) -> None:
        req = TurnRequest(kind="prompt", reason="manual", prompt_text=text)
        ok, why = self.gate.allow(req)
        if not ok:
            await self.send({"type": "error", "message": f"prompt blocked: {why}"})
            await self.activity("trigger", f"Manual prompt blocked: {why}")
            return
        assert self.qwen
        self.gate.mark_fired(req)
        await self.send({"type": "trigger", "reason": "manual", "state": "fired"})
        await self.activity("trigger", "Manual: Ask view")
        await self.qwen.prompt_turn(text)

    async def enqueue_work(
        self, task: str, *, source: str = "manual", call_id: str | None = None
    ) -> str:
        task_id = f"task-{int(time.time() * 1000)}"
        content = (
            f"id: {task_id}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"task: {task}\n"
            f"source: omni\n"
            f"via: {source}\n"
            f"channel_id: omni-phone\n"
            f"username: {self.username}\n"
            f"access_tier: owner\n"
            f"priority: normal\n"
        )
        if call_id:
            content += f"call_id: {call_id}\n"
        path = TASKS_DIR / f"{task_id}.txt"
        path.write_text(content)
        now = time.time()
        self._pending_work[task_id] = {
            "started": now,
            "task": task[:240],
            "last_hb": now,
            "source": source,
            "call_id": call_id,
        }
        logger.info(
            "ENQUEUE_WORK via=%s task_id=%s call_id=%s task=%s",
            source,
            task_id,
            call_id or "-",
            task[:160],
        )
        await self.status("working")
        await self.send(
            {"type": "transcript", "role": "system", "text": f"Core task queued: {task_id}"}
        )
        await self.send(
            {
                "type": "trigger",
                "reason": "work",
                "state": "queued",
                "task_id": task_id,
                "source": source,
            }
        )
        await self.work_event(
            "queued",
            task_id=task_id,
            source=source,
            task=task[:160],
            elapsed_ms=0,
        )
        await self.activity(
            "work",
            f"Task queued ({source}): {task_id} — {task[:80]}",
            task_id=task_id,
            elapsed_ms=0,
        )
        return task_id

    async def poll_results_once(self) -> None:
        now = time.time()
        for task_id in list(self._pending_work):
            meta = self._pending_work[task_id]
            started = float(meta.get("started") or now)
            elapsed = now - started
            result = RESULTS_DIR / f"{task_id}.txt"
            if not result.exists():
                if elapsed > WORK_TIMEOUT_S:
                    del self._pending_work[task_id]
                    await self.work_event(
                        "timeout",
                        task_id=task_id,
                        elapsed_ms=int(elapsed * 1000),
                    )
                    await self.activity(
                        "work",
                        f"Task TIMEOUT after {_fmt_elapsed(elapsed)}: {task_id}",
                        task_id=task_id,
                        elapsed_ms=int(elapsed * 1000),
                    )
                    if not self._pending_work:
                        await self.status("listening")
                    continue
                last_hb = float(meta.get("last_hb") or 0)
                if now - last_hb >= WORK_HEARTBEAT_S:
                    meta["last_hb"] = now
                    # Sticky chip only — do NOT append Activity rows (spam).
                    await self.work_event(
                        "processing",
                        task_id=task_id,
                        elapsed_ms=int(elapsed * 1000),
                        task=str(meta.get("task") or "")[:80],
                    )
                continue

            text = result.read_text().strip()
            del self._pending_work[task_id]
            try:
                result.unlink(missing_ok=True)
                (TASKS_DIR / f"{task_id}.txt").unlink(missing_ok=True)
            except Exception:
                pass
            await self.work_event(
                "result",
                task_id=task_id,
                elapsed_ms=int(elapsed * 1000),
                preview=text[:120],
            )
            await self.activity(
                "work",
                f"Task RESULT after {_fmt_elapsed(elapsed)}: {task_id} — {text[:100]}",
                task_id=task_id,
                elapsed_ms=int(elapsed * 1000),
            )
            await self.send({"type": "transcript", "role": "assistant", "text": text[:2000]})
            if self.qwen:
                req = TurnRequest(
                    kind="prompt",
                    reason="work_result",
                    prompt_text=(
                        f"[System: Core finished in {_fmt_elapsed(elapsed)}. "
                        f"Speak this result to the user briefly.]\n\n{text[:1500]}"
                    ),
                )
                ok, why = self.gate.allow(req)
                if ok:
                    self.gate.mark_fired(req)
                    await self.activity("work", "Speaking task result to user…")
                    await self.qwen.prompt_turn(req.prompt_text)
                else:
                    await self.activity("work", f"Could not speak result yet: {why}")
            await self.status("listening" if not self._pending_work else "working")


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
                    await session.enqueue_work(str(data.get("task") or ""), source="manual")
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
