#!/usr/bin/env python3
"""core_readiness.py — alive vs ready-for-work for sutando-core.

`.alive` only means the heartbeat process is fresh. Omni work needs the core
to have finished (or skipped) `/startup` so it will claim `tasks/task-*.txt`
instead of deferring them behind schedule-crons.

Sentinels (under `<workspace>/state/`):
  core-booting.json  — written by start-cli at launch; cleared by mark-ready
  core-ready.json    — written when boot ceremony finishes (or skip-startup)

Ready = alive AND not booting (booting file absent or stale) AND
        (ready stamp present OR streaming watcher pid alive).

CLI:
  python3 src/core_readiness.py mark-booting [--reason TEXT]
  python3 src/core_readiness.py mark-ready
  python3 src/core_readiness.py probe [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

BOOTING_FILE = "core-booting.json"
READY_FILE = "core-ready.json"
# Booting older than this is ignored (crashed mid-/startup without clearing).
BOOTING_STALE_S = 15 * 60
DEFAULT_ALIVE_MAX_AGE_S = 90.0

_PANE_BOOT_MARKERS = (
    "/startup",
    "schedule-crons",
    "Skill(schedule-crons)",
    "task-orphan-check",
    "I'll continue schedule-crons",
    "complete startup before processing",
)


def _workspace_default() -> Path:
    import sys

    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    from workspace_default import resolve_workspace

    return Path(resolve_workspace())


def _host_label() -> str:
    import subprocess

    try:
        r = subprocess.run(
            ["bash", str(Path(__file__).resolve().parent.parent / "scripts" / "sutando-config.sh"), "host-label"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return os.uname().nodename.split(".")[0]


def booting_path(workspace: Path) -> Path:
    return Path(workspace) / "state" / BOOTING_FILE


def ready_path(workspace: Path) -> Path:
    return Path(workspace) / "state" / READY_FILE


def watcher_pid_path(workspace: Path) -> Path:
    return Path(workspace) / "state" / "watch-tasks-stream.pid"


def alive_path(workspace: Path, host: str | None = None) -> Path:
    return Path(workspace) / "state" / "cores" / f"{host or _host_label()}.alive"


def mark_booting(workspace: Path, *, reason: str = "startup") -> Path:
    path = booting_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reason": reason,
        "schema_version": 1,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    # Drop stale ready so readers don't treat a previous boot as current.
    try:
        ready_path(workspace).unlink(missing_ok=True)
    except TypeError:
        rp = ready_path(workspace)
        if rp.exists():
            rp.unlink()
    return path


def mark_ready(workspace: Path, *, source: str = "startup") -> Path:
    path = ready_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "schema_version": 1,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    bp = booting_path(workspace)
    try:
        bp.unlink(missing_ok=True)
    except TypeError:
        if bp.exists():
            bp.unlink()
    return path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def watcher_alive(workspace: Path) -> bool:
    path = watcher_pid_path(workspace)
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return _pid_alive(int(raw))
    except Exception:
        return False


def read_booting(workspace: Path) -> dict[str, Any] | None:
    path = booting_path(workspace)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        age = max(0.0, time.time() - path.stat().st_mtime)
        data = dict(data)
        data["_age_s"] = age
        data["_stale"] = age > BOOTING_STALE_S
        return data
    except Exception:
        return None


def pane_looks_booting(pane_text: str) -> bool:
    if not pane_text:
        return False
    lower = pane_text.lower()
    return any(m.lower() in lower for m in _PANE_BOOT_MARKERS)


def probe_core_readiness(
    workspace: Path | None = None,
    *,
    host: str | None = None,
    alive_max_age_s: float = DEFAULT_ALIVE_MAX_AGE_S,
    pane_text: str | None = None,
) -> dict[str, Any]:
    """Return alive / ready / booting signals for HUD and pipeline."""
    ws = Path(workspace) if workspace is not None else _workspace_default()
    host = host or _host_label()
    ap = alive_path(ws, host)
    age_s: float | None = None
    alive = False
    if ap.is_file():
        try:
            age_s = max(0.0, time.time() - ap.stat().st_mtime)
            alive = age_s <= alive_max_age_s
        except OSError:
            pass

    boot = read_booting(ws)
    file_booting = bool(boot) and not boot.get("_stale")
    watch = watcher_alive(ws)
    ready_stamp = ready_path(ws).is_file()
    # Pane markers are a fallback only when no ready stamp yet — capture-pane
    # scrollback often still contains "/startup" after boot finished.
    pane_boot = (not ready_stamp) and pane_looks_booting(pane_text or "")
    booting = file_booting or pane_boot

    reason = "ok"
    ready = False
    if not alive:
        reason = "core_down"
    elif file_booting:
        reason = "booting"
        ready = False
    elif ready_stamp or watch:
        ready = True
        reason = "ready"
    elif pane_boot:
        reason = "booting"
        ready = False
    else:
        # Alive but never marked ready and no watcher — treat as not ready
        # (typical mid-/startup before mark-ready, or skip-startup race).
        reason = "not_ready"
        ready = False

    return {
        "alive": alive,
        "ready": ready,
        "booting": booting or pane_boot,
        "reason": reason,
        "age_s": None if age_s is None else round(age_s, 1),
        "watcher_alive": watch,
        "ready_stamp": ready_stamp,
        "booting_age_s": None if not boot else round(float(boot.get("_age_s") or 0), 1),
        "host": host,
        "workspace": str(ws),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mark/probe sutando-core readiness")
    ap.add_argument(
        "action",
        choices=("mark-booting", "mark-ready", "probe"),
        help="write booting sentinel, write ready, or print probe",
    )
    ap.add_argument("--reason", default="startup", help="booting reason")
    ap.add_argument("--source", default="startup", help="ready source label")
    ap.add_argument("--workspace", default="", help="override workspace path")
    ap.add_argument("--json", action="store_true", help="JSON probe output")
    args = ap.parse_args(argv)

    ws = Path(args.workspace) if args.workspace else _workspace_default()
    if args.action == "mark-booting":
        p = mark_booting(ws, reason=args.reason)
        print(f"core-booting → {p}")
        return 0
    if args.action == "mark-ready":
        p = mark_ready(ws, source=args.source)
        print(f"core-ready → {p}")
        return 0

    result = probe_core_readiness(ws)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"alive={result['alive']} ready={result['ready']} "
            f"booting={result['booting']} reason={result['reason']} "
            f"watcher={result['watcher_alive']}"
        )
    return 0 if result.get("alive") else 2


if __name__ == "__main__":
    raise SystemExit(main())
