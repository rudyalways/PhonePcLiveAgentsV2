#!/usr/bin/env python3
"""
Monitor the LiveKit result-watcher heartbeat.

The result watcher runs inside each livekit-agent job and writes
state/livekit-result-watcher.heartbeat every few seconds. This monitor is a
small supervisor/observer: it does not restart anything, but it emits pipeline
events when the watcher heartbeat is missing, stale, or recovers.

Usage:
  python3 src/livekit-result-watcher-monitor.py
  python3 src/livekit-result-watcher-monitor.py --once

Environment:
  RESULT_WATCHER_MONITOR_INTERVAL_S       poll interval, default 20
  RESULT_WATCHER_MONITOR_STALE_S          stale threshold, default 30
  RESULT_WATCHER_MONITOR_REPEAT_ALERT_S   repeat unhealthy event interval, default 300
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
STATE_DIR = REPO / "state"
WATCHER_HEARTBEAT = STATE_DIR / "livekit-result-watcher.heartbeat"
MONITOR_HEARTBEAT = STATE_DIR / "livekit-result-watcher-monitor.heartbeat"

INTERVAL_S = float(os.environ.get("RESULT_WATCHER_MONITOR_INTERVAL_S", "20"))
STALE_S = float(os.environ.get("RESULT_WATCHER_MONITOR_STALE_S", "30"))
REPEAT_ALERT_S = float(os.environ.get("RESULT_WATCHER_MONITOR_REPEAT_ALERT_S", "300"))


try:
    sys.path.insert(0, str(REPO / "src"))
    from pipeline_emit import emit as pipeline_emit
except Exception:  # pragma: no cover - best effort fallback for early startup
    pipeline_emit = None


def _now() -> float:
    return time.time()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}


def _pid_alive(pid: Any) -> bool:
    try:
        n = int(pid)
        if n <= 0:
            return False
        os.kill(n, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _write_monitor_heartbeat(status: str, detail: str = "") -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": int(_now()),
        "pid": os.getpid(),
        "status": status,
    }
    if detail:
        payload["detail"] = detail[:500]
    tmp = MONITOR_HEARTBEAT.with_suffix(".heartbeat.tmp")
    tmp.write_text(json.dumps(payload))
    tmp.rename(MONITOR_HEARTBEAT)


def _emit(phase: str, *, ok: bool, detail: str, meta: dict[str, Any] | None = None) -> None:
    if pipeline_emit is None:
        return
    pipeline_emit(
        phase=phase,
        task_id="watcher-livekit-result",
        ok=ok,
        detail=detail,
        component="livekit-result-watcher-monitor",
        meta=meta or {},
    )


def _status_from_heartbeat(now: float) -> tuple[str, bool, str, dict[str, Any]]:
    hb = _read_json(WATCHER_HEARTBEAT)
    if hb is None:
        return (
            "missing",
            False,
            f"{WATCHER_HEARTBEAT.relative_to(REPO)} missing",
            {},
        )

    if "_error" in hb:
        return (
            "unreadable",
            False,
            f"{WATCHER_HEARTBEAT.relative_to(REPO)} unreadable: {hb['_error']}",
            {"error": hb["_error"]},
        )

    ts = float(hb.get("ts") or 0)
    age = now - ts if ts else float("inf")
    pid = hb.get("pid")
    pid_alive = _pid_alive(pid)
    username = str(hb.get("username") or "")
    pending = hb.get("pending")
    watcher_status = str(hb.get("status") or "unknown")

    meta = {
        "heartbeat_ts": ts,
        "age_s": round(age, 1) if age != float("inf") else None,
        "pid": pid,
        "pid_alive": pid_alive,
        "username": username,
        "pending": pending,
        "watcher_status": watcher_status,
    }

    if age > STALE_S:
        if pid_alive:
            detail = (
                f"heartbeat stale for {int(age)}s; writer pid {pid} alive; "
                f"user={username or '?'} pending={pending}"
            )
        else:
            detail = (
                f"heartbeat stale for {int(age)}s; writer pid {pid} is gone; "
                "likely idle until a new LiveKit job starts"
            )
        return "stale", False, detail, meta

    if pid and not pid_alive:
        return (
            "pid_gone",
            False,
            f"heartbeat is fresh but writer pid {pid} is gone",
            meta,
        )

    return (
        "ok",
        True,
        f"heartbeat fresh ({int(age)}s old); user={username or '?'} pending={pending}",
        meta,
    )


def run_once(last_status: str | None, last_alert_ts: float) -> tuple[str, float]:
    now = _now()
    status, ok, detail, meta = _status_from_heartbeat(now)
    _write_monitor_heartbeat("ok" if ok else status, detail)

    phase = "livekit_result_watcher_recovered" if ok else "livekit_result_watcher_unhealthy"
    should_emit = status != last_status
    if not ok and (now - last_alert_ts) >= REPEAT_ALERT_S:
        should_emit = True

    if should_emit:
        _emit(phase, ok=ok, detail=detail, meta=meta)
        if not ok:
            last_alert_ts = now

    return status, last_alert_ts


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor LiveKit result-watcher heartbeat.")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    args = parser.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _write_monitor_heartbeat("starting")
    _emit(
        "livekit_result_watcher_monitor_started",
        ok=True,
        detail=f"monitor pid {os.getpid()}, interval={INTERVAL_S}s stale={STALE_S}s",
        meta={"pid": os.getpid(), "interval_s": INTERVAL_S, "stale_s": STALE_S},
    )

    stop = False

    def _handle_signal(signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True
        _write_monitor_heartbeat("stopping", f"signal {signum}")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    last_status: str | None = None
    last_alert_ts = 0.0

    while not stop:
        last_status, last_alert_ts = run_once(last_status, last_alert_ts)
        if args.once:
            break
        time.sleep(INTERVAL_S)

    _write_monitor_heartbeat("stopped" if stop else "ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
