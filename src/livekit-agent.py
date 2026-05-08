#!/usr/bin/env python3
"""
Sutando LiveKit Agent — multi-user worker mode.

Runs as a LiveKit Agents worker. LiveKit Cloud dispatches one agent job
per active room. Each room maps to one user (sutando-{username}), so tasks
and results are fully isolated in tasks/{username}/ and results/{username}/.

Usage:
  pip install "livekit-agents[google]" livekit python-dotenv
  python3 src/livekit-agent.py

Environment (.env):
  LIVEKIT_URL           — wss://your-project.livekit.cloud
  LIVEKIT_API_KEY       — from LiveKit Cloud dashboard
  LIVEKIT_API_SECRET    — from LiveKit Cloud dashboard
  GEMINI_API_KEY        — Google AI Studio key
"""

import asyncio
import json
import logging
import os
import subprocess
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
try:
    from pipeline_emit import emit as _pipeline_emit
except ImportError:
    def _pipeline_emit(**_kw):  # type: ignore[misc,no-redef]
        pass

load_dotenv(REPO / ".env")

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
    llm as lk_llm,
    function_tool,
    RunContext,
    RoomInputOptions,
)
from livekit.agents.voice.room_io import AudioInputOptions, RoomOptions

logger = logging.getLogger("sutando-agent")
logging.getLogger("livekit.plugins.openai").setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_VOICE_API_KEY = os.environ.get("GEMINI_VOICE_API_KEY", GEMINI_API_KEY)
VOICE_MODEL = os.environ.get("VOICE_MODEL", "gemini-2.5-flash")
REALTIME_PROVIDER = os.environ.get("REALTIME_PROVIDER", "gemini").lower()
LIVEKIT_AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME", "sutando-local")
LIVEKIT_WORKER_PORT = int(os.environ.get("LIVEKIT_WORKER_PORT", "7082"))

TASKS_DIR = REPO / "tasks"
RESULTS_DIR = REPO / "results"
STATE_DIR = REPO / "state"
TASKS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)
STATE_DIR.mkdir(exist_ok=True)

TASK_TIMEOUT_S = 600  # 10 minutes
RESULT_WATCHER_FALLBACK_S = float(os.environ.get("RESULT_WATCHER_FALLBACK_S", "0.5"))
RESULT_WATCHER_HEARTBEAT_S = 5
RESULT_WATCHER_HEARTBEAT = STATE_DIR / "livekit-result-watcher.heartbeat"

# Qwen/DashScope protocol handling lives in qwen_realtime_compat.py so Gemini
# and other providers continue to use their native LiveKit plugins unchanged.


# ---------------------------------------------------------------------------
# Realtime model factory — switch provider via REALTIME_PROVIDER in .env
# ---------------------------------------------------------------------------


