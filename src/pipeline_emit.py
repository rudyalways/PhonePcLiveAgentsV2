#!/usr/bin/env python3
"""
Append structured pipeline events for Pipeline Trace UI (logs/pipeline-task-events.jsonl).

CLI (from shell / watch-tasks / tmux):
  python3 src/pipeline_emit.py <phase> [task_id] [--ok|--fail] [--detail TEXT] [--component NAME]

Python:
  from pipeline_emit import emit
  emit(phase="task_enqueued", task_id="task-123", ok=True, component="livekit-agent")
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIPELINE_LOG = REPO / "logs" / "pipeline-task-events.jsonl"


def emit(
    *,
    phase: str,
    task_id: str = "",
    ok: bool = True,
    detail: str = "",
    component: str = "",
    meta: dict | None = None,
) -> None:
    PIPELINE_LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.time(),
        "phase": phase,
        "task_id": task_id,
        "ok": ok,
        "detail": detail,
        "component": component,
        "meta": meta or {},
    }
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with PIPELINE_LOG.open("a", encoding="utf-8") as f:
        f.write(line)


def main() -> None:
    p = argparse.ArgumentParser(description="Append one pipeline trace event (JSONL).")
    p.add_argument("phase", help="event phase key, e.g. task_enqueued, watcher_pickup")
    p.add_argument("task_id", nargs="?", default="", help="task-… id if applicable")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--ok", dest="fail", action="store_false")
    g.add_argument("--fail", dest="fail", action="store_true")
    p.set_defaults(fail=False)
    p.add_argument("--detail", default="", help="human-readable detail")
    p.add_argument("--component", default="", help="emitter name")
    args = p.parse_args()
    emit(
        phase=args.phase,
        task_id=args.task_id,
        ok=not args.fail,
        detail=args.detail,
        component=args.component,
    )


if __name__ == "__main__":
    main()
