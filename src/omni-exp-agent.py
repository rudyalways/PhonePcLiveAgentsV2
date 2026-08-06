#!/usr/bin/env python3
"""Omni-agent — phone HTML (camera+mic) → Qwen Omni → optional core via tasks/.

Phases:
  P0  HTTPS + WSS ingest (PCM/JPEG)
  P1  Qwen VoiceTrigger (VAD) audio round-trip
  P2  Frame upload + PromptTrigger scene_change
  P3  work() → tasks/results → speak/inject result

Usage:
  .venv/bin/python src/omni-exp-agent.py
  Open https://<host>:7090/omni-exp on the phone (TLS cert in state/).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from core_readiness import probe_core_readiness  # noqa: E402
from omni_exp_provider_qwen import QwenOmniSession  # noqa: E402
from omni_exp_mode import (  # noqa: E402
    build_omni_exp_instructions,
    format_work_task,
    normalize_omni_exp_mode,
    scene_prompt_for_mode,
    task_system_suffix,
    work_tool_description,
)
from omni_exp_result_speak import (  # noqa: E402
    DELIVER_RETRY_DELAYS_S,
    TRUST_DONE_CLAIM_S,
    WORK_RESULT_DONE_EPILOGUE,
    extract_task_result_body,
    frame_task_result_prompt,
    is_fake_done_claim,
    is_stale_wait_claim,
    is_wait_meta_task,
)
from omni_exp_scene import SceneChangeSensor  # noqa: E402
from omni_exp_speak_queue import SpeakItem, SpeakQueue  # noqa: E402
from omni_exp_turn_gate import TurnGate, TurnRequest  # noqa: E402
from workspace_default import resolve_workspace  # noqa: E402

load_dotenv(REPO / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("omni-exp-agent")


def _env_omni_exp(key: str, default: str = "") -> str:
    """Prefer OMNI_EXP_*; fall back to legacy OMNI_* for one transition."""
    exp = os.environ.get(f"OMNI_EXP_{key}")
    if exp is not None:
        return exp
    legacy = os.environ.get(f"OMNI_{key}")
    if legacy is not None:
        return legacy
    return default


PORT = int(_env_omni_exp("PORT", "7090"))
SRC_DIR = Path(__file__).resolve().parent
STATE_DIR = REPO / "state"
CERT_FILE = STATE_DIR / "server.crt"
KEY_FILE = STATE_DIR / "server.key"
USERS_FILE = SRC_DIR / "users.json"
CLIENT_HTML = SRC_DIR / "omni-exp-client.html"

WORKSPACE = resolve_workspace()
TASKS_DIR = WORKSPACE / "tasks"
RESULTS_DIR = WORKSPACE / "results"
TASKS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(exist_ok=True)

SCENE_CHANGE_ENABLED = _env_omni_exp("SCENE_CHANGE", "1").lower() in ("1", "true", "yes")
# Default 30s — 10s was chatty when the phone hand/lighting kept re-triggering.
SCENE_COOLDOWN_MS = int(_env_omni_exp("SCENE_COOLDOWN_MS", "30000"))
# Mean abs-diff on 64×36 gray thumb (0–255). Higher = fewer false scene fires.
SCENE_ENTER_THRESHOLD = float(_env_omni_exp("SCENE_THRESHOLD", "28"))
# Upload near-dupe skip (MAD). Independent of SCENE_THRESHOLD so quiet scene
# fires do not starve the rolling vision window (main omni uses ~18*0.35).
UPLOAD_DEDUPE_THRESHOLD = float(_env_omni_exp("UPLOAD_DEDUPE", str(18.0 * 0.35)))
# Force a frame to Qwen at least this often even if the scene looks static.
UPLOAD_KEEPALIVE_S = float(_env_omni_exp("UPLOAD_KEEPALIVE_S", "8"))
# After this many consecutive [[NO_SPEAK]] scene replies, multiply cooldown.
SCENE_NOSPEAK_BACKOFF_AFTER = int(_env_omni_exp("SCENE_NOSPEAK_BACKOFF_AFTER", "2"))
SCENE_NOSPEAK_BACKOFF_MULT = float(_env_omni_exp("SCENE_NOSPEAK_BACKOFF_MULT", "3"))
# Fake-done nudge: at most one re-prompt per this many seconds (stops nudge loops).
FAKE_DONE_NUDGE_COOLDOWN_S = float(_env_omni_exp("FAKE_DONE_NUDGE_COOLDOWN_S", "20"))
STALE_WAIT_NUDGE_COOLDOWN_S = float(_env_omni_exp("STALE_WAIT_NUDGE_COOLDOWN_S", "12"))
# Work-result speak drain (docs/omni-exp-agent-design.md ready_merge): serial|concat|latest
WORK_RESULT_MERGE = _env_omni_exp("WORK_RESULT_MERGE", "serial").strip().lower()
SPEAK_QUEUE_MAX = int(_env_omni_exp("SPEAK_QUEUE_MAX", "32"))
AUTH_REQUIRED = _env_omni_exp("AUTH_REQUIRED", "1").lower() in ("1", "true", "yes")
WORK_HEARTBEAT_S = float(_env_omni_exp("WORK_HEARTBEAT_S", "2"))
WORK_TIMEOUT_S = float(_env_omni_exp("WORK_TIMEOUT_S", "600"))
# .alive mtime younger than this → core considered up (matches health-check ~90s).
CORE_ALIVE_MAX_AGE_S = float(_env_omni_exp("CORE_ALIVE_MAX_AGE_S", "90"))
ALLOW_START_CORE = _env_omni_exp("ALLOW_START_CORE", "1").lower() in (
    "1",
    "true",
    "yes",
)
START_CLI = REPO / "src" / "agent" / "start-cli.sh"
HEARTBEAT_PY = REPO / "src" / "core_heartbeat.py"
TMUX_FEEDER = REPO / "src" / "omni-exp-watch-tasks-to-tmux-supervisor.sh"
TMUX_SOCKET = os.environ.get("SUTANDO_TMUX_SOCKET", "/tmp/sutando-tmux.sock")
TMUX_SESSION = os.environ.get("SUTANDO_TMUX_SESSION", "sutando-core")
# Keep Monitor-less cores fed when omni writes tasks (OpenRouter / no Monitor).
ENSURE_TMUX_FEEDER = os.environ.get("SUTANDO_TMUX_TASK_FEEDER", "auto").lower() not in (
    "0",
    "false",
    "no",
    "off",
)
LOGS_DIR = WORKSPACE / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _host_label() -> str:
    try:
        out = subprocess.check_output(
            ["bash", str(REPO / "scripts" / "sutando-config.sh"), "host-label"],
            cwd=str(REPO),
            text=True,
            timeout=5,
        )
        label = out.strip()
        if label:
            return label
    except Exception:
        pass
    return socket.gethostname().split(".")[0] or "local"


HOST_LABEL = _host_label()

# TCC-safe completion signal for launchd tmux feeder (cannot read ~/Documents/results).
FEEDER_DONE_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Sutando"
    / "omni-exp-feeder"
    / "state"
    / "omni-exp-watch-tasks-to-tmux.done"
)


def mark_feeder_done(task_id: str) -> None:
    """Tell launchd feeder this task is finished (stop re-nudge)."""
    base = task_id if task_id.endswith(".txt") else f"{task_id}.txt"
    try:
        FEEDER_DONE_DIR.mkdir(parents=True, exist_ok=True)
        (FEEDER_DONE_DIR / base).write_text("done\n")
        inbox = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Sutando"
            / "omni-exp-feeder"
            / "inbox"
            / base
        )
        inbox.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("feeder done marker failed for %s: %s", base, e)


def _launchd_feeder_running() -> bool:
    """Prefer the TCC-safe launchd inbox feeder over the Documents scanner."""
    label = "com.sutando.omni-exp-tmux-task-feeder"
    try:
        uid = os.getuid()
        out = subprocess.check_output(
            ["launchctl", "print", f"gui/{uid}/{label}"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
        if "state = running" in out or "\tpid = " in out:
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    # bootstrap API sometimes broken while load -w still registered the job.
    try:
        out = subprocess.check_output(
            ["launchctl", "list"], text=True, timeout=3, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            if label not in line:
                continue
            pid = line.split()[0]
            return pid.isdigit()
    except (OSError, subprocess.SubprocessError):
        return False
    return False


def _stop_documents_task_feeder() -> None:
    """Kill the repo-path Documents scanner if launchd inbox feeder owns the job."""
    patterns = (
        "omni-exp-watch-tasks-to-tmux-supervisor.sh",
        f"{REPO}/src/omni-exp-watch-tasks-to-tmux.sh",
    )
    try:
        out = subprocess.check_output(["pgrep", "-fl", "omni-exp-watch-tasks"], text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return
    for line in out.splitlines():
        # Never kill the Application Support / launchd copy.
        if "Application Support" in line or "omni-exp-feeder" in line:
            continue
        if not any(p in line for p in patterns):
            continue
        try:
            pid = int(line.split(None, 1)[0])
            os.kill(pid, 9)
            logger.info("Stopped duplicate Documents tmux feeder pid=%s", pid)
        except (ValueError, OSError):
            pass
    # Drop stale supervisor pid stamp so a later fallback can start cleanly.
    for name in (
        "omni-exp-watch-tasks-to-tmux-supervisor.pid",
        "omni-exp-watch-tasks-to-tmux.pid",
    ):
        try:
            (WORKSPACE / "state" / name).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        import shutil

        shutil.rmtree(WORKSPACE / "state" / "omni-exp-watch-tasks-to-tmux.lock", ignore_errors=True)
    except Exception:
        pass


def ensure_tmux_task_feeder() -> None:
    """Ensure a Monitor fallback feeder is alive.

    Prefer launchd inbox feeder (Application Support). Do NOT also start the
    Documents-scanning supervisor — duplicates paste TASK_FILE into a busy
    core and the scanner's 'missing task file ⇒ done' heuristic races the
    launchd done-markers.
    """
    if not ENSURE_TMUX_FEEDER:
        return
    if _launchd_feeder_running():
        _stop_documents_task_feeder()
        return
    if not TMUX_FEEDER.is_file():
        return
    pid_path = WORKSPACE / "state" / "omni-exp-watch-tasks-to-tmux-supervisor.pid"
    try:
        if pid_path.is_file():
            old = int((pid_path.read_text() or "0").strip() or "0")
            if old > 0:
                os.kill(old, 0)
                out = subprocess.check_output(
                    ["ps", "-p", str(old), "-o", "args="],
                    text=True,
                    timeout=2,
                )
                if "omni-exp-watch-tasks-to-tmux-supervisor" in out:
                    return
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        subprocess.Popen(
            ["bash", str(TMUX_FEEDER)],
            cwd=str(REPO),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("Started tmux task feeder supervisor (Monitor fallback)")
    except Exception as e:
        logger.warning("Could not start tmux task feeder: %s", e)


_PANE_ERR_CACHE: dict[str, Any] = {"ts": 0.0, "error": "", "snippet": ""}


def probe_core_pane_error() -> dict[str, str]:
    """Best-effort read of sutando-core tmux for API/auth failures (cached ~8s)."""
    now = time.time()
    if now - float(_PANE_ERR_CACHE.get("ts") or 0) < 8.0:
        return {
            "error": str(_PANE_ERR_CACHE.get("error") or ""),
            "snippet": str(_PANE_ERR_CACHE.get("snippet") or ""),
        }
    error = ""
    snippet = ""
    try:
        out = subprocess.check_output(
            [
                "tmux",
                "-S",
                TMUX_SOCKET,
                "capture-pane",
                "-t",
                TMUX_SESSION,
                "-p",
                "-S",
                "-30",
            ],
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
        low = out.lower()
        if "403" in out and ("key" in low or "limit" in low or "openrouter" in low):
            error = "openrouter_403"
        elif "api error" in low or "please run /login" in low:
            error = "api_error"
        elif "rate limit" in low or "429" in out:
            error = "rate_limit"
        # Prefer error-bearing lines for HUD; else short tail (no secrets).
        lines = [
            ln.strip()
            for ln in out.splitlines()
            if ln.strip()
            and "sk-" not in ln
            and "Bearer" not in ln
            and not ln.strip().startswith("─")
            and "bypass permissions" not in ln.lower()
        ]
        err_lines = [
            ln
            for ln in lines
            if any(
                t in ln.lower()
                for t in ("403", "api error", "rate limit", "openrouter", "please run /login", "429")
            )
        ]
        pick = err_lines[-2:] if err_lines else lines[-3:]
        snippet = " | ".join(pick)[:220]
    except Exception:
        pass
    _PANE_ERR_CACHE["ts"] = now
    _PANE_ERR_CACHE["error"] = error
    _PANE_ERR_CACHE["snippet"] = snippet
    return {"error": error, "snippet": snippet}


def probe_feeder_hint(task_id: str) -> str:
    """Last feeder-log line mentioning this task (inject / re-nudge / abandon / done)."""
    base = task_id if task_id.endswith(".txt") else f"{task_id}.txt"
    log = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Sutando"
        / "omni-exp-feeder"
        / "omni-exp-watch-tasks-to-tmux.log"
    )
    try:
        # If already visible in the core pane, prefer that over "inbox_pending"
        # (HOL orphans can leave newer tasks sitting in inbox after a parallel inject).
        try:
            pane = subprocess.check_output(
                [
                    "tmux",
                    "-S",
                    TMUX_SOCKET,
                    "capture-pane",
                    "-t",
                    TMUX_SESSION,
                    "-p",
                    "-S",
                    "-40",
                ],
                text=True,
                timeout=2,
                stderr=subprocess.DEVNULL,
            )
            if base in pane or base.replace(".txt", "") in pane:
                return f"in_tmux_pane ({base})"
        except (OSError, subprocess.SubprocessError):
            pass
        if not log.is_file():
            return "feeder_log_missing"
        # Read last ~8KB only.
        data = log.read_bytes()[-8192:].decode("utf-8", errors="replace")
        hits = [ln.strip() for ln in data.splitlines() if base in ln]
        if not hits:
            inbox = (
                Path.home()
                / "Library"
                / "Application Support"
                / "Sutando"
                / "omni-exp-feeder"
                / "inbox"
                / base
            )
            return "inbox_pending" if inbox.is_file() else "no_feeder_line"
        return hits[-1][:160]
    except Exception as e:
        return f"feeder_read_err:{e}"


def sweep_orphan_feeder_inbox() -> int:
    """Drop inbox notifies whose task files are gone (user shell can read Documents)."""
    inbox = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Sutando"
        / "omni-exp-feeder"
        / "inbox"
    )
    if not inbox.is_dir():
        return 0
    n = 0
    try:
        for p in inbox.glob("task-*.txt"):
            if not (TASKS_DIR / p.name).is_file():
                mark_feeder_done(p.stem)
                n += 1
    except OSError:
        pass
    return n


# How long to treat an identical work() request as already done (anti re-open loop).
WORK_DEDUPE_S = float(_env_omni_exp("WORK_DEDUPE_S", "180"))
# Reclaim result files younger than this after omni-exp restart.
WORK_RECLAIM_MAX_AGE_S = float(_env_omni_exp("WORK_RECLAIM_MAX_AGE_S", "1800"))
OMNI_EXP_SUPPORT = (
    Path.home() / "Library" / "Application Support" / "Sutando" / "omni-exp"
)
RECENT_DONE_PATH = OMNI_EXP_SUPPORT / "recent-done.json"


def load_recent_done() -> dict[str, tuple[float, str]]:
    """Persist dedupe across omni-exp restarts (stops Baidu re-open loops)."""
    try:
        if not RECENT_DONE_PATH.is_file():
            return {}
        raw = json.loads(RECENT_DONE_PATH.read_text(encoding="utf-8"))
        out: dict[str, tuple[float, str]] = {}
        if not isinstance(raw, dict):
            return {}
        now = time.time()
        for k, v in raw.items():
            if not isinstance(v, (list, tuple)) or len(v) < 2:
                continue
            ts = float(v[0])
            tid = str(v[1])
            if now - ts <= WORK_DEDUPE_S * 2:
                out[str(k)] = (ts, tid)
        return out
    except Exception:
        return {}


def save_recent_done(done: dict[str, tuple[float, str]]) -> None:
    try:
        OMNI_EXP_SUPPORT.mkdir(parents=True, exist_ok=True)
        payload = {k: [v[0], v[1]] for k, v in done.items()}
        RECENT_DONE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        logger.warning("recent-done save failed: %s", e)


def cleanup_completed_task_pairs() -> int:
    """Remove task+result pairs that finished while no phone session was attached."""
    n = 0
    try:
        results = list(RESULTS_DIR.glob("task-*.txt"))
    except OSError:
        return 0
    archive = TASKS_DIR / "archive"
    try:
        archive.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    for result in results:
        task_id = result.stem
        task_path = TASKS_DIR / f"{task_id}.txt"
        if not task_path.is_file():
            # Result alone — still mark feeder done and drop stale result after reclaim window.
            try:
                age = time.time() - result.stat().st_mtime
            except OSError:
                continue
            if age < 30:
                continue  # let an attached session reclaim first
            mark_feeder_done(task_id)
            try:
                result.unlink(missing_ok=True)
                n += 1
            except OSError:
                pass
            continue
        # Both exist → completed; archive task, drop result, mark feeder.
        body = task_body_from_file(task_path)
        key = normalize_work_task(body)
        try:
            # Refresh persistent dedupe so reconnect won't re-open.
            done = load_recent_done()
            if key:
                done[key] = (time.time(), task_id)
                save_recent_done(done)
            dest = archive / f"{task_id}.txt"
            task_path.replace(dest)
            result.unlink(missing_ok=True)
            mark_feeder_done(task_id)
            n += 1
            logger.info("CLEANUP completed pair %s", task_id)
        except OSError as e:
            logger.warning("cleanup %s failed: %s", task_id, e)
    return n


def normalize_work_task(task: str) -> str:
    """Collapse wording so '打开百度.com' / 'Open baidu.com' dedupe together."""
    t = " ".join((task or "").strip().lower().split())
    t = t.replace("百度.com", "baidu.com").replace("百度", "baidu.com")
    t = t.replace("https://", "").replace("http://", "").replace("www.", "")
    openish = any(
        w in t
        for w in (
            "open",
            "打开",
            "chrome",
            "safari",
            "browser",
            "浏览器",
            "firefox",
        )
    )
    # No leading \b — CJK+ascii ("打开baidu.com") has no word boundary.
    domain = re.search(r"([a-z0-9.-]+\.(?:com|cn|net|org|ai|io))", t)
    if domain and openish:
        return f"open:{domain.group(1)}"
    for a, b in (
        ("谷歌浏览器", "chrome"),
        ("chrome浏览器", "chrome"),
        ("google chrome", "chrome"),
        ("打开", "open "),
        ("浏览器中", " "),
        ("浏览器", " "),
    ):
        t = t.replace(a, b)
    return " ".join(t.split())


def task_body_from_file(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("task:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def pipeline_debug(task_id: str, phase: str) -> dict[str, Any]:
    """Compact pipeline probe for HUD — answers 'is core actually doing work?'."""
    tid = task_id if task_id.startswith("task-") else f"task-{task_id}"
    task_path = TASKS_DIR / f"{tid}.txt"
    archive_path = TASKS_DIR / "archive" / f"{tid}.txt"
    result_path = RESULTS_DIR / f"{tid}.txt"
    core = probe_core_status()
    feeder = probe_feeder_hint(tid)
    pane_err = str(core.get("pane_error") or "")
    on_disk = task_path.is_file()
    archived = archive_path.is_file()
    has_result = result_path.is_file()

    if pane_err == "openrouter_403":
        verdict = "BLOCKED: OpenRouter 403 (key/quota) — core is NOT executing tools"
    elif pane_err == "api_error":
        verdict = "BLOCKED: core API error (check /login or provider key)"
    elif pane_err == "rate_limit":
        verdict = "BLOCKED: rate limit — core stalling on API"
    elif not core.get("alive"):
        verdict = "BLOCKED: sutando-core DOWN — task sits on disk"
    elif core.get("booting") or (core.get("alive") and not core.get("ready")):
        verdict = (
            "BLOCKED: sutando-core BOOTING (/startup) — not ready for work yet "
            f"· reason={core.get('ready_reason') or 'booting'}"
        )
    elif phase == "task_written" and on_disk and "inject" not in feeder.lower() and "nudge" not in feeder.lower():
        verdict = "WAITING: task on disk; feeder has not injected into tmux yet"
    elif phase == "task_written" and on_disk:
        verdict = "WAITING: injected/nudged but core has not claimed the task file"
    elif phase == "cc_processing":
        verdict = "RUNNING?: task claimed (file gone/archived) — waiting for results/"
    else:
        verdict = (
            f"phase={phase} · core="
            f"{'ready' if core.get('ready') else ('up' if core.get('alive') else 'DOWN')}"
        )

    line = (
        f"{verdict} · feeder={feeder[:80]} · "
        f"disk={'yes' if on_disk else 'no'} archive={'yes' if archived else 'no'} "
        f"result={'yes' if has_result else 'no'}"
    )
    if core.get("pane_snippet") and pane_err:
        line += f" · pane: {str(core.get('pane_snippet'))[:100]}"
    return {
        "verdict": verdict,
        "line": line[:320],
        "phase": phase,
        "core_alive": bool(core.get("alive")),
        "core_ready": bool(core.get("ready")),
        "pane_error": pane_err,
        "pane_snippet": str(core.get("pane_snippet") or "")[:160],
        "feeder": feeder[:160],
        "task_on_disk": on_disk,
        "archived": archived,
        "has_result": has_result,
        "pending_tasks": core.get("pending_tasks"),
        "core_step": core.get("step") or "",
    }


def probe_core_status() -> dict[str, Any]:
    """Heartbeat (.alive) + readiness (not mid-/startup) + core-status.json."""
    pane = probe_core_pane_error()
    # Capture a short pane text for boot-marker detection (best-effort).
    pane_text = ""
    try:
        pane_text = subprocess.check_output(
            [
                "tmux",
                "-S",
                TMUX_SOCKET,
                "capture-pane",
                "-t",
                TMUX_SESSION,
                "-p",
                "-S",
                "-30",
            ],
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        pane_text = str(pane.get("snippet") or "")

    readiness = probe_core_readiness(
        WORKSPACE,
        host=HOST_LABEL,
        alive_max_age_s=CORE_ALIVE_MAX_AGE_S,
        pane_text=pane_text,
    )
    alive = bool(readiness.get("alive"))
    age_s = readiness.get("age_s")
    payload: dict[str, Any] = {}
    alive_path = WORKSPACE / "state" / "cores" / f"{HOST_LABEL}.alive"
    if alive_path.exists():
        try:
            raw = json.loads(alive_path.read_text())
            if isinstance(raw, dict):
                payload = raw
        except Exception:
            pass
    step = None
    core_status = None
    status_path = WORKSPACE / "state" / "core-status.json"
    if status_path.exists():
        try:
            st = json.loads(status_path.read_text())
            if isinstance(st, dict):
                core_status = st.get("status")
                step = st.get("step")
        except Exception:
            pass
    pending = 0
    try:
        pending = sum(
            1
            for p in TASKS_DIR.glob("task-*.txt")
            if not (RESULTS_DIR / f"{p.stem}.txt").exists()
        )
    except Exception:
        pass
    return {
        "alive": alive,
        "ready": bool(readiness.get("ready")),
        "booting": bool(readiness.get("booting")),
        "ready_reason": readiness.get("reason") or "",
        "watcher_alive": bool(readiness.get("watcher_alive")),
        "age_s": age_s,
        "host": HOST_LABEL,
        "status": core_status or payload.get("status"),
        "step": step,
        "pending_tasks": pending,
        "can_start": ALLOW_START_CORE and START_CLI.is_file(),
        "can_stop": ALLOW_START_CORE,
        "how": f"bash {START_CLI.relative_to(REPO)}" if START_CLI.is_file() else "bash src/startup.sh",
        "pane_error": pane.get("error") or "",
        "pane_snippet": pane.get("snippet") or "",
    }


def _ensure_core_heartbeat() -> None:
    """start-cli alone does not refresh .alive — startup.sh normally starts this."""
    try:
        found = subprocess.run(
            ["pgrep", "-f", "src/core_heartbeat.py"],
            capture_output=True,
            text=True,
            check=False,
        )
        if found.returncode == 0 and found.stdout.strip():
            return
    except Exception:
        pass
    if not HEARTBEAT_PY.is_file():
        return
    log_path = LOGS_DIR / "core-heartbeat.log"
    with open(log_path, "a", encoding="utf-8") as logf:
        subprocess.Popen(
            [sys.executable, str(HEARTBEAT_PY)],
            cwd=str(REPO),
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    logger.info("Started core_heartbeat.py")


def start_sutando_core() -> dict[str, Any]:
    """Detach-launch canonical core (start-cli.sh) + heartbeat. No-op if already alive."""
    st = probe_core_status()
    if st["alive"]:
        _ensure_core_heartbeat()
        return {"ok": True, "started": False, "message": "core already alive", **probe_core_status()}
    if not ALLOW_START_CORE:
        return {
            "ok": False,
            "started": False,
            "message": "OMNI_EXP_ALLOW_START_CORE=0 — start manually: " + st["how"],
            **st,
        }
    if not START_CLI.is_file():
        return {"ok": False, "started": False, "message": "start-cli.sh missing", **st}
    log_path = LOGS_DIR / "omni-start-core.log"
    try:
        # Prefer --force-restart when a stale tmux session may be blocking a fresh start.
        args = ["bash", str(START_CLI), "--force-restart"]
        with open(log_path, "a", encoding="utf-8") as logf:
            logf.write(f"\n--- start {datetime.now(timezone.utc).isoformat()} ---\n")
            logf.flush()
            # Skip /startup so omni work is not blocked behind schedule-crons.
            env = {
                **os.environ,
                "SUTANDO_CORE_SESSION": "1",
                "SUTANDO_SKIP_STARTUP": "1",
            }
            proc = subprocess.Popen(
                args,
                cwd=str(REPO),
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
        _ensure_core_heartbeat()
        # Give heartbeat a moment so the next probe can flip to UP.
        time.sleep(1.5)
        logger.info("Started sutando-core via %s pid=%s log=%s", args, proc.pid, log_path)
        st2 = probe_core_status()
        return {
            "ok": True,
            "started": True,
            "pid": proc.pid,
            "log": str(log_path),
            "message": (
                f"start-cli launched (pid {proc.pid}); "
                + ("core UP" if st2["alive"] else f"wait for .alive < {int(CORE_ALIVE_MAX_AGE_S)}s")
            ),
            **st2,
        }
    except Exception as e:
        logger.exception("start core failed: %s", e)
        return {"ok": False, "started": False, "message": str(e), **st}


def stop_sutando_core() -> dict[str, Any]:
    """Stop the canonical tmux sutando-core session."""
    if not ALLOW_START_CORE:
        return {
            "ok": False,
            "stopped": False,
            "message": "OMNI_EXP_ALLOW_START_CORE=0 — stop blocked",
            **probe_core_status(),
        }
    try:
        r = subprocess.run(
            ["tmux", "-S", TMUX_SOCKET, "kill-session", "-t", TMUX_SESSION],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        # Also drop watcher session if present (codex path).
        subprocess.run(
            ["tmux", "-S", TMUX_SOCKET, "kill-session", "-t", f"{TMUX_SESSION}-watcher"],
            capture_output=True,
            check=False,
            timeout=5,
        )
        time.sleep(0.5)
        st = probe_core_status()
        ok = r.returncode == 0 or not st["alive"]
        msg = (
            "sutando-core stopped"
            if ok
            else f"kill-session rc={r.returncode}: {(r.stderr or r.stdout or '').strip()}"
        )
        logger.info("stop_sutando_core: %s", msg)
        return {"ok": ok, "stopped": ok, "message": msg, **st}
    except Exception as e:
        logger.exception("stop core failed: %s", e)
        return {"ok": False, "stopped": False, "message": str(e), **probe_core_status()}

# Operating mode: normal_with_gui | no_gui | no_gui_html_output | research.
OMNI_EXP_MODE = normalize_omni_exp_mode(_env_omni_exp("MODE", "research"))
# Research capture loop needs scene_change; force on (override OMNI_EXP_SCENE_CHANGE=0).
if OMNI_EXP_MODE == "research":
    SCENE_CHANGE_ENABLED = True
# Optional full prompt override (OMNI_EXP_INSTRUCTIONS) wins over mode defaults.
_INSTRUCTIONS_OVERRIDE = _env_omni_exp("INSTRUCTIONS", "")
INSTRUCTIONS = build_omni_exp_instructions(
    OMNI_EXP_MODE,
    override=_INSTRUCTIONS_OVERRIDE or None,
)
SCENE_PROMPT = scene_prompt_for_mode(OMNI_EXP_MODE)

# Tool name + description aligned with voice (task-bridge.ts workTool) and
# LiveKit (livekit-agent.py work) — same contract: name=work, param=task.
# Description follows OMNI_EXP_MODE (no_gui drops browser/app wording).
WORK_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "work",
    "description": work_tool_description(OMNI_EXP_MODE),
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
        self.scene = SceneChangeSensor(
            enter_threshold=SCENE_ENTER_THRESHOLD,
            upload_dedupe_threshold=UPLOAD_DEDUPE_THRESHOLD,
            upload_keepalive_s=UPLOAD_KEEPALIVE_S,
        )
        # Latest JPEG from the client (even if upload was skipped) for speech keyframes.
        self._last_jpeg: bytes | None = None
        self.qwen: QwenOmniSession | None = None
        # task_id -> {started, task, last_hb, source, call_id?}
        self._pending_work: dict[str, dict[str, Any]] = {}
        # normalize_work_task(task) -> (done_at_epoch, task_id) — blocks re-open loops
        self._recent_done: dict[str, tuple[float, str]] = load_recent_done()
        self._closed = False
        self._assistant_text = ""
        self._audio_buf: list[str] = []
        self._tools_this_response = 0
        self._handled_call_ids: set[str] = set()
        # Monotonic model-response id for Activity logs (t1, t2, …).
        # Qwen often does tool call on tN then a speech-only ack on tN+1.
        self._turn_seq = 0
        self._turn_id = "-"
        # Which PromptTrigger started the in-flight model response (if any).
        self._pending_prompt_reason: str | None = None
        self._scene_nospeak_streak = 0
        self._last_fake_done_nudge_at = 0.0
        self._last_stale_wait_nudge_at = 0.0
        # When set (epoch), success-language with tools=0 is trusted (already_done).
        self._trust_done_claim_until = 0.0
        # task_ids whose work_result speak actually started (or HTML fallback fired).
        self._spoken_task_ids: set[str] = set()
        self._deliver_retry_tasks: set[asyncio.Task[Any]] = set()
        # Buffer PromptTriggers (work results / nudges) until response.done.
        self.speak_queue = SpeakQueue(merge=WORK_RESULT_MERGE, max_items=SPEAK_QUEUE_MAX)
        self._drain_lock = asyncio.Lock()

    def _begin_turn(self, data: dict[str, Any] | None = None) -> str:
        self._turn_seq += 1
        resp = (data or {}).get("response") if isinstance((data or {}).get("response"), dict) else {}
        provider_id = str((resp or {}).get("id") or (data or {}).get("response_id") or "")
        short = provider_id[-8:] if len(provider_id) >= 8 else provider_id
        self._turn_id = f"t{self._turn_seq}" + (f"/{short}" if short else "")
        return self._turn_id

    async def _try_start_prompt(self, reason: str, text: str) -> tuple[bool, str]:
        """Start a PromptTrigger, reserving the turn *before* await (avoids race)."""
        if not self.qwen:
            return False, "no_qwen"
        req = TurnRequest(kind="prompt", reason=reason, prompt_text=text)
        ok, why = self.gate.allow(req)
        if not ok:
            return False, why
        self.gate.mark_fired(req)
        # Reserve immediately — response.created arrives later; without this,
        # a second prompt_turn in the same poll loop races past allow().
        self.gate.begin_response()
        self._pending_prompt_reason = reason
        try:
            await self.qwen.prompt_turn(text)
        except Exception:
            self.gate.end_response()
            self._pending_prompt_reason = None
            raise
        return True, "ok"

    async def enqueue_speak(self, item: SpeakItem, *, with_retry: bool = False) -> None:
        self.speak_queue.push(item)
        kind = ""
        if isinstance(item.meta, dict):
            kind = str(item.meta.get("kind") or "")
        # Fake-done nudges are model-correction prompts, not user tasks — Activity
        # only. Emitting work_event without task_id used to spawn spam cards.
        if kind not in ("fake_done_nudge", "stale_wait_nudge"):
            await self.work_event(
                "speak_queued",
                task_id=item.task_id,
                queue_len=len(self.speak_queue),
                merge=self.speak_queue.merge,
                preview=(item.preview or "")[:80],
            )
        await self.activity(
            "work",
            (
                f"Fake-done nudge buffered (q={len(self.speak_queue)})"
                if kind == "fake_done_nudge"
                else f"Stale-wait nudge buffered (q={len(self.speak_queue)})"
                if kind == "stale_wait_nudge"
                else f"Speak buffered ({self.speak_queue.merge}) queue={len(self.speak_queue)}"
                + (f" · {item.task_id}" if item.task_id else "")
            ),
        )
        await self.drain_speak_queue()
        # Mirror inject-delivery.ts: re-check after 1.5s / 3s, then HTML fallback.
        if with_retry and item.reason == "work_result" and item.task_id:
            task = asyncio.create_task(
                self._deliver_work_result_with_retry(item),
                name=f"omni-deliver-{item.task_id}",
            )
            self._deliver_retry_tasks.add(task)
            task.add_done_callback(self._deliver_retry_tasks.discard)

    async def drain_speak_queue(self) -> None:
        """Start at most one buffered prompt when the session is idle."""
        async with self._drain_lock:
            if self.gate.responding or self.gate.voice_active:
                return
            if not self.speak_queue:
                return
            item = self.speak_queue.take()
            if not item:
                return
            left = len(self.speak_queue)
            await self.activity(
                "work",
                f"Speaking buffered turn…"
                + (f" ({left} still queued)" if left else "")
                + (f" · {item.task_id}" if item.task_id else ""),
            )
            try:
                ok, why = await self._try_start_prompt(item.reason, item.prompt_text)
            except Exception as e:
                await self.activity("error", f"Speak drain failed: {e}")
                self.speak_queue.push_front(item)
                return
            if not ok:
                self.speak_queue.push_front(item)
                await self.activity("work", f"Speak drain deferred: {why}")
                return
            if item.task_id:
                self._spoken_task_ids.add(item.task_id)
            await self.work_event(
                "speak_started",
                task_id=item.task_id,
                queue_len=left,
                merge=self.speak_queue.merge,
                preview=(item.preview or "")[:80],
            )

    async def _deliver_work_result_with_retry(self, item: SpeakItem) -> None:
        """Retry speak drain like voice inject-delivery; HTML TTS if still silent."""
        tid = item.task_id or ""
        for delay in DELIVER_RETRY_DELAYS_S:
            await asyncio.sleep(delay)
            if self._closed:
                return
            if tid and tid in self._spoken_task_ids:
                return
            if tid and not self.speak_queue.contains_task(tid):
                # Dropped / never re-queued — put it back and try again.
                self.speak_queue.push_front(item)
            await self.drain_speak_queue()
            if tid and tid in self._spoken_task_ids:
                return
        if self._closed:
            return
        if tid and tid in self._spoken_task_ids:
            return
        await self._html_result_fallback(item)

    async def _html_result_fallback(self, item: SpeakItem) -> None:
        """When Qwen can't speak the result, announce on the HTML client (browser TTS).

        Better than silence for the phone page — Discord is intentionally unused here.
        """
        tid = item.task_id or ""
        body = extract_task_result_body(item.prompt_text) or (item.preview or "Task completed.")
        body = body[:500].strip()
        if tid:
            self._spoken_task_ids.add(tid)
            self.speak_queue.remove_task(tid)
        await self.send(
            {
                "type": "result.announce",
                "task_id": tid or None,
                "text": body,
                "tts": True,
                "reason": "speak_retry_exhausted",
            }
        )
        await self.activity(
            "work",
            f"HTML result fallback (browser TTS)"
            + (f" · {tid}" if tid else "")
            + f" — {body[:80]}",
            task_id=tid or None,
        )
        await self.work_event(
            "result_announce",
            task_id=tid or None,
            preview=body[:120],
            tts=True,
        )

    def _turn_tag(self) -> str:
        return f"[{self._turn_id}]"

    async def send(self, payload: dict[str, Any]) -> None:
        if self.ws.closed or self._closed:
            return
        await self.ws.send_json(payload)

    async def status(self, state: str) -> None:
        await self.send({"type": "status", "state": state})

    async def activity(self, kind: str, message: str, **extra: Any) -> None:
        await self.send(
            {
                "type": "activity",
                "kind": kind,
                "message": message,
                "turn_id": self._turn_id,
                **extra,
            }
        )

    async def work_event(self, state: str, **extra: Any) -> None:
        # Always include turn_id for HUD task-card metadata (unless caller overrides).
        payload = {"type": "work", "state": state, "turn_id": self._turn_id, **extra}
        await self.send(payload)

    async def push_core_status(self, st: dict[str, Any] | None = None) -> None:
        payload = st or probe_core_status()
        await self.send({"type": "core", **payload})

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
        await self.activity(
            "session",
            f"Qwen ready ({self.qwen.model}) · tool: work · mode={OMNI_EXP_MODE}",
        )
        await self.activity("session", f"Tasks dir: {TASKS_DIR}")
        if OMNI_EXP_MODE == "no_gui":
            await self.activity(
                "session",
                "NO GUI mode — work tasks forbid browser/app/GUI automation",
            )
        elif OMNI_EXP_MODE == "no_gui_html_output":
            await self.activity(
                "session",
                "NO GUI + HTML mode — research without browser search; "
                "write local HTML and open it",
            )
        elif OMNI_EXP_MODE == "research":
            await self.activity(
                "session",
                "RESEARCH mode — capture scene/audio topics; deep research → "
                "MD then auto-play HTML deck (see docs/omni-exp-research-mode.md)",
            )
        core = probe_core_status()
        await self.push_core_status(core)
        if core.get("ready"):
            await self.activity(
                "session",
                f"Sutando-core READY · age={core.get('age_s')}s · pending={core.get('pending_tasks')}",
            )
        elif core["alive"]:
            await self.activity(
                "session",
                f"Sutando-core UP but not ready ({core.get('ready_reason')}) · "
                f"age={core.get('age_s')}s · pending={core.get('pending_tasks')}",
            )
        else:
            await self.activity(
                "error",
                "Sutando-core DOWN — work() will queue but not finish. "
                f"Start: {core.get('how')} (or tap Start core)",
            )
        # After restart, results may already exist while _pending_work is empty —
        # reclaim so HUD/speak catch up instead of Qwen re-calling work().
        n = self.adopt_stranded_results()
        if n:
            await self.activity("work", f"Reclaimed {n} stranded result(s) from disk")

    async def _on_qwen_event(self, data: dict[str, Any]) -> None:
        et = data.get("type", "")
        if et == "input_audio_buffer.speech_started":
            self.gate.voice_active = True
            await self.status("user_speaking")
            await self.send({"type": "vad", "state": "speech_started"})
            await self.activity("vad", "VAD: speech started")
            # Ensure Qwen has a fresh frame in the buffer for this voice turn
            # (main /omni works because lower dedupe kept frames flowing; we force).
            await self._force_vision_keyframe("speech_started")
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
            turn = self._begin_turn(data)
            await self.status("responding")
            await self.send(
                {"type": "stream", "state": "started", "turn_id": turn}
            )
            # Scene probes are frequent; log streaming only for user-facing turns.
            if self._pending_prompt_reason != "scene_change":
                await self.activity("stream", f"{self._turn_tag()} Response streaming…")
        elif et == "response.done":
            self.gate.end_response()
            text = self._assistant_text.strip()
            prompt_reason = self._pending_prompt_reason
            self._pending_prompt_reason = None
            suppress = "[[NO_SPEAK]]" in text
            trust_done = time.time() < float(self._trust_done_claim_until or 0)
            fake_done = is_fake_done_claim(
                text,
                tools_this_response=self._tools_this_response,
                prompt_reason=prompt_reason,
                trust_done_claim=trust_done,
            )
            stale_wait = is_stale_wait_claim(
                text,
                tools_this_response=self._tools_this_response,
                pending_work_count=len(self._pending_work),
                prompt_reason=prompt_reason,
            )
            if fake_done:
                # Don't play audio that claims a PC action that never ran.
                suppress = True
            elif stale_wait:
                # Result already delivered; don't keep saying "还在等…".
                suppress = True
            elif trust_done and prompt_reason != "work_result":
                # Consumed the already_done trust window on this speak.
                self._trust_done_claim_until = 0.0
            scene_quiet = (
                prompt_reason == "scene_change"
                and suppress
                and not fake_done
                and not stale_wait
            )
            await self.send(
                {
                    "type": "stream",
                    "state": "done",
                    "audio_chunks": len(self._audio_buf),
                    "suppressed": suppress,
                    "tools_called": self._tools_this_response,
                    "fake_done": fake_done,
                    "stale_wait": stale_wait,
                    "turn_id": self._turn_id,
                    "prompt_reason": prompt_reason,
                }
            )
            if text and not scene_quiet:
                await self.send(
                    {
                        "type": "transcript",
                        "role": "assistant",
                        "text": (
                            "(blocked: claimed action without work)"
                            if fake_done
                            else "(blocked: stale wait — nothing pending)"
                            if stale_wait
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
            # After work() returns, Qwen often starts a NEW response.created that
            # only speaks ("Working on it…") with tools=0. That is normal — do not
            # log it as "no tool delegated" when a task is already pending.
            if self._tools_this_response == 0 and text and not suppress:
                if self._pending_work:
                    await self.activity(
                        "work",
                        f"{self._turn_tag()} Spoke only (OK — work already queued on an earlier turn)",
                    )
                else:
                    await self.activity(
                        "work",
                        f"{self._turn_tag()} No work/tool delegated (model spoke only)",
                    )
            if fake_done:
                self._scene_nospeak_streak = 0
                pending = len(self._pending_work)
                await self.activity(
                    "work",
                    f"{self._turn_tag()} Blocked fake claim — spoke success without calling work"
                    + (f" ({pending} task(s) still pending)" if pending else ""),
                )
                now_nudge = time.time()
                can_nudge = (
                    self.qwen is not None
                    and (now_nudge - self._last_fake_done_nudge_at) >= FAKE_DONE_NUDGE_COOLDOWN_S
                )
                if can_nudge:
                    self._last_fake_done_nudge_at = now_nudge
                    wait_line = (
                        "A prior work call is still pending — say you are still waiting once, "
                        "then stay quiet until TASK_RESULT."
                        if pending
                        else "Nothing is pending — call work with a concrete task if the user "
                        "still wants the action, or answer their latest request."
                    )
                    await self.enqueue_speak(
                        SpeakItem(
                            reason="work_result",
                            prompt_text=(
                                "[System] You just claimed a PC action finished but did NOT "
                                "call the work tool in that turn — nothing ran. "
                                f"{wait_line} "
                                "Do not claim success again without calling work."
                            ),
                            preview="fake-done nudge",
                            meta={"kind": "fake_done_nudge"},
                        )
                    )
                elif self.qwen:
                    await self.activity(
                        "work",
                        f"{self._turn_tag()} Fake-done nudge skipped (cooldown "
                        f"{FAKE_DONE_NUDGE_COOLDOWN_S:.0f}s)",
                    )
            elif stale_wait:
                self._scene_nospeak_streak = 0
                await self.activity(
                    "work",
                    f"{self._turn_tag()} Blocked stale wait — nothing pending",
                )
                now_nudge = time.time()
                can_nudge = (
                    self.qwen is not None
                    and (now_nudge - self._last_stale_wait_nudge_at)
                    >= STALE_WAIT_NUDGE_COOLDOWN_S
                )
                if can_nudge:
                    self._last_stale_wait_nudge_at = now_nudge
                    await self.enqueue_speak(
                        SpeakItem(
                            reason="work_result",
                            prompt_text=(
                                "[System] Nothing is pending — prior work already finished. "
                                "Do NOT say you are still waiting. Respond to the user's latest "
                                "request: answer directly if it's camera/simple, otherwise call "
                                "work with a concrete new task."
                            ),
                            preview="stale-wait nudge",
                            meta={"kind": "stale_wait_nudge"},
                        )
                    )
                elif self.qwen:
                    await self.activity(
                        "work",
                        f"{self._turn_tag()} Stale-wait nudge skipped (cooldown "
                        f"{STALE_WAIT_NUDGE_COOLDOWN_S:.0f}s)",
                    )
            elif prompt_reason == "scene_change":
                if suppress:
                    self._scene_nospeak_streak += 1
                    # Stretch cooldown after repeated "nothing new" scene probes.
                    if self._scene_nospeak_streak >= SCENE_NOSPEAK_BACKOFF_AFTER:
                        stretched = int(
                            SCENE_COOLDOWN_MS
                            * SCENE_NOSPEAK_BACKOFF_MULT
                            * min(self._scene_nospeak_streak - SCENE_NOSPEAK_BACKOFF_AFTER + 1, 4)
                        )
                        self.gate.cooldowns_ms["scene_change"] = stretched
                    await self.activity(
                        "trigger",
                        f"{self._turn_tag()} scene_change → nothing new"
                        + (
                            f" (backoff {self.gate.cooldowns_ms['scene_change']}ms)"
                            if self._scene_nospeak_streak >= SCENE_NOSPEAK_BACKOFF_AFTER
                            else ""
                        ),
                    )
                else:
                    self._scene_nospeak_streak = 0
                    self.gate.cooldowns_ms["scene_change"] = SCENE_COOLDOWN_MS
            else:
                self._scene_nospeak_streak = 0
                self.gate.cooldowns_ms["scene_change"] = SCENE_COOLDOWN_MS
            await self.status("listening" if not self._pending_work else "working")
            logger.info(
                "response.done turn=%s tools=%s fake_done=%s stale_wait=%s scene_quiet=%s chars=%s text=%s",
                self._turn_id,
                self._tools_this_response,
                fake_done,
                stale_wait,
                scene_quiet,
                len(text),
                text[:120],
            )
            if not scene_quiet:
                await self.activity(
                    "stream",
                    f"{self._turn_tag()} Response done"
                    + (" (suppressed)" if suppress else f" · {len(text)} chars")
                    + f" · tools={self._tools_this_response}",
                )
            # Design drain: after response.done, start next buffered PromptTrigger.
            try:
                await self.drain_speak_queue()
            except Exception as e:
                await self.activity("error", f"speak drain: {e}")
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
            "TOOL_CALL turn=%s tool=%s parameters=%s call_id=%s",
            self._turn_id,
            name,
            params_exact,
            call_id,
        )
        await self.activity(
            "work",
            f"{self._turn_tag()} Tool call: {name}({params_exact})",
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

        task_id, status, note = await self.enqueue_work(task, source="tool", call_id=call_id)
        if self.qwen:
            await self.qwen.send_function_output(
                call_id,
                {
                    "ok": True,
                    "task_id": task_id,
                    "status": status,
                    "message": note,
                },
            )
        # Dedupe paths never emit task_written — close the provisional Tool card
        # and (for already_done) allow the model to confirm without fake-done mute.
        if status in ("already_done", "already_queued"):
            await self.work_event(
                status,
                task_id=task_id,
                call_id=call_id,
                source="tool",
                task=task[:160],
                preview=note[:120],
            )
            if status == "already_done":
                self._trust_done_claim_until = time.time() + TRUST_DONE_CLAIM_S
        logger.info(
            "TOOL_CALL turn=%s tool=%s → %s %s parameters=%s",
            self._turn_id,
            name,
            status,
            task_id,
            params_exact,
        )
        await self.activity(
            "work",
            f"{self._turn_tag()} Tool {name}({params_exact}) → {status} {task_id}",
        )

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
        self._last_jpeg = jpeg
        if self.scene.should_upload(jpeg):
            await self.qwen.append_image(jpeg)
            logger.debug("vision upload ok bytes=%s", len(jpeg))
        if SCENE_CHANGE_ENABLED and self.scene.observe(jpeg):
            await self._prompt_scene()

    async def _force_vision_keyframe(self, reason: str) -> None:
        """Push the latest camera JPEG into Qwen before a user/voice turn."""
        if not self.qwen:
            return
        jpeg = self._last_jpeg or self.scene.last_accepted_jpeg
        if not jpeg:
            await self.activity("trigger", f"Vision keyframe skipped ({reason}): no frame yet")
            return
        try:
            await self.qwen.append_image(jpeg)
            self.scene.note_upload(jpeg)
            await self.activity(
                "trigger",
                f"Vision keyframe → Qwen ({reason}) · {len(jpeg)}B",
            )
        except Exception as e:
            await self.activity("error", f"Vision keyframe failed ({reason}): {e}")

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
        await self.status("proactive")
        await self.send({"type": "trigger", "reason": "scene_change", "state": "fired"})
        # One line at fire time; [[NO_SPEAK]] outcome is collapsed on response.done.
        await self.activity("trigger", "Auto trigger: scene_change")
        ok, why = await self._try_start_prompt("scene_change", SCENE_PROMPT)
        if not ok:
            await self.activity("trigger", f"scene_change start failed: {why}")
            await self.status("listening" if not self._pending_work else "working")

    async def handle_manual_prompt(self, text: str) -> None:
        req = TurnRequest(kind="prompt", reason="manual", prompt_text=text)
        ok, why = self.gate.allow(req)
        if not ok:
            await self.send({"type": "error", "message": f"prompt blocked: {why}"})
            await self.activity("trigger", f"Manual prompt blocked: {why}")
            return
        assert self.qwen
        await self._force_vision_keyframe("ask_view")
        await self.send({"type": "trigger", "reason": "manual", "state": "fired"})
        await self.activity("trigger", "Manual: Ask view")
        ok, why = await self._try_start_prompt("manual", text)
        if not ok:
            await self.send({"type": "error", "message": f"prompt blocked: {why}"})
            await self.activity("trigger", f"Manual prompt start failed: {why}")

    def adopt_stranded_results(self) -> int:
        """Adopt result files left on disk after omni-exp restart into _pending_work."""
        n = 0
        now = time.time()
        try:
            results = list(RESULTS_DIR.glob("task-*.txt"))
        except OSError:
            return 0
        for result in results:
            task_id = result.stem
            if task_id in self._pending_work:
                continue
            try:
                age = max(0.0, now - result.stat().st_mtime)
            except OSError:
                continue
            if age > WORK_RECLAIM_MAX_AGE_S:
                continue
            task_path = TASKS_DIR / f"{task_id}.txt"
            body = task_body_from_file(task_path) if task_path.is_file() else ""
            self._pending_work[task_id] = {
                "started": now - age,
                "task": (body or task_id)[:240],
                "last_hb": 0.0,
                "last_debug_act": 0.0,
                "last_debug_sig": "",
                "source": "reclaim",
                "call_id": None,
                "phase": "result_file",
                "reclaimed": True,
            }
            n += 1
            logger.info("RECLAIM result task_id=%s age=%.1fs", task_id, age)
        return n

    async def enqueue_work(
        self, task: str, *, source: str = "manual", call_id: str | None = None
    ) -> tuple[str, str, str]:
        """Queue work for sutando-core.

        Returns (task_id, status, message_for_model).
        status: queued | already_queued | already_done
        """
        key = normalize_work_task(task)
        now = time.time()
        # "Wait for previous result" is not real work — never enqueue another task.
        if is_wait_meta_task(task):
            if self._pending_work:
                tid = next(iter(self._pending_work))
                note = (
                    f"Not queued — that was only 'wait for prior result'. "
                    f"{tid} is already in flight. Say you're waiting once, then stay "
                    "quiet until TASK_RESULT. Do NOT call work again just to wait."
                )
                await self.activity("work", f"DEDUPÉ wait_meta → {tid}: {task[:60]}")
                return tid, "already_queued", note
            # Prefer most recent completion if any.
            if self._recent_done:
                tid = max(self._recent_done.values(), key=lambda v: v[0])[1]
                note = (
                    f"Not queued — prior work already finished ({tid}). "
                    "Summarize that result if asked; do NOT say you are still waiting. "
                    "Call work only for a new concrete request."
                )
                await self.activity("work", f"DEDUPÉ wait_meta already_done → {tid}: {task[:60]}")
                return tid, "already_done", note
            note = (
                "Not queued — nothing is pending. Do not say you are waiting. "
                "Call work with a concrete new task if the user wants something done."
            )
            await self.activity("work", f"DEDUPÉ wait_meta idle: {task[:60]}")
            return "task-none", "already_done", note
        # Same work already in flight — do not open Baidu again.
        for tid, meta in self._pending_work.items():
            if normalize_work_task(str(meta.get("task") or "")) == key:
                note = (
                    f"Same task already in flight as {tid} — NOT queued again. "
                    "Tell the user you're still waiting once; do NOT claim a new open, "
                    "and do NOT call work again just to wait."
                )
                await self.activity("work", f"DEDUPÉ already_queued → {tid}: {task[:60]}")
                return tid, "already_queued", note
        # Same work finished recently — stop the re-open loop.
        prev = self._recent_done.get(key)
        if prev and now - prev[0] < WORK_DEDUPE_S:
            tid = prev[1]
            note = (
                f"Same task already completed recently ({tid}) — NOT queued again. "
                "Tell the user it's already done; do NOT open the site again."
            )
            await self.activity("work", f"DEDUPÉ already_done → {tid}: {task[:60]}")
            return tid, "already_done", note

        task_id = f"task-{int(time.time() * 1000)}"
        task_body = format_work_task(OMNI_EXP_MODE, task)
        content = (
            f"id: {task_id}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"task: {task_body}\n"
            f"source: omni\n"
            f"via: {source}\n"
            f"channel_id: omni-phone\n"
            f"username: {self.username}\n"
            f"access_tier: owner\n"
            f"priority: normal\n"
            f"omni_exp_mode: {OMNI_EXP_MODE}\n"
        )
        if call_id:
            content += f"call_id: {call_id}\n"
        content += task_system_suffix(OMNI_EXP_MODE)
        path = TASKS_DIR / f"{task_id}.txt"
        path.write_text(content)
        # TCC-safe notify for launchd feeder (cannot scan ~/Documents/tasks).
        try:
            inbox = (
                Path.home()
                / "Library"
                / "Application Support"
                / "Sutando"
                / "omni-exp-feeder"
                / "inbox"
            )
            inbox.mkdir(parents=True, exist_ok=True)
            (inbox / f"{task_id}.txt").write_text(task_id + "\n")
        except Exception as e:
            logger.warning("feeder inbox notify failed: %s", e)
        # Drop stale inbox orphans (no task file) so HOL can't block this notify.
        try:
            swept = sweep_orphan_feeder_inbox()
            if swept:
                logger.info("swept %s orphan feeder inbox entries", swept)
        except Exception as e:
            logger.warning("feeder inbox sweep failed: %s", e)
        ensure_tmux_task_feeder()
        self._pending_work[task_id] = {
            "started": now,
            "task": task[:240],
            "last_hb": now,
            "last_debug_act": 0.0,
            "last_debug_sig": "",
            "source": source,
            "call_id": call_id,
            "phase": "task_written",
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
        # Lifecycle: tool_called (earlier) → task_written → … → result_processed
        await self.work_event(
            "task_written",
            task_id=task_id,
            call_id=call_id,
            source=source,
            task=task[:160],
            elapsed_ms=0,
            path=str(path),
        )
        # Keep legacy "queued" for older clients / sticky wait rows.
        await self.work_event(
            "queued",
            task_id=task_id,
            call_id=call_id,
            source=source,
            task=task[:160],
            elapsed_ms=0,
        )
        await self.activity(
            "work",
            f"{self._turn_tag()} Task file written ({source}): {task_id} — {task[:80]}",
            task_id=task_id,
            elapsed_ms=0,
        )
        core = probe_core_status()
        await self.push_core_status(core)
        if not core["alive"]:
            await self.activity(
                "error",
                f"⚠ Core DOWN — {task_id} is queued on disk but nothing will run it. "
                f"Tap Start core or run: {core.get('how')}",
            )
        elif core.get("booting") or not core.get("ready"):
            await self.activity(
                "work",
                f"⚠ Core booting/not ready ({core.get('ready_reason')}) — "
                f"{task_id} waits until /startup finishes (or use skip-startup boot).",
                task_id=task_id,
            )
        elif core.get("pane_error"):
            await self.activity(
                "error",
                f"⚠ Core pane error ({core.get('pane_error')}): "
                f"{str(core.get('pane_snippet') or '')[:140]}",
            )
        dbg = pipeline_debug(task_id, "task_written")
        await self.activity("work", f"Pipeline: {dbg['line']}", task_id=task_id)
        note = (
            "Task queued for Sutando core — NOT finished yet. "
            "Tell the user you started it; do not claim success until a later result arrives."
        )
        return task_id, "queued", note

    async def poll_results_once(self) -> None:
        now = time.time()
        # Pick up results written while we were down / not tracking.
        self.adopt_stranded_results()
        for task_id in list(self._pending_work):
            meta = self._pending_work[task_id]
            started = float(meta.get("started") or now)
            elapsed = now - started
            elapsed_ms = int(elapsed * 1000)
            task_path = TASKS_DIR / f"{task_id}.txt"
            archive_path = TASKS_DIR / "archive" / f"{task_id}.txt"
            result = RESULTS_DIR / f"{task_id}.txt"
            task_snippet = str(meta.get("task") or "")[:80]
            call_id = meta.get("call_id")

            if not result.exists():
                if elapsed > WORK_TIMEOUT_S:
                    del self._pending_work[task_id]
                    mark_feeder_done(task_id)
                    await self.work_event(
                        "timeout",
                        task_id=task_id,
                        call_id=call_id,
                        elapsed_ms=elapsed_ms,
                        task=task_snippet,
                    )
                    await self.activity(
                        "work",
                        f"Task TIMEOUT after {_fmt_elapsed(elapsed)}: {task_id}",
                        task_id=task_id,
                        elapsed_ms=elapsed_ms,
                    )
                    if not self._pending_work:
                        await self.status("listening")
                    continue

                # Task file gone (or archived) but no result yet → core claimed it.
                claimed = (not task_path.exists()) or archive_path.exists()
                phase = str(meta.get("phase") or "task_written")
                if claimed and phase not in ("cc_processing", "result_file", "result_processed"):
                    meta["phase"] = "cc_processing"
                    phase = "cc_processing"
                    await self.work_event(
                        "cc_processing",
                        task_id=task_id,
                        call_id=call_id,
                        elapsed_ms=elapsed_ms,
                        task=task_snippet,
                    )
                    await self.activity(
                        "work",
                        f"CC processing {task_id} (task file claimed)",
                        task_id=task_id,
                        elapsed_ms=elapsed_ms,
                    )

                last_hb = float(meta.get("last_hb") or 0)
                if now - last_hb >= WORK_HEARTBEAT_S:
                    meta["last_hb"] = now
                    dbg = pipeline_debug(task_id, phase)
                    # Sticky chip / task-list timer + pipeline debug for HUD.
                    await self.work_event(
                        "processing",
                        task_id=task_id,
                        call_id=call_id,
                        elapsed_ms=elapsed_ms,
                        task=task_snippet,
                        phase=phase,
                        debug=dbg,
                        debug_line=dbg.get("line"),
                    )
                    # Activity: ~10s or when verdict/pane error changes (not every 2s).
                    sig = f"{dbg.get('verdict')}|{dbg.get('pane_error')}|{dbg.get('feeder')}"
                    last_act = float(meta.get("last_debug_act") or 0)
                    if sig != meta.get("last_debug_sig") or now - last_act >= 10.0:
                        meta["last_debug_act"] = now
                        meta["last_debug_sig"] = sig
                        kind = "error" if dbg.get("pane_error") or not dbg.get("core_alive") else "work"
                        await self.activity(
                            kind,
                            f"Pipeline [{_fmt_elapsed(elapsed)}]: {dbg.get('line')}",
                            task_id=task_id,
                            elapsed_ms=elapsed_ms,
                        )
                continue

            # Result file present: CC finished → omni consumes → speak.
            text = result.read_text().strip()
            meta["phase"] = "result_file"
            await self.work_event(
                "result_file",
                task_id=task_id,
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                preview=text[:120],
                task=task_snippet,
            )
            # "cc_done" = core wrote the result (same moment we see the file).
            await self.work_event(
                "cc_done",
                task_id=task_id,
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                preview=text[:120],
                task=task_snippet,
            )
            del self._pending_work[task_id]
            mark_feeder_done(task_id)
            # Remember completion so identical work() calls don't re-open Baidu.
            done_key = normalize_work_task(task_snippet or task_id)
            if done_key:
                self._recent_done[done_key] = (now, task_id)
                # Cap map size.
                if len(self._recent_done) > 64:
                    oldest = sorted(self._recent_done.items(), key=lambda kv: kv[1][0])[:16]
                    for k, _ in oldest:
                        self._recent_done.pop(k, None)
                save_recent_done(self._recent_done)
            try:
                result.unlink(missing_ok=True)
                task_path.unlink(missing_ok=True)
            except Exception:
                pass
            await self.work_event(
                "result",
                task_id=task_id,
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                preview=text[:120],
                task=task_snippet,
            )
            await self.activity(
                "work",
                f"Task RESULT after {_fmt_elapsed(elapsed)}: {task_id} — {text[:100]}",
                task_id=task_id,
                elapsed_ms=elapsed_ms,
            )
            await self.send({"type": "transcript", "role": "assistant", "text": text[:2000]})
            # Skip speaking backlog-cleanup markers (still mark processed).
            if text.startswith("[cleared]"):
                await self.work_event(
                    "result_processed",
                    task_id=task_id,
                    call_id=call_id,
                    elapsed_ms=elapsed_ms,
                    preview=text[:120],
                    task=task_snippet,
                    spoke=False,
                    skipped="cleared",
                )
            elif self.qwen:
                # Exact frameTaskResult + inject-delivery retry (HTML TTS if still silent).
                await self.enqueue_speak(
                    SpeakItem(
                        reason="work_result",
                        prompt_text=(
                            frame_task_result_prompt(text[:1500])
                            + WORK_RESULT_DONE_EPILOGUE
                        ),
                        task_id=task_id,
                        preview=text[:120],
                        meta={"elapsed_ms": elapsed_ms, "call_id": call_id},
                    ),
                    with_retry=True,
                )
                await self.work_event(
                    "result_processed",
                    task_id=task_id,
                    call_id=call_id,
                    elapsed_ms=elapsed_ms,
                    preview=text[:120],
                    task=task_snippet,
                    spoke=False,
                    queued=True,
                    queue_len=len(self.speak_queue),
                )
            else:
                await self.work_event(
                    "result_processed",
                    task_id=task_id,
                    call_id=call_id,
                    elapsed_ms=elapsed_ms,
                    preview=text[:120],
                    task=task_snippet,
                    spoke=False,
                    speak_blocked="no_qwen",
                )
            await self.status(
                "listening"
                if not self._pending_work and not self.speak_queue and not self.gate.responding
                else "working"
            )


async def result_poller(app: web.Application) -> None:
    last_core_sig: str | None = None
    ticks = 0
    while True:
        sessions: list[PhoneSession] = list(app["sessions"])
        for s in sessions:
            try:
                await s.poll_results_once()
            except Exception as e:
                logger.warning("result poll: %s", e)
        # No attached phone session → still retire finished task+result pairs so
        # feeder/core don't keep re-driving them (Baidu re-open loop).
        if not sessions and ticks % 4 == 0:
            try:
                cleaned = await asyncio.to_thread(cleanup_completed_task_pairs)
                if cleaned:
                    logger.info("cleaned %s completed task/result pairs (no session)", cleaned)
            except Exception as e:
                logger.warning("completed-pair cleanup: %s", e)
        ticks += 1
        # Push core liveness every ~4s (or on change) so the HUD stays honest.
        if ticks % 8 == 0 and sessions:
            try:
                core = probe_core_status()
                sig = (
                    f"{core['alive']}:{core.get('ready')}:{core.get('booting')}:"
                    f"{core.get('age_s')}:{core.get('pending_tasks')}:"
                    f"{core.get('status')}:{core.get('pane_error')}"
                )
                if sig != last_core_sig:
                    last_core_sig = sig
                    for s in sessions:
                        await s.push_core_status(core)
            except Exception as e:
                logger.warning("core status push: %s", e)
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
                elif action == "core_status":
                    await session.push_core_status()
                elif action == "start_core":
                    result = await asyncio.to_thread(start_sutando_core)
                    await session.send({"type": "core_start", **result})
                    await session.push_core_status()
                    await session.activity(
                        "session" if result.get("ok") else "error",
                        result.get("message") or json.dumps(result)[:200],
                    )
                elif action == "stop_core":
                    result = await asyncio.to_thread(stop_sutando_core)
                    await session.send({"type": "core_stop", **result})
                    await session.push_core_status()
                    await session.activity(
                        "session" if result.get("ok") else "error",
                        result.get("message") or json.dumps(result)[:200],
                    )
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
        return web.Response(text="omni-exp-client.html missing", status=404)
    return web.FileResponse(CLIENT_HTML)


async def legacy_omni_redirect(_request: web.Request) -> web.StreamResponse:
    """Old /omni bookmarks → canonical /omni-exp (no separate app)."""
    raise web.HTTPFound("/omni-exp")


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
    app.router.add_get("/omni-exp", index)
    app.router.add_get("/omni-exp-client.html", index)
    # Pre-rename paths: redirect only (do not serve a second identity).
    app.router.add_get("/omni", legacy_omni_redirect)
    app.router.add_get("/omni-client.html", legacy_omni_redirect)
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
    print(f"Omni-exp agent at {proto}://0.0.0.0:{PORT}/omni-exp  (ws: {proto.replace('http','ws')}://…/ws)", flush=True)
    web.run_app(app, host="0.0.0.0", port=PORT, ssl_context=ssl_ctx, print=None)


if __name__ == "__main__":
    main()