def create_realtime_model() -> "lk_llm.RealtimeModel":
    if REALTIME_PROVIDER == "gemini":
        from livekit.plugins.google.beta import realtime as google_realtime
        return google_realtime.RealtimeModel(
            model=os.environ.get(
                "VOICE_NATIVE_AUDIO_MODEL",
                "gemini-2.5-flash-native-audio-preview-12-2025",
            ),
            voice=os.environ.get("REALTIME_VOICE", "Puck"),
            api_key=GEMINI_VOICE_API_KEY,
        )

    elif REALTIME_PROVIDER in ("openai", "qwen", "minimax"):
        from livekit.plugins.openai import realtime as openai_realtime

        provider_defaults = {
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "model": "gpt-4o-realtime-preview",
                "voice": "alloy",
            },
            "qwen": {
                "base_url": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                "api_key_env": "DASHSCOPE_API_KEY",
                "model": "qwen3.5-omni-plus-realtime",
                "voice": "Ethan",
            },
            "minimax": {
                "base_url": "https://api.minimax.chat/v1",
                "api_key_env": "MINIMAX_API_KEY",
                "model": "minimax-realtime",
                "voice": "default",
            },
        }
        cfg = provider_defaults[REALTIME_PROVIDER]

        if REALTIME_PROVIDER == "qwen":
            from qwen_realtime_compat import (
                patch_qwen_realtime,
                qwen_input_transcription_config,
                qwen_turn_detection_config,
            )

            patch_qwen_realtime()

        extra_kwargs = {}
        if REALTIME_PROVIDER == "qwen":
            # DashScope Qwen Omni realtime docs configure VAD plus
            # input_audio_transcription. Sending transcription=None matches our
            # logs: append-only with no input_audio_buffer.speech_* / no response.
            extra_kwargs["turn_detection"] = qwen_turn_detection_config()
            extra_kwargs["input_audio_noise_reduction"] = None
            extra_kwargs["input_audio_transcription"] = qwen_input_transcription_config()

        model = openai_realtime.RealtimeModel(
            model=os.environ.get("REALTIME_MODEL", cfg["model"]),
            voice=(
                os.environ.get("QWEN_REALTIME_VOICE", cfg["voice"])
                if REALTIME_PROVIDER == "qwen"
                else os.environ.get("REALTIME_VOICE", cfg["voice"])
            ),
            api_key=os.environ.get(cfg["api_key_env"], ""),
            base_url=os.environ.get("REALTIME_BASE_URL", cfg["base_url"]),
            **extra_kwargs,
        )

        try:
            from urllib.parse import urlparse

            opts = getattr(model, "_opts", None)
            raw_base = (opts.base_url if opts else "") or cfg["base_url"]
            parsed = urlparse(raw_base)
            api_host = parsed.netloc or raw_base[:80]
            td = extra_kwargs.get("turn_detection")
            if td is not None and REALTIME_PROVIDER == "qwen":
                vad_info = (
                    f"{td.get('type', '?') if isinstance(td, dict) else getattr(td, 'type', '?')}"
                    f"(thr={td.get('threshold', '?') if isinstance(td, dict) else getattr(td, 'threshold', '?')},"
                    f"prefix_ms={td.get('prefix_padding_ms', '?') if isinstance(td, dict) else getattr(td, 'prefix_padding_ms', '?')},"
                    f"silence_ms={td.get('silence_duration_ms', '?') if isinstance(td, dict) else getattr(td, 'silence_duration_ms', '?')})"
                )
            elif td is not None:
                vad_info = "server_vad(configured)"
            else:
                vad_info = "sdk-default"
            it = extra_kwargs.get("input_audio_transcription")
            if it is None:
                tx_info = "disabled"
            else:
                tx_info = getattr(it, "model", None) or str(it)
            logger.info(
                "[REALTIME] provider=%s model=%s host=%s vad=%s input_user_transcription=%s",
                REALTIME_PROVIDER,
                getattr(model, "model", cfg["model"]),
                api_host,
                vad_info,
                tx_info,
            )
        except Exception as ex:
            logger.debug("[REALTIME] config summary log failed: %s", ex)

        if REALTIME_PROVIDER == "qwen":
            model._capabilities.auto_tool_reply_generation = True
            logger.info("Qwen: set auto_tool_reply_generation=True")

        return model

    else:
        raise ValueError(
            f"Unknown REALTIME_PROVIDER: {REALTIME_PROVIDER}. "
            f"Supported: gemini, openai, qwen, minimax"
        )

# ---------------------------------------------------------------------------
# Helpers (ported from task-bridge.ts)
# ---------------------------------------------------------------------------


def write_owner_activity(channel: str, summary: str) -> None:
    try:
        payload = {
            "ts": int(time.time()),
            "channel": channel,
            "summary": summary[:80],
        }
        tmp = STATE_DIR / "last-owner-activity.json.tmp"
        tmp.write_text(json.dumps(payload))
        tmp.rename(STATE_DIR / "last-owner-activity.json")
    except Exception as e:
        logger.warning("owner-activity write failed: %s", e)


def archive_file(src: Path, kind: str, task_id: str, username: str) -> None:
    try:
        if not src.exists():
            return
        ym = datetime.now().strftime("%Y-%m")
        dest_dir = REPO / kind / username / "archive" / ym
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest_dir / f"{task_id}.txt"))
    except Exception:
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass


def log_conversation(username: str, role: str, text: str) -> None:
    log_file = REPO / "logs" / f"conversation-{username}.log"
    log_file.parent.mkdir(exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()}|{role}|{text.replace(chr(10), ' ')[:200]}\n"
    try:
        with open(log_file, "a") as f:
            f.write(line)
    except Exception:
        pass


