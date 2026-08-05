#!/usr/bin/env python3
"""
Pipeline Trace — real-time detailed visualization of user operation pipeline.

Data sources:
1. Conversation logs (logs/conversation-{user}.log) — user input + assistant response
2. Agent log (logs/livekit-agent.log) — task creation, execution path, timing
3. Task/result archives (tasks/*/archive/, results/*/archive/) — metadata + full results
4. logs/pipeline-task-events.jsonl — watcher / LiveKit / voice-bridge / (optional) core emits
5. core-status.json — real-time processing step (proactive loop / sutando-core)
6. state/*.heartbeat — service liveness

Usage:
  python3 skills/pipeline-trace/scripts/pipeline-trace.py
  Open http://localhost:7902
"""

import http.server
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

REPO_DIR = Path(__file__).resolve().parent.parent.parent.parent
PORT = int(os.environ.get("PIPELINE_TRACE_PORT", "7902"))
POLL_INTERVAL = float(os.environ.get("PIPELINE_TRACE_POLL_INTERVAL", "10"))
MAX_TRACES = int(os.environ.get("PIPELINE_TRACE_MAX_TRACES", "20"))
HEALTH_POLL_INTERVAL = float(os.environ.get("PIPELINE_TRACE_HEALTH_INTERVAL", "10"))
CORE_PEEK_INTERVAL = float(os.environ.get("PIPELINE_TRACE_CORE_PEEK_INTERVAL", "10"))
HEARTBEAT_INTERVAL = 15
STALE_ACTIVE_TRACE_AFTER = 15 * 60


# --- Data structures ---

@dataclass
class Checkpoint:
    name: str
    label: str
    status: str = "pending"  # pending, active, completed, error, skipped
    timestamp: float = 0.0
    content: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class Trace:
    trace_id: str
    source: str = ""
    user: str = ""
    text_preview: str = ""
    full_text: str = ""
    result_text: str = ""
    task_id: str = ""
    execution_path: str = ""  # "core", "self-execute", "direct", ""
    checkpoints: list = field(default_factory=list)
    created_at: float = 0.0
    completed_at: float = 0.0
    duration: float = 0.0

    def to_dict(self):
        return {
            "trace_id": self.trace_id,
            "source": self.source,
            "user": self.user,
            "text_preview": self.text_preview,
            "full_text": self.full_text,
            "result_text": self.result_text,
            "task_id": self.task_id,
            "execution_path": self.execution_path,
            "checkpoints": [
                {"name": c.name, "label": c.label, "status": c.status,
                 "timestamp": c.timestamp, "content": c.content, "meta": c.meta}
                for c in self.checkpoints
            ],
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "duration": self.duration,
        }


# --- Global state ---

traces: dict[str, Trace] = {}
traces_lock = threading.Lock()
sse_clients: list[queue.Queue] = []
sse_lock = threading.Lock()


def broadcast(event_type: str, data: str):
    with sse_lock:
        dead = []
        for q in sse_clients:
            try:
                q.put_nowait((event_type, data))
            except Exception:
                dead.append(q)
        for q in dead:
            sse_clients.remove(q)


# --- Agent log parser ---

_TASK_RE = re.compile(r'Task (task-\d+) \[(\w+)\]: (.+)')
_RESULT_CORE_RE = re.compile(r'Result (task-\d+) \(core\): (.+)')
_RESULT_ASYNC_RE = re.compile(r'Result \(async\) (task-\d+): (.+)')

_INLINE_TOOL_PATTERNS = [
    (re.compile(r'^Opened (.+)'), 'open_url'),
    (re.compile(r'^Pressed (.+)'), 'press_key'),
    (re.compile(r'^Typed \d+'), 'type_text'),
    (re.compile(r'^Switched to (.+)'), 'switch_app'),
    (re.compile(r'^Screenshot captured'), 'describe_screen'),
    (re.compile(r'^Failed to open'), 'open_url'),
    (re.compile(r'^press_key failed'), 'press_key'),
]


def _parse_iso_timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return 0.0


def _normalize_task_id(task_id: str) -> str:
    task_id = (task_id or "").strip()
    if task_id.isdigit():
        return f"task-{task_id}"
    return task_id


def _detect_inline_tool(assistant_text: str) -> str:
    for pattern, tool_name in _INLINE_TOOL_PATTERNS:
        if pattern.match(assistant_text):
            return tool_name
    return ""


@dataclass
class AgentEvent:
    timestamp: float
    event_type: str  # task_created, result_core, result_async
    task_id: str
    username: str = ""
    text: str = ""


def parse_agent_log() -> list[AgentEvent]:
    """Parse livekit-agent.log for structured pipeline events."""
    log_path = REPO_DIR / "logs" / "livekit-agent.log"
    if not log_path.exists():
        return []

    events = []
    try:
        lines = log_path.read_text(errors="replace").splitlines()
    except Exception:
        return []

    for line in lines:
        # Try JSON format first
        ts = 0.0
        msg = ""
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                msg = obj.get("message", "")
                ts_str = obj.get("timestamp", "")
                if ts_str:
                    dt = datetime.fromisoformat(ts_str)
                    ts = dt.timestamp()
            except Exception:
                continue
        else:
            # Plain text format: INFO:name:message
            if ":" in line:
                msg = line.split(":", 2)[-1].strip() if line.count(":") >= 2 else ""

        if not msg or not ts:
            continue

        m = _TASK_RE.search(msg)
        if m:
            events.append(AgentEvent(
                timestamp=ts, event_type="task_created",
                task_id=m.group(1), username=m.group(2), text=m.group(3)
            ))
            continue

        m = _RESULT_CORE_RE.search(msg)
        if m:
            events.append(AgentEvent(
                timestamp=ts, event_type="result_core",
                task_id=m.group(1), text=m.group(2)
            ))
            continue

        m = _RESULT_ASYNC_RE.search(msg)
        if m:
            events.append(AgentEvent(
                timestamp=ts, event_type="result_async",
                task_id=m.group(1), text=m.group(2)
            ))

    return events


# --- Conversation log parser ---

def parse_conversation_logs() -> list[tuple]:
    """Parse conversation logs. Returns list of (user_ts, user_text, assistant_ts, assistant_text, username)."""
    logs_dir = REPO_DIR / "logs"
    results = []

    for log_file in logs_dir.glob("conversation-*.log"):
        username = log_file.stem.replace("conversation-", "")
        try:
            lines = log_file.read_text(errors="replace").splitlines()
        except Exception:
            continue

        entries = []
        for line in lines:
            parts = line.split("|", 2)
            if len(parts) < 3:
                continue
            ts_str, role, text = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if role == "SESSION_END":
                continue
            try:
                dt = datetime.fromisoformat(ts_str)
                ts = dt.timestamp()
            except Exception:
                continue
            entries.append((ts, role, text))

        i = 0
        while i < len(entries):
            ts, role, text = entries[i]
            if role != "user":
                i += 1
                continue

            assistant_ts = 0.0
            assistant_text = ""
            j = i + 1
            while j < len(entries):
                if entries[j][1] == "assistant":
                    assistant_ts = entries[j][0]
                    assistant_text = entries[j][2]
                    break
                if entries[j][1] == "user":
                    break
                j += 1

            results.append((ts, text, assistant_ts, assistant_text, username))
            i = j + 1 if assistant_ts > 0 else i + 1

    return results


# --- Task/result archive parser ---

def _parse_task_file(path: Path) -> dict | None:
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return None
    fields = {}
    known_keys = {"id", "timestamp", "task", "source", "channel_id",
                  "username", "user_id", "access_tier"}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        if k in known_keys:
            fields[k] = v.strip()
    return fields if fields else None


def load_task_archive() -> dict[str, dict]:
    """Load live and archived task files indexed by task_id."""
    cache = {}
    tasks_dir = REPO_DIR / "tasks"
    if not tasks_dir.exists():
        return cache
    for f in tasks_dir.rglob("task-*.txt"):
        fields = _parse_task_file(f)
        if fields and "id" in fields:
            cache[fields["id"]] = fields
    return cache


