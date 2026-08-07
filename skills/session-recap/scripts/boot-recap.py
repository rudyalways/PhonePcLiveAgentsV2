#!/usr/bin/env python3
"""Fast boot session-recap — no LLM, capped dialog dump, mark-ready first.

Called from /schedule-crons step 5.6 so boot catchup is seconds, not minutes.
Deep LLM recap remains on-demand via `/session-recap`.

Exit codes:
  0 — wrote recap, skipped (idempotent), or nothing to recap
  2 — pending owner tasks in tasks/; caller must yield (do not dump)
  1 — hard failure
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(SCRIPT_DIR))
import extract as ex  # noqa: E402

DEFAULT_MAX_CHARS = 48_000
HEAD_LINES = 40
TAIL_LINES = 40


def _workspace() -> Path:
    r = subprocess.run(
        ["bash", str(REPO / "scripts" / "sutando-config.sh"), "workspace"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(r.stdout.strip())


def _pending_tasks(ws: Path) -> list[Path]:
    tasks = ws / "tasks"
    if not tasks.is_dir():
        return []
    return sorted(tasks.glob("task-*.txt"), key=lambda p: p.stat().st_mtime)


def _mark_ready(source: str) -> None:
    subprocess.run(
        [sys.executable, str(REPO / "src" / "core_readiness.py"), "mark-ready",
         "--source", source],
        cwd=str(REPO),
        check=False,
        timeout=30,
    )


def _dump_dialog(path: Path, max_chars: int) -> str:
    out: list[str] = []
    total = 0
    with open(path, errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            t, ts = d.get("type"), (d.get("timestamp") or "")[:19]
            piece = None
            if t == "user":
                txt = ex.text_of(d.get("message", {}).get("content")).strip()
                if txt:
                    piece = f"[{ts}] USER: {txt}"
            elif t == "assistant":
                txt = ex.text_of(d.get("message", {}).get("content")).strip()
                if txt:
                    piece = f"[{ts}] ASSISTANT: {txt}"
            if not piece:
                continue
            total += len(piece)
            if max_chars and total > max_chars:
                out.append(
                    f"...[truncated at {max_chars} chars for boot speed; "
                    "run /session-recap for a full LLM summary]"
                )
                break
            out.append(piece)
    return "\n".join(out)


def _thin_summary(meta: dict, dump: str) -> str:
    lines = [ln for ln in dump.splitlines() if ln.strip()]
    users = [ln for ln in lines if "] USER:" in ln]
    assistants = [ln for ln in lines if "] ASSISTANT:" in ln]
    head = lines[:HEAD_LINES]
    tail = lines[-TAIL_LINES:] if len(lines) > HEAD_LINES + TAIL_LINES else []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [
        f"# Last session recap (boot-fast)",
        "",
        f"_Generated {now} by boot-recap.py — mechanical extract, no LLM. "
        f"For a deep summary run `/session-recap`._",
        "",
        "## Executive summary",
        f"- Previous session file: `{meta.get('file')}`",
        f"- Window: {meta.get('start')} → {meta.get('end')}",
        f"- Messages: user={meta.get('user_msgs')} assistant={meta.get('assistant_msgs')} "
        f"size≈{meta.get('size_kb')}KB",
        f"- First user line: {meta.get('first_user') or '(none)'}",
        f"- Dialog lines in boot extract: {len(lines)} "
        f"(user={len(users)}, assistant={len(assistants)})",
        "",
        "## Opening (first dialog slice)",
        "```",
        *head,
        "```",
    ]
    if tail:
        parts += ["", "## Closing (last dialog slice)", "```", *tail, "```"]
    parts += [
        "",
        "## Recommended next actions",
        "- Process any pending `tasks/task-*.txt` before optional deep `/session-recap`.",
        "- Use `/session-recap last` if you need architecture decisions / PR detail "
        "beyond this extract.",
        "",
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--max-chars",
        type=int,
        default=int(os.environ.get("SUTANDO_SESSION_RECAP_BOOT_MAX_CHARS") or DEFAULT_MAX_CHARS),
    )
    ap.add_argument("--skip-mark-ready", action="store_true")
    args = ap.parse_args(argv)

    ws = _workspace()
    pending = _pending_tasks(ws)
    if pending:
        print(
            f"boot-recap: {len(pending)} pending task(s) — yield first "
            f"(e.g. {pending[0].name})",
            file=sys.stderr,
        )
        return 2

    if not args.skip_mark_ready:
        _mark_ready("startup-before-recap")

    tdir = ex.transcripts_dir()
    sessions = sorted(tdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not sessions:
        print("boot-recap: no transcripts — skip")
        return 0

    try:
        path = ex.pick(sessions, "last")
    except SystemExit as e:
        print(f"boot-recap: {e} — skip")
        return 0

    stamp = ws / "state" / "last-recap-session.txt"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    sid = path.stem
    if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == sid:
        print(f"boot-recap: already stamped {sid} — skip")
        return 0

    meta = ex.session_meta(path)
    dump = _dump_dialog(path, args.max_chars)
    body = _thin_summary(meta, dump)
    out = ws / "state" / "last-session-recap.md"
    out.write_text(body, encoding="utf-8")
    stamp.write_text(sid + "\n", encoding="utf-8")
    print(f"boot-recap: wrote {out} ({len(body)} chars) session={sid}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired:
        print("boot-recap: mark-ready timed out", file=sys.stderr)
        raise SystemExit(1)