def build_voice_context() -> str:
    """Read voice-context.txt and user profile for system prompt enrichment."""
    parts = []
    vc = REPO / "voice-context.txt"
    if vc.exists():
        parts.append(vc.read_text().strip())
    profile = (
        Path.home()
        / ".claude"
        / "projects"
        / "-Users-qingyi-project-sutando"
        / "memory"
        / "user_profile.md"
    )
    if profile.exists():
        parts.append(f"User profile:\n{profile.read_text().strip()}")
    return "\n\n".join(parts)


def build_greeting(username: str) -> str:
    """Generate the initial greeting instruction."""
    stand_name = ""
    sid = REPO / "stand-identity.json"
    if sid.exists():
        try:
            si = json.loads(sid.read_text())
            stand_name = f" — {si['name']}" if si.get("name") else ""
        except Exception:
            pass

    conversation_log = REPO / "logs" / f"conversation-{username}.log"
    has_history = conversation_log.exists()
    tutorial_hint = (
        ""
        if has_history
        else ' Then say: "If this is your first time, say tutorial and I\'ll walk you through what I can do."'
    )

    today = datetime.now().strftime("%Y-%m-%d")
    briefing_file = RESULTS_DIR / username / f"briefing-{today}.txt"
    briefing_hint = (
        ' Mention: "I have your morning briefing ready if you want it."'
        if has_history and briefing_file.exists()
        else ""
    )

    return (
        f"A user just connected. Say hi and introduce yourself as Sutando{stand_name}"
        f" — their personal AI. Ready to help with anything: voice tasks, screen control,"
        f" research, coding, email, scheduling. Keep it brief — 1-2 natural sentences."
        f"{tutorial_hint}{briefing_hint}"
    )


# ---------------------------------------------------------------------------
# System prompt (ported from voice-agent.ts L423-511)
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTIONS = f"""\
You are Sutando, a personal AI that belongs entirely to the user.
Named after Stands from JoJo's Bizarre Adventure — a personal spirit that fights for you.
Every Sutando evolves differently based on what its user needs.

You handle anything: research, writing, email, scheduling, code, logistics, phone calls, creative work.
You can see the user's screen (they are sharing it via LiveKit) and control the PC remotely.

{build_voice_context()}

DEFAULT BEHAVIOR: Call work for almost everything.
You are the voice interface. The Claude Code session is the brain.
Your job is to relay the user's requests to work and speak the results.

ONLY answer directly (without calling work) for:
- Simple greetings ("hi", "hello")
- Self-introduction ("who are you", "introduce yourself", "what can you do")
- Yes/no acknowledgments
- Asking the user a clarifying question
- get_current_time (current date/time)
- press_key, type_text — simple keystrokes
- describe_screen — screen viewing
- open_url, switch_app — app control

For EVERYTHING else, call work. This includes:
- Questions about the system, architecture, code, capabilities
- Requests to do anything (write, read, change, create, delete, send)
- Translation, research, analysis, explanations
- Anything you're not 100% certain about

TOOLS:
- work: THE default tool. Call it for any non-trivial request. Returns "pending" — say "Working on it" and wait for the result.
- get_current_time: Current date and time. Instant.
- press_key: Press a keyboard key or shortcut. Instant.
- type_text: Type text into the frontmost app. Instant.
- open_url: Open a URL in Chrome. Instant.
- switch_app: Switch to a macOS application. Instant.
- describe_screen: Capture and describe the current screen. Instant.
- capture_screen: Take a screenshot and return the path. Instant.

CRITICAL RULES:
- NEVER pretend you called a tool. NEVER say "done" without actually calling work.
- NEVER say "I can't do that" — you CAN do almost anything by calling work.
- For SIMPLE actions (press enter, clear input), use press_key — do NOT use work for keystrokes.
- For COMPLEX operations (git, code changes, file ops), ALWAYS delegate to work.
- If you KNOW the answer, answer directly. Only delegate to work for questions you genuinely cannot answer.
- MISSING CONTEXT: When the user references something you don't have context for, ALWAYS delegate to work.

VOICE RULES:
- Keep responses to 2–3 sentences. You are talking, not writing.
- Never read file contents or code aloud — summarize the outcome.
- Focus on what changed or was found, not how it was done.

GOODBYE: When the user says goodbye, respond with a SHORT farewell that STARTS with "Goodbye".
"""

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