def load_result_archive() -> dict[str, str]:
    """Load live and archived result files indexed by task_id."""
    cache = {}
    results_dir = REPO_DIR / "results"
    if not results_dir.exists():
        return cache
    for f in results_dir.rglob("task-*.txt"):
        try:
            cache[f.stem] = f.read_text(errors="replace").strip()
        except Exception:
            pass
    return cache


# --- Core status ---

def read_core_status() -> dict:
    path = REPO_DIR / "core-status.json"
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"status": "idle", "ts": 0}


# --- Health ---

def _process_alive(pattern: str) -> bool:
    try:
        return subprocess.run(["pgrep", "-f", pattern],
                              capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


_HEARTBEAT_PROCESS_MAP = {
    "livekit-agent": "livekit-agent.py",
    "mobile-control": "mobile-control-server",
    "token-server": "livekit-token-server",
}

_PIPELINE_LOG = REPO_DIR / "logs" / "pipeline-task-events.jsonl"
_CORE_PEEK_CACHE: tuple[float, dict] = (0.0, {})


def _pgrep(pattern: str) -> bool:
    try:
        return subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            timeout=3,
        ).returncode == 0
    except Exception:
        return False


def _claude_core_pid() -> str:
    try:
        proc = subprocess.run(
            ["pgrep", "-fl", "claude.*sutando-core"],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except Exception:
        return ""
    for line in proc.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid, cmd = parts
        if "tmux " in cmd:
            continue
        return pid
    return ""


def _tmux_core_pane() -> str:
    try:
        proc = subprocess.run(
            ["tmux", "-S", "/tmp/sutando-tmux.sock", "capture-pane", "-p", "-t", "sutando-core:0.0", "-S", "-80"],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except Exception:
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def _core_visible_state(pane: str) -> dict:
    lines = [line.strip() for line in pane.splitlines() if line.strip()]
    interesting = ""
    tool = ""
    for line in reversed(lines):
        if any(token in line for token in ("Hatching", "Bash(", "Read ", "Update(", "Monitor(", "Running scheduled task", "Task file detected")):
            interesting = line
            break
    for line in reversed(lines):
        if any(token in line for token in ("Bash(", "Read ", "Update(", "Edit(", "Apply", "Monitor(")):
            tool = line
            break
    state = "unknown"
    if "Hatching" in interesting:
        state = "thinking"
    elif "Bash(" in interesting or "Read " in interesting or "Update(" in interesting or "Monitor(" in interesting:
        state = "tool"
    elif any("❯" in line for line in lines[-6:]):
        state = "idle_or_prompt"
    return {
        "state": state,
        "line": interesting[:180],
        "tool": tool[:180],
    }


def read_core_peek() -> dict:
    """Cheap, cached view of the visible sutando-core Claude session."""
    global _CORE_PEEK_CACHE
    now = time.time()
    cached_ts, cached = _CORE_PEEK_CACHE
    if now - cached_ts < CORE_PEEK_INTERVAL:
        return cached

    pid = _claude_core_pid()
    pane = _tmux_core_pane()
    visible = _core_visible_state(pane)
    data = {
        "alive": bool(pid),
        "pid": pid,
        "tmux_alive": bool(pane),
        "state": visible["state"],
        "line": visible["line"],
        "tool": visible["tool"],
        "cpu": "",
        "mem": "",
        "elapsed": "",
        "https_connected": False,
    }

    if pid:
        try:
            ps = subprocess.run(
                ["ps", "-p", pid, "-o", "etime=,%cpu=,%mem=,state="],
                capture_output=True,
                text=True,
                timeout=1,
            )
            parts = ps.stdout.split()
            if len(parts) >= 4:
                data["elapsed"], data["cpu"], data["mem"], data["proc_state"] = parts[:4]
        except Exception:
            pass
        try:
            lsof = subprocess.run(
                ["lsof", "-nP", "-p", pid, "-iTCP"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            data["https_connected"] = "->" in lsof.stdout and ":443" in lsof.stdout and "ESTABLISHED" in lsof.stdout
        except Exception:
            pass

    _CORE_PEEK_CACHE = (now, data)
    return data


def load_pipeline_events_by_task() -> dict[str, list[dict]]:
    """Parse recent JSONL pipeline emits, grouped by task_id."""
    by_task: dict[str, list[dict]] = {}
    if not _PIPELINE_LOG.exists():
        return by_task
    try:
        lines = _PIPELINE_LOG.read_text(errors="replace").splitlines()
    except Exception:
        return by_task
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        tid = _normalize_task_id(row.get("task_id") or "")
        if not tid:
            continue
        row["task_id"] = tid
        by_task.setdefault(tid, []).append(row)
    for evs in by_task.values():
        evs.sort(key=lambda r: r.get("ts", 0))
    return by_task


def _latest_event(evs: list[dict], phase: str) -> dict | None:
    for ev in reversed(evs):
        if ev.get("phase") == phase:
            return ev
    return None


def _any_event_failed(evs: list[dict], phases: set[str]) -> dict | None:
    for ev in reversed(evs):
        if ev.get("phase") in phases and ev.get("ok") is False:
            return ev
    return None


def _task_result_files_exist(task_id: str) -> tuple[bool, bool]:
    """(tasks/task_id.txt still present, results/task_id.txt present)."""
    tpath = REPO_DIR / "tasks" / f"{task_id}.txt"
    rpath = REPO_DIR / "results" / f"{task_id}.txt"
    return tpath.exists(), rpath.exists()


def _task_file_progress_ts(task_id: str) -> float:
    """Latest filesystem progress timestamp for a live task/result pair."""
    if not task_id:
        return 0.0
    latest = 0.0
    for path in (
        REPO_DIR / "tasks" / f"{task_id}.txt",
        REPO_DIR / "results" / f"{task_id}.txt",
    ):
        try:
            if path.exists():
                latest = max(latest, path.stat().st_mtime)
        except Exception:
            pass
    return latest


def _latest_progress_ts(
    *,
    user_ts: float,
    assistant_ts: float,
    matched_task_id: str,
    pipeline_events: list[dict],
) -> float:
    """Best-effort "real progress" clock used to retire stale active traces."""
    candidates = [float(user_ts or 0)]
    if assistant_ts > 0:
        candidates.append(float(assistant_ts))
    for ev in pipeline_events or []:
        try:
            candidates.append(float(ev.get("ts", 0) or 0))
        except Exception:
            pass
    candidates.append(_task_file_progress_ts(matched_task_id))
    return max(candidates)


def _retire_stale_active_checkpoints(
    checkpoints: list[Checkpoint],
    *,
    last_progress_ts: float,
    now: float,
) -> None:
    """Move stuck traces out of Active after no progress for 15 minutes."""
    if not any(c.status == "active" for c in checkpoints):
        return
    stale_for = now - float(last_progress_ts or 0)
    if stale_for <= STALE_ACTIVE_TRACE_AFTER:
        return

    stale_min = int(STALE_ACTIVE_TRACE_AFTER / 60)
    for cp in checkpoints:
        if cp.status != "active":
            continue
        previous = cp.content
        cp.status = "error"
        cp.timestamp = last_progress_ts or cp.timestamp
        cp.content = (
            f"无进展超过 {stale_min} 分钟，已自动移出 Active。"
            + (f" 上次状态：{previous}" if previous else "")
        )
        cp.meta = {
            **(cp.meta or {}),
            "failure_hint": "trace_no_progress",
            "stale_after_s": STALE_ACTIVE_TRACE_AFTER,
            "stale_for_s": round(stale_for, 1),
        }


def read_heartbeats() -> list[dict]:
    state_dir = REPO_DIR / "state"
    results = []
    if not state_dir.exists():
        return results
    for f in state_dir.glob("*.heartbeat"):
        try:
            ts = float(f.read_text().strip())
            age = time.time() - ts
            name = f.stem.replace("-", " ").title()
            ok = age < 120
            if not ok:
                pattern = _HEARTBEAT_PROCESS_MAP.get(f.stem)
                if pattern and _process_alive(pattern):
                    ok = True
            results.append({"name": name, "age_s": round(age), "ok": ok})
        except Exception:
            results.append({"name": f.stem, "age_s": -1, "ok": False})
    return results


def read_owner_activity() -> dict:
    path = REPO_DIR / "state" / "last-owner-activity.json"
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


# --- Trace builder ---

def build_traces() -> list[Trace]:
    """Build detailed traces from multiple data sources."""
    conv_entries = parse_conversation_logs()
    agent_events = parse_agent_log()
    task_archive = load_task_archive()
    result_archive = load_result_archive()
    core_status = read_core_status()
    pipeline_by_task = load_pipeline_events_by_task()

    # Index agent events by task_id
    agent_by_task: dict[str, list[AgentEvent]] = {}
    for ev in agent_events:
        agent_by_task.setdefault(ev.task_id, []).append(ev)

    # Index agent task_created events by timestamp for matching
    agent_task_by_ts: list[AgentEvent] = [
        ev for ev in agent_events if ev.event_type == "task_created"
    ]
    agent_task_by_ts.sort(key=lambda e: e.timestamp)
    task_meta_by_ts: list[tuple[float, str, dict]] = []
    for task_id, meta in task_archive.items():
        task_ts = _parse_iso_timestamp(meta.get("timestamp"))
        if task_ts > 0:
            task_meta_by_ts.append((task_ts, task_id, meta))
    task_meta_by_ts.sort(key=lambda row: row[0])
    task_candidates: dict[str, tuple[float, str]] = {
        task_id: (task_ts, meta.get("username", ""))
        for task_ts, task_id, meta in task_meta_by_ts
    }
    for ev in agent_task_by_ts:
        task_candidates.setdefault(ev.task_id, (ev.timestamp, ev.username))

    conv_task_matches: dict[int, str] = {}
    matched_conv_indexes: set[int] = set()
    for task_id, (task_ts, task_user) in sorted(task_candidates.items(), key=lambda row: row[1][0]):
        best_idx = -1
        best_diff = 999.0
        for idx, (user_ts, _user_text, _assistant_ts, _assistant_text, username) in enumerate(conv_entries):
            if idx in matched_conv_indexes:
                continue
            if task_user and task_user != username:
                continue
            diff = abs(task_ts - user_ts)
            if diff < 3.0 and diff < best_diff:
                best_idx = idx
                best_diff = diff
        if best_idx >= 0:
            conv_task_matches[best_idx] = task_id
            matched_conv_indexes.add(best_idx)

    all_traces = []
    traced_task_ids: set[str] = set()

    for conv_idx, (user_ts, user_text, assistant_ts, assistant_text, username) in enumerate(conv_entries):
        trace_id = f"conv-{username}-{int(user_ts * 1000)}"

        # Match with agent event by timestamp (±3s window)
        matched_task_id = conv_task_matches.get(conv_idx, "")
        matched_events: list[AgentEvent] = agent_by_task.get(matched_task_id, [])

        # Determine execution path
        exec_path = ""
        has_core_result = False
        for ev in matched_events:
            if ev.event_type == "result_core":
                has_core_result = True
            elif ev.event_type == "result_async":
                has_core_result = True

        if matched_task_id:
            traced_task_ids.add(matched_task_id)
            if has_core_result:
                exec_path = "core"
            elif assistant_ts > 0:
                exec_path = "core"
            else:
                exec_path = ""
        else:
            exec_path = "direct" if assistant_ts > 0 and (assistant_ts - user_ts) < 5 else ""

        # Get task metadata from archive
        task_meta = task_archive.get(matched_task_id, {})
        source = task_meta.get("source", "voice")
        tier = task_meta.get("access_tier", "owner")

        # Get full result from archive
        full_result = result_archive.get(matched_task_id, assistant_text)

        # Duration
        duration = (assistant_ts - user_ts) if assistant_ts > 0 else 0.0

        # Build checkpoints
        task_pipeline_events = pipeline_by_task.get(matched_task_id, [])
        checkpoints = _build_checkpoints(
            user_ts=user_ts,
            user_text=user_text,
            assistant_ts=assistant_ts,
            assistant_text=assistant_text,
            matched_task_id=matched_task_id,
            exec_path=exec_path,
            task_meta=task_meta,
            full_result=full_result,
            core_status=core_status,
            duration=duration,
            source=source,
            tier=tier,
            pipeline_events=task_pipeline_events,
        )
        last_progress_ts = _latest_progress_ts(
            user_ts=user_ts,
            assistant_ts=assistant_ts,
            matched_task_id=matched_task_id,
            pipeline_events=task_pipeline_events,
        )
        _retire_stale_active_checkpoints(
            checkpoints,
            last_progress_ts=last_progress_ts,
            now=time.time(),
        )

        trace = Trace(
            trace_id=trace_id,
            source=source,
            user=username,
            text_preview=user_text[:80],
            full_text=user_text,
            result_text=full_result[:300] if full_result else assistant_text[:300],
            task_id=matched_task_id,
            execution_path=exec_path,
            checkpoints=checkpoints,
            created_at=user_ts,
            completed_at=assistant_ts,
            duration=duration,
        )
        all_traces.append(trace)

    candidate_task_ids = {
        tid for tid in set(pipeline_by_task) | set(task_archive) | set(agent_by_task)
        if tid.startswith("task-") and tid not in traced_task_ids
    }
    for task_id in sorted(candidate_task_ids):
        task_meta = task_archive.get(task_id, {})
        task_events = agent_by_task.get(task_id, [])
        pipeline_events = pipeline_by_task.get(task_id, [])
        created_event = next((ev for ev in task_events if ev.event_type == "task_created"), None)
        result_event = next(
            (ev for ev in reversed(task_events) if ev.event_type in {"result_core", "result_async"}),
            None,
        )

        event_ts = [
            float(ev.get("ts", 0) or 0)
            for ev in pipeline_events
            if float(ev.get("ts", 0) or 0) > 0
        ]
        user_ts = (
            _parse_iso_timestamp(task_meta.get("timestamp"))
            or (created_event.timestamp if created_event else 0.0)
            or (min(event_ts) if event_ts else 0.0)
        )
        if user_ts <= 0:
            continue

        username = task_meta.get("username") or (created_event.username if created_event else "") or "unknown"
        user_text = task_meta.get("task") or (created_event.text if created_event else "") or task_id
        source = task_meta.get("source") or "livekit-voice"
        tier = task_meta.get("access_tier", "owner")
        full_result = result_archive.get(task_id, result_event.text if result_event else "")
        delivered_event = (
            _latest_event(pipeline_events, "agent_consumed_sync_result")
            or _latest_event(pipeline_events, "agent_generate_reply_from_result")
            or _latest_event(pipeline_events, "result_file_observed")
        )
        assistant_ts = float((delivered_event or {}).get("ts", 0) or 0)
        assistant_text = full_result or (result_event.text if result_event else "")
        duration = (assistant_ts - user_ts) if assistant_ts > 0 else 0.0
        exec_path = "core"

        checkpoints = _build_checkpoints(
            user_ts=user_ts,
            user_text=user_text,
            assistant_ts=assistant_ts,
            assistant_text=assistant_text,
            matched_task_id=task_id,
            exec_path=exec_path,
            task_meta=task_meta,
            full_result=full_result,
            core_status=core_status,
            duration=duration,
            source=source,
            tier=tier,
            pipeline_events=pipeline_events,
        )
        last_progress_ts = _latest_progress_ts(
            user_ts=user_ts,
            assistant_ts=assistant_ts,
            matched_task_id=task_id,
            pipeline_events=pipeline_events,
        )
        _retire_stale_active_checkpoints(
            checkpoints,
            last_progress_ts=last_progress_ts,
            now=time.time(),
        )

        all_traces.append(Trace(
            trace_id=f"task-{task_id}",
            source=source,
            user=username,
            text_preview=user_text[:80],
            full_text=user_text,
            result_text=full_result[:300] if full_result else assistant_text[:300],
            task_id=task_id,
            execution_path=exec_path,
            checkpoints=checkpoints,
            created_at=user_ts,
            completed_at=assistant_ts,
            duration=duration,
        ))

    return all_traces


def _build_executor_bridge(
    *,
    matched_task_id: str,
    exec_path: str,
    user_ts: float,
    assistant_ts: float,
    core_status: dict,
    pipeline_events: list[dict],
) -> list[Checkpoint]:
    """Six-step core ↔ watcher ↔ voice visibility (success + failure semantics)."""
    if not matched_task_id:
        return []

    evs = list(pipeline_events or [])
    now = time.time()
    wq_ev = _latest_event(evs, "watcher_to_core_queue")
    wp_ev = _latest_event(evs, "watcher_pickup")
    watcher_ok_ev = (wq_ev and wq_ev.get("ok", True)) or (
        wp_ev and wp_ev.get("ok", True)
    )
    watcher_glob_fail = _latest_event(evs, "watcher_fswatch_missing")
    task_waiting = assistant_ts <= 0
    task_txt, result_txt = _task_result_files_exist(matched_task_id)

    watchers_proc = _pgrep("watch-tasks") or _pgrep("fswatch ")
    sutando_core_proc = _pgrep("claude.*sutando-core") or _pgrep("claude.*--name.*sutando-core")
    core_peek = read_core_peek()
    peek_state = core_peek.get("state") or "unknown"
    peek_line = core_peek.get("line") or ""
    peek_summary = (
        f"Claude {peek_state}"
        + (f" · {peek_line}" if peek_line else "")
        + (f" · CPU {core_peek.get('cpu')}%" if core_peek.get("cpu") else "")
        + (f" · HTTPS {'yes' if core_peek.get('https_connected') else 'no'}" if core_peek.get("alive") else "")
    )

    # --- 1. File watcher → core queue ---
    b1_ts = (_latest_event(evs, "watcher_to_core_queue") or _latest_event(evs, "watcher_pickup") or {}).get(
        "ts", 0
    ) or (_latest_event(evs, "task_enqueued") or {}).get("ts", 0)
    if watcher_glob_fail and not watcher_glob_fail.get("ok", True):
        b1_status, b1_content = (
            "error",
            "失败：fswatch 缺失或不可用 — " + (watcher_glob_fail.get("detail") or "安装 fswatch 并重启 watch-tasks"),
        )
    elif watcher_ok_ev:
        d = ((wq_ev or {}).get("detail")) or (
            (wp_ev or {}).get("detail") or "已枚举 tasks/*.txt → sutando-core 可读输出"
        )
        b1_status, b1_content = "completed", d
    elif task_waiting and (now - user_ts) > 20 and not watchers_proc:
        b1_status, b1_content = (
            "error",
            "失败：watch-tasks/fswatch 进程未检测到 — Claude 可能不会收到 TASK_DETECTED（建议 pgrep watch-tasks）",
        )
    elif task_waiting:
        b1_status, b1_content = "active", "等待 fswatch 触发并将任务写给 sutando-core 阅读…"
    else:
        # Finished trace without explicit watcher emit (historical): infer OK if core path worked
        b1_status, b1_content = "completed", "（推断）链路曾完成或未记录 watcher 打点"

    b1_meta = {"failure_hint": "watcher_dead_or_fswatch"} if b1_status == "error" else {}

    # --- 2. sutando-core session alive ---
    claim_ev = _latest_event(evs, "core_task_claim") or _latest_event(evs, "core_task_begin")
    if claim_ev is not None and claim_ev.get("ok", True):
        detail = claim_ev.get("detail") or "sutando-core 报告开始处理此 task"
        b2_ts, b2_status, b2_content = claim_ev.get("ts", 0), "completed", f"{detail}\n{peek_summary}"
        b2_meta = {"core_peek": core_peek}
    elif claim_ev is not None and claim_ev.get("ok") is False:
        b2_ts = float(claim_ev.get("ts", 0)) or user_ts
        b2_status, b2_content = (
            "error",
            "失败：" + (claim_ev.get("detail") or "core 拒绝或未能认领 task"),
        )
        b2_meta = {"failure_hint": "core_claim_rejected"}
    elif sutando_core_proc:
        b2_ts = float(core_status.get("ts", 0)) or user_ts
        if task_waiting and not claim_ev:
            b2_status, b2_content = "active", "检测到 claude sutando-core 进程；等待 claim 打点（建议在处理 task 前后运行 pipeline_emit）\n" + peek_summary
        else:
            b2_status, b2_content = "completed", "检测到 claude sutando-core / tmux 会话进程\n" + peek_summary
        b2_meta = {"peek_core_status_json": True, "peek_tmux_attach": True, "core_peek": core_peek}
    elif task_waiting:
        b2_ts = user_ts
        b2_status, b2_content = (
            "error",
            "失败：未检测到 sutando-core（claude --name sutando-core）— proactive loop 可能不会消费任务",
        )
        b2_meta = {"failure_hint": "sutando_core_down"}
    else:
        b2_ts = user_ts
        b2_status, b2_content = "skipped", "任务已关闭（无待定 core）"
        b2_meta = {}
    # --- 3. Core processing (+ proactive loop heartbeat) ---
    cs_stat = core_status.get("status", "idle")
    step = core_status.get("step", "")
    sync_wait_timeout = _any_event_failed(evs, {"livekit_sync_wait_timeout"})
    if sync_wait_timeout and task_waiting:
        b3_ts = sync_wait_timeout.get("ts", 0) or user_ts
        b3_status = "active"
        b3_content = (
            sync_wait_timeout.get("detail")
            or "LiveKit 同步等待超时；task 保留在 tasks/ 中等待 sutando-core"
        )
        if core_peek.get("alive"):
            b3_content += "\n" + peek_summary
        b3_meta = {"core_peek": core_peek} if core_peek.get("alive") else {}
    elif task_waiting and cs_stat == "running":
        elapsed = max(0.0, now - user_ts)
        b3_ts = float(core_status.get("ts", 0)) or now
        b3_status = "active"
        b3_content = f"进行中：core-status.step = {step or 'working…'} （已等待 {elapsed:.0f}s；含 proactive-loop 报告的 step）\n{peek_summary}"
        b3_meta = {"elapsed_s": round(elapsed, 1), "core_peek": core_peek}
    elif pulse := _latest_event(evs, "livekit_poll_core_pulse"):
        b3_ts = pulse.get("ts", 0)
        b3_status, b3_content = "completed", f"LiveKit 轮询心跳：{pulse.get('detail', '')}"
        b3_meta = {}
    elif apulse := _latest_event(evs, "livekit_async_poll_pulse"):
        b3_ts = apulse.get("ts", 0)
        b3_status, b3_content = "active", apulse.get("detail", "") or "LiveKit async 长线轮询 heartbeat"
        b3_meta = {}
    elif assistant_ts > 0:
        b3_ts = assistant_ts - 1
        b3_status, b3_content = "completed", "已进入回复阶段 — core 侧面视为成功"
        b3_meta = {}
    elif cs_stat != "running" and task_waiting and sutando_core_proc:
        staleness = now - float(core_status.get("ts", 0) or 0)
        if staleness > 120:
            b3_ts = now
            b3_status = "error"
            b3_content = (
                f"失败：core-status 过久未更新 ({staleness:.0f}s stale) — session 卡住或没在写 core-status.json"
            )
            b3_meta = {"failure_hint": "core_status_stale"}
        else:
            b3_ts = now
            b3_status, b3_content = (
                "active",
                "core-status=idle — 可能在两次 proactive pass 间隙；若过久请检查会话",
            )
            b3_meta = {}
    elif task_waiting:
        b3_ts = now
        b3_status, b3_content = "pending", "等待执行状态…"
        b3_meta = {}
    else:
        b3_ts = assistant_ts or user_ts
        b3_status, b3_content = "completed", "—"
        b3_meta = {}

    # --- 4. Core execute success (semantic) ---
    done_ev = _latest_event(evs, "core_task_done")
    timeout_ev = _any_event_failed(
        evs,
        {"livekit_async_wait_timeout", "task_bridge_timeout"},
    )
    if timeout_ev:
        b4_status, b4_content = "error", "失败：" + (
            timeout_ev.get("detail") or "桥接层等待结果超时"
        )
        b4_ts = timeout_ev.get("ts", 0)
        b4_meta = {"failure_hint": "bridge_timeout"}
    elif done_ev:
        b4_ts, b4_status, b4_content = done_ev.get("ts", 0), "completed", done_ev.get("detail") or "core 报告 task 完成"
        b4_meta = {}
    elif assistant_ts > 0 and not sync_wait_timeout:
        b4_ts = assistant_ts - 0.5
        b4_status, b4_content = "completed", "已产生语音/文本回复 — 推断执行成功"
        b4_meta = {}
    elif task_waiting:
        b4_ts = user_ts
        b4_status, b4_content = "active", "仍在执行或等待结果…"
        b4_meta = {}
    else:
        b4_ts = user_ts
        b4_status, b4_content = "pending", "—"
        b4_meta = {}

    # --- 5. Result file on disk ---
    ro = _latest_event(evs, "result_file_observed")
    if ro and ro.get("ok"):
        b5_ts, b5_status, b5_content = ro.get("ts", 0), "completed", ro.get("detail") or "results/*.txt 已读"
        b5_meta = {}
    elif assistant_ts > 0 and not sync_wait_timeout:
        b5_ts = assistant_ts - 0.4
        b5_status, b5_content = "completed", "结果已消费（可能已归档至 results/archive）"
        b5_meta = {}
    elif result_txt:
        b5_ts = now
        b5_status, b5_content = "active", "results/*.txt 存在，等待 LiveKit/桥接读取"
        b5_meta = {}
    elif task_txt:
        b5_ts = user_ts
        b5_status, b5_content = "pending", "尚无 results/task.txt — sutando-core 尚未写回?"
        b5_meta = {}
    else:
        b5_ts = user_ts
        b5_status, b5_content = "completed", "—"
        b5_meta = {}

    # --- 6. Voice / agent consumes result ---
    ad = (
        _latest_event(evs, "agent_consumed_sync_result")
        or _latest_event(evs, "agent_result_delivery")
        or _latest_event(evs, "agent_generate_reply_from_result")
    )
    if timeout_ev:
        b6_ts = timeout_ev.get("ts", now)
        b6_status, b6_content = "error", "失败：未及时向用户播报 — " + (
            timeout_ev.get("detail") or "查看 task_bridge_timeout / async timeout"
        )
        b6_meta = {"failure_hint": "voice_delivery"}
    elif ad:
        b6_ts, b6_status, b6_content = ad.get("ts", 0), "completed", ad.get("detail") or "Agent 已从 results 拉回并交给模型/TTS"
        b6_meta = {}
    elif assistant_ts > 0:
        b6_ts = assistant_ts
        b6_status, b6_content = (
            "completed",
            "对话日志已有 assistant — 推断已交付语音/文本客户端",
        )
        b6_meta = {}
    elif task_waiting:
        b6_ts = user_ts
        b6_status, b6_content = "pending", "等待 Agent 读取 results 并向用户播报"
        b6_meta = {}
    else:
        b6_ts = assistant_ts or user_ts
        b6_status, b6_content = "completed", "—"
        b6_meta = {}

    # Promote timestamps: avoid 0 ordering glitches
    def _sanitize_ts(t: float, fallback: float) -> float:
        x = float(t or 0)
        return x if x > 946684800 else fallback

    u_ts = float(user_ts)
    return [
        Checkpoint(
            name="bridge_watcher_handoff",
            label="① 文件监听→核心",
            status=b1_status,
            timestamp=_sanitize_ts(float(b1_ts or 0), u_ts),
            content=b1_content,
            meta=b1_meta,
        ),
        Checkpoint(
            name="bridge_core_alive",
            label="② sutando-core",
            status=b2_status,
            timestamp=_sanitize_ts(float(b2_ts or 0), u_ts),
            content=b2_content,
            meta=b2_meta,
        ),
        Checkpoint(
            name="bridge_core_process",
            label="③ 核心处理 / proactive ",
            status=b3_status,
            timestamp=_sanitize_ts(float(b3_ts or 0), u_ts),
            content=b3_content,
            meta=b3_meta,
        ),
        Checkpoint(
            name="bridge_execute_ok",
            label="④ 核心执行产物",
            status=b4_status,
            timestamp=_sanitize_ts(float(b4_ts or 0), u_ts),
            content=b4_content,
            meta=b4_meta,
        ),
        Checkpoint(
            name="bridge_result_disk",
            label="⑤ 结果落盘",
            status=b5_status,
            timestamp=_sanitize_ts(float(b5_ts or 0), u_ts),
            content=b5_content,
            meta=b5_meta,
        ),
        Checkpoint(
            name="bridge_voice_agent",
            label="⑥ 播报/Agent",
            status=b6_status,
            timestamp=_sanitize_ts(float(b6_ts or 0), u_ts),
            content=b6_content,
            meta=b6_meta,
        ),
    ]


def _build_checkpoints(*, user_ts, user_text, assistant_ts, assistant_text,
                       matched_task_id, exec_path, task_meta,
                       full_result, core_status, duration, source, tier,
                       pipeline_events) -> list[Checkpoint]:
    """Build detailed checkpoints including cross-service executor bridge."""
    cps = []
    # 1. Voice Input
    cp1 = Checkpoint(name="voice_input", label="语音输入", status="completed",
                     timestamp=user_ts, content=user_text)
    cps.append(cp1)

    # 2. Tool Decision
    if exec_path == "direct":
        tool_name = _detect_inline_tool(assistant_text)
        if tool_name:
            cp2 = Checkpoint(name="tool_decision", label="工具决策", status="completed",
                             timestamp=user_ts, content=f"{tool_name}() — 内联执行")
        else:
            cp2 = Checkpoint(name="tool_decision", label="工具决策", status="completed",
                             timestamp=user_ts, content="直接回答（无需 work 调用）")
    elif matched_task_id:
        cp2 = Checkpoint(name="tool_decision", label="工具决策", status="completed",
                         timestamp=user_ts, content="调用 work() → 委派后端处理")
    elif assistant_ts > 0:
        cp2 = Checkpoint(name="tool_decision", label="工具决策", status="completed",
                         timestamp=user_ts, content="work() 调用")
    else:
        cp2 = Checkpoint(name="tool_decision", label="工具决策", status="active",
                         timestamp=user_ts, content="等待 AI 决策...")
    cps.append(cp2)

    # 3. Task Created
    if matched_task_id:
        meta_detail = f"{matched_task_id} | {source} | {tier}"
        cp3 = Checkpoint(name="task_created", label="任务创建", status="completed",
                         timestamp=user_ts, content=meta_detail,
                         meta={"task_id": matched_task_id, "source": source, "tier": tier})
    elif exec_path == "direct":
        cp3 = Checkpoint(name="task_created", label="任务创建", status="skipped",
                         content="直接回答，跳过任务创建")
    elif assistant_ts > 0:
        cp3 = Checkpoint(name="task_created", label="任务创建", status="completed",
                         timestamp=user_ts, content=f"task 已创建 | {source}")
    else:
        cp3 = Checkpoint(name="task_created", label="任务创建", status="active",
                         timestamp=user_ts, content="正在创建任务...")
    cps.append(cp3)

    # 4. Execution Path
    if exec_path == "core":
        cp4 = Checkpoint(name="execution", label="执行方式", status="completed",
                         timestamp=user_ts,
                         content="Core 处理 — sutando-core 接手执行",
                         meta={"path": "core"})
    elif exec_path == "direct":
        cp4 = Checkpoint(name="execution", label="执行方式", status="skipped",
                         content="直接回答，无后端执行")
    elif assistant_ts == 0:
        cp4 = Checkpoint(name="execution", label="执行方式", status="pending",
                         content="等待执行方式确定...")
    else:
        cp4 = Checkpoint(name="execution", label="执行方式", status="completed",
                         timestamp=user_ts, content="已执行")
    cps.append(cp4)

    executor_bridge: list[Checkpoint] = []
    if matched_task_id and exec_path != "direct":
        executor_bridge = _build_executor_bridge(
            matched_task_id=matched_task_id,
            exec_path=exec_path,
            user_ts=user_ts,
            assistant_ts=assistant_ts,
            core_status=core_status,
            pipeline_events=pipeline_events or [],
        )
    if executor_bridge:
        cps.extend(executor_bridge)

    # 5. Processing (rollup — expanded by ①–⑥ when bridge present)
    is_active = assistant_ts == 0 and cp3.status != "skipped"
    if executor_bridge:
        cp5 = Checkpoint(
            name="processing",
            label="处理中(汇总)",
            status="skipped",
            timestamp=user_ts,
            content="Executor 细节见 ①–⑥。proactive-loop 写入的 core-status.step 会出现在 ③。",
        )
    elif is_active and core_status.get("status") == "running":
        step = core_status.get("step", "working...")
        elapsed = time.time() - user_ts
        cp5 = Checkpoint(name="processing", label="处理中", status="active",
                         timestamp=user_ts,
                         content=f"{step} ({elapsed:.0f}s)",
                         meta={"step": step, "elapsed": elapsed})
    elif is_active:
        elapsed = time.time() - user_ts
        cp5 = Checkpoint(name="processing", label="处理中", status="active",
                         timestamp=user_ts,
                         content=f"执行中... ({elapsed:.0f}s)")
    elif exec_path == "direct":
        cp5 = Checkpoint(name="processing", label="处理中", status="skipped",
                         content="直接回答")
    elif assistant_ts > 0:
        proc_time = duration
        if proc_time < 60:
            time_str = f"{proc_time:.1f}s"
        else:
            time_str = f"{proc_time / 60:.1f}min"
        cp5 = Checkpoint(name="processing", label="处理中", status="completed",
                         timestamp=user_ts,
                         content=f"处理完成 ({time_str})")
    else:
        cp5 = Checkpoint(name="processing", label="处理中", status="pending")
    cps.append(cp5)

    # 6. Result Generated
    if assistant_ts > 0 and (full_result or assistant_text):
        result_display = (full_result or assistant_text)[:200]
        cp6 = Checkpoint(name="result", label="结果生成", status="completed",
                         timestamp=assistant_ts, content=result_display)
    elif exec_path == "direct" and assistant_ts > 0:
        cp6 = Checkpoint(name="result", label="结果生成", status="completed",
                         timestamp=assistant_ts, content=assistant_text[:200])
    else:
        cp6 = Checkpoint(name="result", label="结果生成", status="pending")
    cps.append(cp6)

    # 7. Delivered
    if assistant_ts > 0:
        cp7 = Checkpoint(name="delivered", label="回复交付", status="completed",
                         timestamp=assistant_ts,
                         content=f"TTS 播报完成 | 总耗时 {duration:.1f}s" if duration > 0 else "已回复")
    else:
        cp7 = Checkpoint(name="delivered", label="回复交付", status="pending")
    cps.append(cp7)

    return cps


# --- Collector loop ---

def collector_loop():
    prev_snapshot: str = ""

    while True:
        try:
            trace_list = build_traces()

            # Sort newest first, limit
            trace_list.sort(key=lambda t: t.created_at, reverse=True)
            if MAX_TRACES > 0:
                trace_list = trace_list[:MAX_TRACES]

            with traces_lock:
                traces.clear()
                for t in trace_list:
                    traces[t.trace_id] = t

            snapshot = json.dumps([t.to_dict() for t in trace_list], ensure_ascii=False)
            if snapshot != prev_snapshot:
                broadcast("traces", snapshot)
                prev_snapshot = snapshot

        except Exception as e:
            print(f"collector error: {e}", file=sys.stderr)

        time.sleep(POLL_INTERVAL)


def health_broadcast_loop():
    while True:
        try:
            heartbeats = read_heartbeats()
            activity = read_owner_activity()
            core = read_core_status()
            data = {
                "heartbeats": heartbeats,
                "owner_activity": activity,
                "core_status": core,
                "core_peek": read_core_peek(),
                "watcher_live": _pgrep("watch-tasks"),
                "sutando_core_live": _pgrep("claude.*sutando-core")
                or _pgrep("claude.*--name.*sutando-core"),
            }
            broadcast("health", json.dumps(data, ensure_ascii=False))
        except Exception:
            pass
        time.sleep(HEALTH_POLL_INTERVAL)


# --- HTML ---

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pipeline Trace — Sutando</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif;background:#0a0a12;color:#c0c0d0;min-height:100vh;line-height:1.5}

.header{padding:14px 20px;border-bottom:1px solid #1e1e30;display:flex;align-items:center;gap:12px;position:sticky;top:0;background:#0a0a12;z-index:10}
.header h1{font-size:15px;color:#fff;font-weight:600}
.badges{display:flex;gap:8px;margin-left:auto;font-size:11px}
.badge{padding:3px 10px;border-radius:12px;font-weight:500}
.badge.idle{background:#1a1a2e;color:#7b7baa}
.badge.running{background:#1a2e2e;color:#4eccc3;animation:pulse-badge 1.5s infinite}
.badge.count{background:#12121e;color:#666}
@keyframes pulse-badge{0%,100%{opacity:1}50%{opacity:.6}}

.container{max-width:800px;margin:0 auto;padding:16px 20px}
.section-label{font-size:11px;color:#555;text-transform:uppercase;letter-spacing:.5px;margin:20px 0 10px;font-weight:600;display:flex;align-items:center;gap:8px}
.section-label .count{color:#444}

/* Trace card */
.trace-card{background:#12121e;border:1px solid #1e1e30;border-radius:10px;margin-bottom:10px;transition:border-color .2s ease,box-shadow .2s ease,background-color .2s ease;overflow:hidden}
.trace-card.new-trace{animation:slide-in .25s ease}
.trace-card.active-trace{border-color:#2a4a4a;box-shadow:0 0 20px rgba(78,204,163,.05)}
@keyframes slide-in{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}
@media (prefers-reduced-motion:reduce){
  .trace-card,.trace-card.new-trace,.badge.running,.trace-status-icon.active,.tl-dot.active,.connected-indicator{animation:none}
  .trace-card{transition:none}
}

.trace-summary{padding:12px 16px;cursor:pointer;display:flex;align-items:center;gap:10px;user-select:none}
.trace-summary:hover{background:#151520}
.trace-expand{color:#444;font-size:10px;transition:transform .2s}
.trace-expand.open{transform:rotate(90deg)}
.trace-source{padding:2px 8px;border-radius:6px;font-size:10px;font-weight:600;text-transform:uppercase;flex-shrink:0}
.trace-source.voice,.trace-source.livekit-voice{background:#1e1a2e;color:#a388ee}
.trace-source.discord{background:#1a1e2e;color:#7289da}
.trace-source.telegram{background:#1a2e2e;color:#5ecbd6}
.trace-source.api{background:#2e2a1a;color:#d4a852}
.trace-source.context-drop{background:#2e1a1a;color:#d67b7b}
.trace-user{color:#888;font-size:11px;flex-shrink:0}
.trace-text{color:#bbb;font-size:12px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.trace-meta{display:flex;align-items:center;gap:8px;flex-shrink:0}
.trace-duration{font-size:10px;color:#4ecca3;font-weight:500}
.trace-time{color:#555;font-size:11px}
.trace-status-icon{font-size:10px}
.trace-status-icon.done{color:#4ecca3}
.trace-status-icon.active{color:#4eccc3;animation:pulse-badge 1.2s infinite}
.trace-status-icon.error{color:#e94560}

/* Timeline detail */
.trace-detail{padding:0 16px 16px;display:none}
.trace-detail.open{display:block}
.timeline{position:relative;padding-left:24px;margin-top:4px}
.timeline::before{content:'';position:absolute;left:7px;top:8px;bottom:8px;width:2px;background:#1e1e30;border-radius:1px}

.tl-node{position:relative;padding:8px 0 8px 16px;font-size:12px}
.tl-dot{position:absolute;left:-20px;top:12px;width:10px;height:10px;border-radius:50%;border:2px solid #2a2a3a;background:#0a0a12;z-index:2}
.tl-dot.completed{background:#4ecca3;border-color:#4ecca3}
.tl-dot.active{background:#4eccc3;border-color:#4eccc3;animation:pulse-node 1.2s infinite}
.tl-dot.error{background:#e94560;border-color:#e94560}
.tl-dot.skipped{background:#1a1a2a;border-color:#2a2a3a;border-style:dashed}
@keyframes pulse-node{0%,100%{box-shadow:0 0 0 0 rgba(78,204,163,.6)}50%{box-shadow:0 0 0 5px rgba(78,204,163,0)}}

.tl-header{display:flex;align-items:center;gap:8px}
.tl-label{font-weight:500;color:#ddd}
.tl-label.pending{color:#444}
.tl-label.skipped{color:#444;text-decoration:line-through}
.tl-label.active{color:#4eccc3}
.tl-time{font-size:10px;color:#555;margin-left:auto}
.tl-content{margin-top:3px;font-size:11px;color:#888;word-break:break-all;white-space:pre-wrap;max-height:80px;overflow:hidden;text-overflow:ellipsis}
.tl-meta{margin-top:4px;font-size:10px;color:#5a8a82;font-family:ui-monospace,monospace}
.tl-content.result-content{max-height:120px;color:#999;background:#0d0d18;padding:8px 10px;border-radius:6px;margin-top:6px}

/* Health bar */
.health-bar{display:flex;gap:12px;flex-wrap:wrap;padding:8px 0}
.health-item{display:flex;align-items:center;gap:4px;font-size:11px;color:#666}
.health-dot{width:6px;height:6px;border-radius:50%}
.health-dot.ok{background:#4ecca3}
.health-dot.bad{background:#e94560}
.health-dot.stale{background:#f0ad4e}

.empty{text-align:center;color:#444;padding:40px;font-size:13px}
.connected-indicator{width:6px;height:6px;border-radius:50%;background:#4ecca3;animation:pulse-conn 2s infinite}
@keyframes pulse-conn{0%,100%{opacity:1}50%{opacity:.3}}
.disconnected-indicator{width:6px;height:6px;border-radius:50%;background:#e94560}
</style>
</head>
<body>

<div class="header">
  <h1>Pipeline Trace</h1>
  <div id="conn-status" class="connected-indicator" title="SSE connected"></div>
  <div class="badges">
    <span class="badge idle" id="status-badge">idle</span>
    <span class="badge count" id="count-badge">0 traces</span>
  </div>
</div>

<div class="container">
  <div style="font-size:10px;color:#555;line-height:1.45;margin:0 0 12px">
    Executor 链路①–⑥ 读取 <span style="color:#6a8">logs/pipeline-task-events.jsonl</span> + 实时进程探测。
    窥视 sutando-core：<code style="background:#151520;padding:1px 5px;border-radius:4px;color:#8ab">core-status.json</code>（proactive-loop 写 step）；
    <code style="background:#151520;padding:1px 5px;border-radius:4px;color:#8ab">tmux -S /tmp/sutando-tmux.sock attach -t sutando-core</code>
  </div>
    <label style="font-size:11px;color:#555">用户:</label>
    <select id="user-filter" style="background:#111;color:#aaa;border:1px solid #333;border-radius:4px;padding:3px 6px;font-size:12px" onchange="applyFilter()">
      <option value="">全部</option>
    </select>
  </div>
  <div id="active-section"></div>
  <div id="history-section">
    <div class="empty">Waiting for traces...</div>
  </div>
  <div class="section-label">Services</div>
  <div class="health-bar" id="health-bar"></div>
</div>

<script>
let allTraces = [];
let expandedIds = new Set();
let currentFilter = '';
let renderedTraceIds = new Set();
let userFilterSignature = '';
let hasRenderedTraces = false;

function relativeTime(ts) {
  if (!ts) return '';
  const diff = (Date.now() / 1000) - ts;
  if (diff < 0) return 'just now';
  if (diff < 60) return Math.round(diff) + 's ago';
  if (diff < 3600) return Math.round(diff / 60) + 'm ago';
  if (diff < 86400) return Math.round(diff / 3600) + 'h ago';
  return Math.round(diff / 86400) + 'd ago';
}

function formatTime(ts) {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleTimeString('en-US', {hour12:false, hour:'2-digit', minute:'2-digit', second:'2-digit'});
}

function formatDuration(d) {
  if (!d || d <= 0) return '';
  if (d < 60) return d.toFixed(1) + 's';
  return (d / 60).toFixed(1) + 'min';
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

function toggleTrace(id) {
  if (expandedIds.has(id)) expandedIds.delete(id);
  else expandedIds.add(id);
  renderAll(allTraces);
}

function renderTimeline(checkpoints) {
  let html = '<div class="timeline">';
  for (const c of checkpoints) {
    const dotClass = c.status;
    const labelClass = c.status === 'pending' ? 'pending' : c.status === 'skipped' ? 'skipped' : c.status === 'active' ? 'active' : '';
    const timeStr = c.timestamp ? formatTime(c.timestamp) : '';

    html += '<div class="tl-node">';
    html += '<div class="tl-dot ' + dotClass + '"></div>';
    html += '<div class="tl-header">';
    html += '<span class="tl-label ' + labelClass + '">' + escapeHtml(c.label) + '</span>';
    if (timeStr) html += '<span class="tl-time">' + timeStr + '</span>';
    html += '</div>';

    if (c.content && c.status !== 'pending') {
      const isResult = c.name === 'result';
      html += '<div class="tl-content' + (isResult ? ' result-content' : '') + '">' + escapeHtml(c.content) + '</div>';
    }
    if (c.meta && typeof c.meta === 'object' && Object.keys(c.meta).length) {
      html += '<div class="tl-meta">' + escapeHtml(JSON.stringify(c.meta)) + '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function renderTrace(t, forceExpand, isNew) {
  const isActive = t.checkpoints.some(c => c.status === 'active');
  const hasError = t.checkpoints.some(c => c.status === 'error');
  const isExpanded = forceExpand || expandedIds.has(t.trace_id);
  const sourceClass = (t.source || 'voice').toLowerCase().replace(/[^a-z-]/g, '');
  const statusIcon = isActive
    ? '<span class="trace-status-icon active">◐</span>'
    : hasError
      ? '<span class="trace-status-icon error">!</span>'
      : '<span class="trace-status-icon done">✓</span>';
  const durationStr = t.duration > 0 ? formatDuration(t.duration) : '';

  let cardClass = 'trace-card' + (isActive ? ' active-trace' : '') + (isNew ? ' new-trace' : '');
  let html = '<div class="' + cardClass + '" data-id="' + t.trace_id + '">';

  // Summary row
  html += '<div class="trace-summary" onclick="toggleTrace(\'' + t.trace_id + '\')">';
  html += '<span class="trace-expand ' + (isExpanded ? 'open' : '') + '">▶</span>';
  html += '<span class="trace-source ' + sourceClass + '">' + escapeHtml(t.source || 'voice') + '</span>';
  html += '<span class="trace-user">@' + escapeHtml(t.user || '?') + '</span>';
  html += '<span class="trace-text">' + escapeHtml(t.text_preview) + '</span>';
  html += '<span class="trace-meta">';
  if (durationStr) html += '<span class="trace-duration">' + durationStr + '</span>';
  html += statusIcon;
  html += '<span class="trace-time">' + relativeTime(t.created_at) + '</span>';
  html += '</span>';
  html += '</div>';

  // Detail timeline
  if (isExpanded) {
    html += '<div class="trace-detail open">';
    html += renderTimeline(t.checkpoints);
    html += '</div>';
  }

  html += '</div>';
  return html;
}

function applyFilter() {
  currentFilter = document.getElementById('user-filter').value;
  renderAll(allTraces);
}

function renderAll(traceData) {
  allTraces = traceData;

  // Update user filter options only when the set changes. Rebuilding the
  // select on every poll causes a visible twitch in some browsers.
  const users = [...new Set(traceData.map(t => t.user).filter(Boolean))].sort();
  const sel = document.getElementById('user-filter');
  const prev = sel.value;
  const userSignature = users.join('\n');
  if (userSignature !== userFilterSignature) {
    while (sel.options.length > 1) sel.remove(1);
    users.forEach(u => {
      const opt = document.createElement('option');
      opt.value = u; opt.textContent = '@' + u;
      if (u === prev) opt.selected = true;
      sel.appendChild(opt);
    });
    userFilterSignature = userSignature;
  }
  currentFilter = sel.value;

  const filtered = currentFilter ? traceData.filter(t => t.user === currentFilter) : traceData;
  const active = filtered.filter(t => t.checkpoints.some(c => c.status === 'active'));
  const completed = filtered.filter(t => !t.checkpoints.some(c => c.status === 'active'));

  const activeEl = document.getElementById('active-section');
  const historyEl = document.getElementById('history-section');

  // Active traces always expanded
  if (active.length > 0) {
    activeEl.innerHTML = '<div class="section-label">Active <span class="count">(' + active.length + ')</span></div>' +
      active.map(t => renderTrace(t, true, hasRenderedTraces && !renderedTraceIds.has(t.trace_id))).join('');
  } else {
    activeEl.innerHTML = '';
  }

  // History traces collapsible
  if (completed.length > 0) {
    historyEl.innerHTML = '<div class="section-label">History <span class="count">(' + completed.length + ')</span></div>' +
      completed.map(t => renderTrace(t, false, hasRenderedTraces && !renderedTraceIds.has(t.trace_id))).join('');
  } else if (active.length === 0) {
    historyEl.innerHTML = '<div class="empty">No traces yet. Send a task via voice to see the pipeline.</div>';
  } else {
    historyEl.innerHTML = '';
  }

  renderedTraceIds = new Set(traceData.map(t => t.trace_id));
  hasRenderedTraces = true;

  // Badges
  const badge = document.getElementById('status-badge');
  if (active.length > 0) {
    badge.textContent = active.length + ' active';
    badge.className = 'badge running';
  } else {
    badge.textContent = 'idle';
    badge.className = 'badge idle';
  }
  document.getElementById('count-badge').textContent = traceData.length + ' traces';
}

function renderHealth(data) {
  const bar = document.getElementById('health-bar');
  const items = [];
  if (data.heartbeats) {
    for (const h of data.heartbeats) {
      const cls = h.ok ? 'ok' : (h.age_s > 300 ? 'bad' : 'stale');
      items.push('<div class="health-item"><div class="health-dot ' + cls + '"></div>' + h.name + '</div>');
    }
  }
  if (data.core_status) {
    const cs = data.core_status;
    const cls = cs.status === 'running' ? 'ok' : 'stale';
    const label = cs.status === 'running' ? 'Core: ' + (cs.step || 'working') : 'Core: idle';
    items.push('<div class="health-item"><div class="health-dot ' + cls + '"></div>' + label + '</div>');
  }
  if (data.core_peek) {
    const p = data.core_peek;
    const cls = p.alive ? 'ok' : 'bad';
    let label = 'Claude: ' + (p.alive ? (p.state || 'alive') : 'dead');
    if (p.cpu) label += ' CPU ' + p.cpu + '%';
    if (p.https_connected !== undefined) label += ' HTTPS ' + (p.https_connected ? 'yes' : 'no');
    if (p.line) label += ' — ' + p.line;
    items.push('<div class="health-item" title="' + escapeHtml(JSON.stringify(p)) + '"><div class="health-dot ' + cls + '"></div>' + escapeHtml(label) + '</div>');
  }
  if (data.watcher_live !== undefined) {
    items.push('<div class="health-item"><div class="health-dot ' + (data.watcher_live ? 'ok' : 'bad') + '"></div>watch-tasks</div>');
    items.push('<div class="health-item"><div class="health-dot ' + (data.sutando_core_live ? 'ok' : 'bad') + '"></div>sutando-core</div>');
  }
  if (data.owner_activity && data.owner_activity.ts) {
    const ago = relativeTime(data.owner_activity.ts);
    items.push('<div class="health-item"><div class="health-dot ok"></div>Owner: ' + (data.owner_activity.channel || '?') + ' ' + ago + '</div>');
  }
  bar.innerHTML = items.join('') || '<div class="health-item" style="color:#333">No services detected</div>';
}

// SSE
let es;
function connectSSE() {
  es = new EventSource('/sse');
  const connEl = document.getElementById('conn-status');
  es.onopen = function() { connEl.className = 'connected-indicator'; connEl.title = 'SSE connected'; };
  es.addEventListener('traces', function(e) {
    try { renderAll(JSON.parse(e.data)); } catch(err) { console.error('traces parse error', err); }
  });
  es.addEventListener('health', function(e) {
    try { renderHealth(JSON.parse(e.data)); } catch(err) { console.error('health parse error', err); }
  });
  es.onerror = function() { connEl.className = 'disconnected-indicator'; connEl.title = 'SSE disconnected'; };
}

// Refresh relative times every 10s
setInterval(function() {
  document.querySelectorAll('.trace-time').forEach(function(el) {
    // re-render handled by next SSE push
  });
}, 10000);

fetch('/api/traces').then(r => r.json()).then(renderAll).catch(() => {});
fetch('/api/health').then(r => r.json()).then(renderHealth).catch(() => {});
connectSSE();
</script>
</body></html>"""


# --- HTTP Server ---

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", ""):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode())

        elif path == "/sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            q: queue.Queue = queue.Queue()
            with sse_lock:
                sse_clients.append(q)

            try:
                with traces_lock:
                    all_t = [t.to_dict() for t in sorted(
                        traces.values(), key=lambda t: t.created_at, reverse=True
                    )]
                self.wfile.write(f"event: traces\ndata: {json.dumps(all_t, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()

                last_heartbeat = time.time()
                while True:
                    try:
                        event_type, data = q.get(timeout=1.0)
                        self.wfile.write(f"event: {event_type}\ndata: {data}\n\n".encode())
                        self.wfile.flush()
                    except queue.Empty:
                        if time.time() - last_heartbeat > HEARTBEAT_INTERVAL:
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                            last_heartbeat = time.time()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with sse_lock:
                    if q in sse_clients:
                        sse_clients.remove(q)

        elif path == "/api/traces":
            with traces_lock:
                all_t = [t.to_dict() for t in sorted(
                    traces.values(), key=lambda t: t.created_at, reverse=True
                )]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(all_t, ensure_ascii=False).encode())

        elif path == "/api/health":
            heartbeats = read_heartbeats()
            activity = read_owner_activity()
            core = read_core_status()
            data = {
                "heartbeats": heartbeats,
                "owner_activity": activity,
                "core_status": core,
                "core_peek": read_core_peek(),
                "watcher_live": _pgrep("watch-tasks"),
                "sutando_core_live": _pgrep("claude.*sutando-core")
                or _pgrep("claude.*--name.*sutando-core"),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    t = threading.Thread(target=collector_loop, daemon=True)
    t.start()
    t2 = threading.Thread(target=health_broadcast_loop, daemon=True)
    t2.start()

    class QuietHTTPServer(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            import sys as _sys
            exc_type = _sys.exc_info()[0]
            if exc_type in (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                return  # Normal for SSE/keepalive connections — suppress
            super().handle_error(request, client_address)

    server = QuietHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Pipeline Trace → http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDone.")