class SutandoAgent(Agent):
    def __init__(self, username: str = "default") -> None:
        super().__init__(instructions=SYSTEM_INSTRUCTIONS)
        self._username = username
        self._tasks_dir = TASKS_DIR
        self._results_dir = RESULTS_DIR
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        self._results_dir.mkdir(parents=True, exist_ok=True)
        self._pending_tasks: dict[str, float] = {}
        self._delivered_results: set[str] = set()
        self._result_watcher_tasks: set[asyncio.Task] = set()
        self._last_transcript: str = ""  # Most recent user transcription (for inline tool logging)

    def _is_session_healthy(self) -> bool:
        """Return True if sutando-core updated core-status.json within the last 5 minutes."""
        try:
            status_file = REPO / "core-status.json"
            if not status_file.exists():
                return False
            data = json.loads(status_file.read_text())
            return (time.time() - data.get("ts", 0)) < 300
        except Exception:
            return False

    @function_tool()
    async def work(self, context: RunContext, task: str) -> str:
        """Do the work. Call this for any non-trivial request — questions, actions,
        research, writing, translation, file changes, system queries, explanations.
        Results are spoken back when ready."""
        task_id = f"task-{int(time.time() * 1000)}"
        timestamp = datetime.now(timezone.utc).isoformat()
        content = (
            f"id: {task_id}\n"
            f"timestamp: {timestamp}\n"
            f"task: {task}\n"
            f"source: livekit-voice\n"
            f"channel_id: local-voice\n"
            f"username: {self._username}\n"
            f"access_tier: owner\n"
        )
        task_file = self._tasks_dir / f"{task_id}.txt"
        task_file.write_text(content)
        self._pending_tasks[task_id] = time.time()
        write_owner_activity("voice", task)
        log_conversation(self._username, "user", task)
        if self._last_transcript:
            self._last_transcript = ""
        logger.info("Task %s [%s]: %s", task_id, self._username, task[:100])
        _pipeline_emit(
            phase="task_enqueued",
            task_id=task_id,
            ok=True,
            detail="tasks/*.txt written (LiveKit Gemini work)",
            component="livekit-agent",
        )

        # Give sutando-core 10s to respond (if alive, it's faster + richer)
        result_file = self._results_dir / f"{task_id}.txt"
        _pipeline_emit(
            phase="livekit_wait_core_started",
            task_id=task_id,
            ok=True,
            detail="polling results/ ≤10s for sutando-core",
            component="livekit-agent",
        )
        poll_interval_s = 0.5
        poll_attempts = int(10 / poll_interval_s)
        for poll_i in range(poll_attempts):
            if result_file.exists():
                result_text = result_file.read_text().strip()
                self._delivered_results.add(task_id)
                _pipeline_emit(
                    phase="result_file_observed",
                    task_id=task_id,
                    ok=True,
                    detail="livekit-sync: results/*.txt readable",
                    component="livekit-agent",
                )
                archive_file(result_file, "results", task_id, self._username)
                archive_file(task_file, "tasks", task_id, self._username)
                self._pending_tasks.pop(task_id, None)
                log_conversation(self._username, "assistant", result_text[:200])
                logger.info("Result %s (core): %s", task_id, result_text[:100])
                _pipeline_emit(
                    phase="agent_consumed_sync_result",
                    task_id=task_id,
                    ok=True,
                    detail="Returning to Gemini for TTS",
                    component="livekit-agent",
                )
                return result_text
            if poll_i in (9, poll_attempts - 1):
                _pipeline_emit(
                    phase="livekit_poll_core_pulse",
                    task_id=task_id,
                    ok=True,
                    detail=f"still waiting sutando-core {(poll_i + 1) * poll_interval_s:.1f}/10s",
                    component="livekit-agent",
                )
            await asyncio.sleep(poll_interval_s)

        _pipeline_emit(
            phase="livekit_sync_wait_timeout",
            task_id=task_id,
            ok=False,
            detail="10s no core result; task remains queued for sutando-core",
            component="livekit-agent",
        )
        return "I sent that to the core agent and it is still processing."

    def start_result_watcher(self, session: AgentSession) -> None:
        """Watch results/ for delayed core results and speak them into this session."""
        if self._result_watcher_tasks:
            return

        for coro in (
            self._result_watcher_heartbeat(),
            self._result_watcher_fswatch(session),
            self._result_watcher_fallback_scan(session),
        ):
            task = asyncio.create_task(coro)
            self._result_watcher_tasks.add(task)
            task.add_done_callback(self._result_watcher_tasks.discard)

        logger.info("Result watcher started for %s", self._username)
        _pipeline_emit(
            phase="livekit_result_watcher_started",
            task_id=f"watcher-{self._username}",
            ok=True,
            detail="fswatch + fallback scan",
            component="livekit-agent",
        )

    def _write_result_watcher_heartbeat(self, status: str = "running") -> None:
        try:
            payload = {
                "ts": int(time.time()),
                "pid": os.getpid(),
                "username": self._username,
                "status": status,
                "pending": len(self._pending_tasks),
            }
            tmp = RESULT_WATCHER_HEARTBEAT.with_suffix(".heartbeat.tmp")
            tmp.write_text(json.dumps(payload))
            tmp.rename(RESULT_WATCHER_HEARTBEAT)
        except Exception as e:
            logger.debug("result watcher heartbeat failed: %s", e)

    async def _result_watcher_heartbeat(self) -> None:
        while True:
            self._write_result_watcher_heartbeat()
            await asyncio.sleep(RESULT_WATCHER_HEARTBEAT_S)

    def _task_belongs_to_user(self, task_id: str) -> bool:
        task_file = self._tasks_dir / f"{task_id}.txt"
        if not task_file.exists():
            return False
        try:
            body = task_file.read_text()
        except Exception:
            return False
        return (
            "channel_id: local-voice" in body
            and f"username: {self._username}" in body
        )

    async def _deliver_result_file(self, result_file: Path, session: AgentSession, source: str) -> bool:
        if not result_file.name.startswith("task-") or result_file.suffix != ".txt":
            return False

        task_id = result_file.stem
        if task_id in self._delivered_results:
            return False
        submitted_at = self._pending_tasks.get(task_id)
        if submitted_at and (time.time() - submitted_at) < 10:
            # Let the synchronous work() path return fast results directly.
            return False
        if not self._task_belongs_to_user(task_id):
            return False

        try:
            result_text = result_file.read_text().strip()
        except Exception:
            return False
        if not result_text:
            return False

        self._delivered_results.add(task_id)
        task_file = self._tasks_dir / f"{task_id}.txt"
        _pipeline_emit(
            phase="result_file_observed",
            task_id=task_id,
            ok=True,
            detail=f"{source}: results/*.txt",
            component="livekit-agent",
        )
        archive_file(result_file, "results", task_id, self._username)
        archive_file(task_file, "tasks", task_id, self._username)
        self._pending_tasks.pop(task_id, None)
        log_conversation(self._username, "assistant", result_text[:200])
        logger.info("Result watcher delivered %s: %s", task_id, result_text[:100])
        _pipeline_emit(
            phase="agent_generate_reply_from_result",
            task_id=task_id,
            ok=True,
            detail="session.generate_reply with result excerpt",
            component="livekit-agent",
        )
        try:
            await session.generate_reply(
                instructions=f"The task completed. Tell the user this result concisely: {result_text[:500]}"
            )
        except Exception as e:
            logger.warning("Failed to speak watched result %s: %s", task_id, e)
            _pipeline_emit(
                phase="agent_generate_reply_from_result",
                task_id=task_id,
                ok=False,
                detail=f"generate_reply failed: {e}",
                component="livekit-agent",
            )
        return True

    async def _scan_result_files(self, session: AgentSession, source: str) -> None:
        for result_file in sorted(self._results_dir.glob("task-*.txt")):
            await self._deliver_result_file(result_file, session, source)

    async def _result_watcher_fallback_scan(self, session: AgentSession) -> None:
        while True:
            await self._scan_result_files(session, "fallback scan")
            await asyncio.sleep(RESULT_WATCHER_FALLBACK_S)

    async def _result_watcher_fswatch(self, session: AgentSession) -> None:
        if not shutil.which("fswatch"):
            logger.warning("fswatch not found; result watcher using fallback scan only")
            _pipeline_emit(
                phase="livekit_result_watcher_fswatch_missing",
                task_id=f"watcher-{self._username}",
                ok=False,
                detail="fswatch not found; fallback scan only",
                component="livekit-agent",
            )
            return

        while True:
            proc = None
            try:
                await self._scan_result_files(session, "startup scan")
                proc = await asyncio.create_subprocess_exec(
                    "fswatch",
                    "-0",
                    str(self._results_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                logger.info("Result fswatch started for %s", self._username)
                while True:
                    assert proc.stdout is not None
                    raw = await proc.stdout.readuntil(b"\0")
                    changed = Path(raw.rstrip(b"\0").decode("utf-8", errors="ignore"))
                    if changed.is_dir():
                        await self._scan_result_files(session, "fswatch dir event")
                    else:
                        await self._deliver_result_file(changed, session, "fswatch")
            except asyncio.CancelledError:
                if proc and proc.returncode is None:
                    proc.terminate()
                raise
            except Exception as e:
                logger.warning("result fswatch loop restarting: %s", e)
                _pipeline_emit(
                    phase="livekit_result_watcher_restart",
                    task_id=f"watcher-{self._username}",
                    ok=False,
                    detail=str(e)[:200],
                    component="livekit-agent",
                )
                if proc and proc.returncode is None:
                    proc.terminate()
                await asyncio.sleep(2)

    def _log_inline_tool(self, result: str) -> None:
        """Log an inline tool call (non-work) to the conversation log using last transcription."""
        if self._last_transcript:
            log_conversation(self._username, "user", self._last_transcript)
            self._last_transcript = ""
        log_conversation(self._username, "assistant", result)

    def _log_direct_response(self, response_text: str) -> None:
        """Log direct realtime answers that do not call work() or an inline tool."""
        if not self._last_transcript or not response_text.strip():
            return
        log_conversation(self._username, "user", self._last_transcript)
        self._last_transcript = ""
        log_conversation(self._username, "assistant", response_text.strip())

    @function_tool()
    async def get_current_time(self, context: RunContext) -> str:
        """Get the current date and time."""
        now = datetime.now()
        return now.strftime("%A, %B %d, %Y — %I:%M %p")

    @function_tool()
    async def press_key(
        self,
        context: RunContext,
        key: str,
        modifiers: str | list[str] | None = None,
        app: str | None = None,
    ) -> str:
        """Press a keyboard key or shortcut in the frontmost app.
        key: enter, escape, tab, delete, space, up, down, left, right, or a letter.
        modifiers: optional list of command, shift, control, option.
        app: optional target app name to activate first."""
        if isinstance(modifiers, str):
            try:
                modifiers = json.loads(modifiers)
            except (json.JSONDecodeError, TypeError):
                modifiers = [modifiers] if modifiers else []
        if modifiers is None:
            modifiers = []
        try:
            if app:
                subprocess.run(
                    ["osascript", "-e", f'tell application "{app}" to activate'],
                    timeout=3,
                    capture_output=True,
                )
                await asyncio.sleep(0.3)

            key_map = {
                "enter": 36, "return": 36, "escape": 53, "esc": 53, "tab": 48,
                "delete": 51, "backspace": 51, "space": 49,
                "up": 126, "down": 125, "left": 123, "right": 124,
                "a": 0, "c": 8, "v": 9, "x": 7, "z": 6, "f": 3, "s": 1, "w": 13, "q": 12,
            }
            key_code = key_map.get(key.lower())
            mod_str = (
                f" using {{{', '.join(m + ' down' for m in modifiers)}}}"
                if modifiers
                else ""
            )

            if key_code is not None:
                script = f'tell application "System Events" to key code {key_code}{mod_str}'
            else:
                script = f'tell application "System Events" to keystroke "{key}"{mod_str}'

            subprocess.run(
                ["osascript", "-e", script], timeout=3, capture_output=True
            )
            result = f"Pressed {'+'.join(modifiers + [key])}"
        except Exception as e:
            result = f"press_key failed: {e}"
        self._log_inline_tool(result)
        return result

    @function_tool()
    async def type_text(self, context: RunContext, text: str) -> str:
        """Type text into the frontmost application using clipboard paste for reliability."""
        try:
            subprocess.run(
                ["osascript", "-e", f'set the clipboard to "{text}"'],
                timeout=3,
                capture_output=True,
            )
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to keystroke "v" using command down',
                ],
                timeout=3,
                capture_output=True,
            )
            result = f"Typed {len(text)} characters"
        except Exception as e:
            result = f"type_text failed: {e}"
        self._log_inline_tool(result)
        return result

    @function_tool()
    async def open_url(self, context: RunContext, url: str) -> str:
        """Open a URL in a new Chrome tab."""
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'tell application "Google Chrome" to tell front window to make new tab with properties {{URL:"{url}"}}',
                ],
                timeout=5,
                capture_output=True,
            )
            result = f"Opened {url}"
        except Exception as e:
            result = f"Failed to open {url}: {e}"
        self._log_inline_tool(result)
        return result

    @function_tool()
    async def switch_app(self, context: RunContext, app: str) -> str:
        """Switch to (activate) a macOS application."""
        aliases = {
            "vs code": "Visual Studio Code", "vscode": "Visual Studio Code",
            "chrome": "Google Chrome", "safari": "Safari",
            "terminal": "Terminal", "finder": "Finder",
            "slack": "Slack", "discord": "Discord",
        }
        app_name = aliases.get(app.lower(), app)
        try:
            subprocess.run(
                ["osascript", "-e", f'tell application "{app_name}" to activate'],
                timeout=10,
                capture_output=True,
            )
            result = f"Switched to {app_name}"
        except Exception as e:
            result = f"Failed to switch to {app_name}: {e}"
        self._log_inline_tool(result)
        return result

    @function_tool()
    async def describe_screen(self, context: RunContext) -> str:
        """Capture the current screen and describe what's on it."""
        try:
            port = os.environ.get("SCREEN_CAPTURE_PORT", "7900")
            result = subprocess.run(
                [
                    "curl",
                    "-s",
                    f"http://localhost:{port}/capture",
                ],
                timeout=10,
                capture_output=True,
                text=True,
            )
            data = json.loads(result.stdout)
            return f"Screenshot captured: {data.get('path', 'unknown')}"
        except Exception as e:
            return f"Screen capture failed: {e}"

    @function_tool()
    async def capture_screen(self, context: RunContext) -> str:
        """Take a screenshot and return the file path."""
        try:
            path = f"/tmp/sutando-screen-{int(time.time())}.png"
            subprocess.run(
                ["screencapture", "-x", path], timeout=5, capture_output=True
            )
            return f"Screenshot saved: {path}"
        except Exception as e:
            return f"Screenshot failed: {e}"


# ---------------------------------------------------------------------------
# Worker mode entrypoint — one job per room (= one job per user)
# ---------------------------------------------------------------------------


async def entrypoint(ctx: JobContext):
    # Room name convention: sutando-{username}
    room_name = ctx.room.name
    username = room_name.removeprefix("sutando-") if room_name.startswith("sutando-") else room_name
    logger.info("Agent job started: room=%s username=%s", room_name, username)

    # Write heartbeat
    heartbeat = STATE_DIR / "livekit-agent.heartbeat"
    heartbeat.write_text(str(int(time.time())))

    # Debug: log room-level track events
    @ctx.room.on("track_subscribed")
    def _on_track_sub(track, pub, participant):
        logger.info("[PIPELINE] track_subscribed: kind=%s source=%s from=%s sid=%s",
                    track.kind, pub.source, participant.identity, track.sid)

    @ctx.room.on("track_published")
    def _on_track_pub(pub, participant):
        logger.info("[PIPELINE] track_published: kind=%s source=%s from=%s",
                    pub.kind, pub.source, participant.identity)

    @ctx.room.on("participant_connected")
    def _on_p_connect(participant):
        logger.info("[PIPELINE] participant_connected: %s", participant.identity)

    @ctx.room.on("participant_disconnected")
    def _on_p_disconnect(participant):
        logger.info("[PIPELINE] participant_disconnected: %s", participant.identity)

    await ctx.connect()

    logger.info("[DISPATCH] job received: room=%s username=%s", room_name, username)
    for p in ctx.room.remote_participants.values():
        logger.info("[DISPATCH] existing participant: identity=%s tracks=%d",
                    p.identity, len(p.track_publications))

    agent = SutandoAgent(username=username)
    realtime_model = create_realtime_model()
    logger.info("Realtime provider: %s username: %s", REALTIME_PROVIDER, username)

    session = AgentSession(llm=realtime_model)
    greeting = build_greeting(username)

    if REALTIME_PROVIDER == "qwen":
        from qwen_realtime_compat import QWEN_INPUT_SAMPLE_RATE

        # RoomOptions (not legacy RoomInputOptions) so we can disable input AGC/AP: logs showed
        # ~0 RMS int16 on /mobile (APM can zero mic); Qwen then never sees speech.
        logger.info(
            "[SESSION] RoomOptions: participant_identity=phone-user "
            "audio=%dHz mono AGC=False text=True (Qwen)",
            QWEN_INPUT_SAMPLE_RATE,
        )
        await session.start(
            room=ctx.room,
            agent=agent,
            room_options=RoomOptions(
                audio_input=AudioInputOptions(
                    sample_rate=QWEN_INPUT_SAMPLE_RATE,
                    auto_gain_control=False,
                ),
                text_input=True,
                close_on_disconnect=False,
                participant_identity="phone-user",
            ),
        )
    else:
        logger.info(
            "[SESSION] RoomInputOptions: participant_identity=phone-user audio=True text=True "
            "close_on_disconnect=False (audio from phone-user only)"
        )
        room_in = dict(
            text_enabled=True,
            audio_enabled=True,
            close_on_disconnect=False,
            participant_identity="phone-user",
        )
        await session.start(
            room=ctx.room,
            agent=agent,
            room_input_options=RoomInputOptions(**room_in),
        )
    agent.start_result_watcher(session)

    # Capture user transcriptions for inline tool logging (open_url, press_key, etc.)
    # These tools don't go through work(), so we hook the realtime session events.
    # NOTE: session.llm._sessions is a WeakSet; _session (no 's') is a class-level type annotation.
    try:
        realtime_sessions = list(getattr(session.llm, '_sessions', set()))
        if realtime_sessions:
            realtime_session = realtime_sessions[0]
            def _capture_transcript(event: dict) -> None:
                event_type = event.get("type")
                if event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "").strip()
                    if transcript:
                        agent._last_transcript = transcript
                        logger.info("[TRANSCRIPT] user input_audio (completed): %s", transcript[:400])
                elif event_type in ("response.output_audio_transcript.done", "response.output_text.done"):
                    response_text = (
                        event.get("transcript")
                        or event.get("text")
                        or event.get("delta")
                        or ""
                    )
                    if response_text and str(response_text).strip():
                        logger.info("[TRANSCRIPT] assistant output (done): %s", str(response_text).strip()[:400])
                    agent._log_direct_response(response_text)
            realtime_session.on("openai_server_event_received", _capture_transcript)
            logger.info("Transcript capture hook attached to realtime session")
        else:
            logger.warning("No realtime sessions found after session.start() — transcript capture unavailable")
    except Exception as e:
        logger.warning("Could not attach transcription capture: %s", e)

    # Greeting: skip for Qwen — the generate_reply timeout corrupts Qwen's
    # session state and suppresses VAD from detecting subsequent user speech.
    if REALTIME_PROVIDER not in ("qwen",):
        try:
            await asyncio.wait_for(
                session.generate_reply(instructions=greeting),
                timeout=10,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("Greeting skipped (%s: %s)", type(e).__name__, e)
    else:
        logger.info("Skipping greeting for %s provider", REALTIME_PROVIDER)

    logger.info("Sutando agent ready — room=%s username=%s", room_name, username)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=LIVEKIT_AGENT_NAME,
            port=LIVEKIT_WORKER_PORT,
        )
    )
