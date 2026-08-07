#!/usr/bin/env python3
"""ResearchSessionBuffer + capture-flush stamp routing."""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from omni_exp_mode import (  # noqa: E402
    format_work_task,
    research_task_kind,
    task_system_suffix,
)
from omni_exp_research_capture import (  # noqa: E402
    ResearchSessionBuffer,
    load_research_buffer,
    research_capture_state_path,
    save_research_buffer,
)

failures: list[str] = []


def check(label: str, cond: bool) -> None:
    if cond:
        print(f"PASS  {label}")
    else:
        print(f"FAIL  {label}")
        failures.append(label)


buf = ResearchSessionBuffer(flush_idle_s=90, flush_min_interval_s=45)

hooks = buf.add_asr("待办：下周发方案")
check("zh todo cue", any(h.kind == "todo" for h in hooks))
check(
    "todo cue → capture-flush",
    buf.should_flush() == "capture-flush",
)
body = buf.build_flush_task("capture-flush")
check("flush tag capture-flush", "[research-capture-flush]" in body)
check("flush asks notes not deck", "Do NOT build the HTML research deck" in body)
buf.mark_flushed()
check("after flush no immediate re-flush", buf.should_flush() == "none")

buf2 = ResearchSessionBuffer(flush_min_interval_s=45)
buf2.add_asr("please look into competitor pricing")
check("en research cue", any(h.kind == "research" for h in buf2.hooks))
check("research cue flushes", buf2.should_flush() == "capture-flush")

buf3 = ResearchSessionBuffer()
buf3.add_asr("make a full deck about transformers")
check("deep cue detected", buf3.wants_deep(buf3.asr_lines[-1][1]))
check("deep flush kind", buf3.should_flush(deep=True) == "deep")
deep_body = buf3.build_flush_task("deep")
check("deep tag", "[research-deep]" in deep_body)
check("deep asks pipeline", "research-deep pipeline" in deep_body)

buf4 = ResearchSessionBuffer(flush_idle_s=1.0, flush_min_interval_s=0.0, min_hooks_for_idle_flush=2)
buf4.add_asr("we discussed auth tonight at length")  # topic
buf4.add_asr("and also the API redesign path")  # topic
buf4.last_append_at = time.time() - 2.0
check("idle flush with topics", buf4.should_flush() == "capture-flush")

buf5 = ResearchSessionBuffer()
buf5.add_scene_note("scene_change fired (camera)")
check("scene note hook", any(h.kind == "note" and h.source == "scene" for h in buf5.hooks))

# Stamp routing
flush_task = "[research-capture-flush] Whiteboard notes"
check("kind capture-flush", research_task_kind(flush_task) == "capture-flush")
stamp = task_system_suffix("research", flush_task)
check(
    "capture stamp no deck",
    "CAPTURE-FLUSH" in stamp and "Do NOT build or open an HTML deck" in stamp,
)
deep_stamp = task_system_suffix("research", "[research-mode] look into X")
check("deep stamp has HTML DECK", "HTML DECK" in deep_stamp)
check(
    "format keeps capture tag",
    format_work_task("research", flush_task) == flush_task,
)
check(
    "format keeps deep tag",
    format_work_task("research", "[research-deep] foo") == "[research-deep] foo",
)

# ≥ M hooks flush (debounced)
buf_m = ResearchSessionBuffer(flush_min_interval_s=0.0, flush_hook_count=3)
for i in range(3):
    buf_m.add_asr(f"we discussed topic number {i} in detail today")
check("M-hooks flush", buf_m.should_flush() == "capture-flush")

buf_ink = ResearchSessionBuffer()
buf_ink.add_ink_note(ocr_text="auth deadline Friday")
check("ink/OCR note", any(h.source == "ocr" for h in buf_ink.hooks))

# Persist round-trip
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    buf_p = ResearchSessionBuffer(flush_idle_s=12, flush_min_interval_s=34, flush_hook_count=7)
    buf_p.add_asr("待办：写纪要")
    buf_p.add_scene_note("scene_change fired (camera)")
    save_research_buffer(ws, "tester", buf_p)
    path = research_capture_state_path(ws, "tester")
    check("persist file exists", path.is_file())
    loaded = load_research_buffer(
        ws, "tester", flush_idle_s=12, flush_min_interval_s=34, flush_hook_count=7
    )
    check("persist restores ASR", any("待办" in t for _, t in loaded.asr_lines))
    check("persist restores hooks", any(h.kind == "todo" for h in loaded.hooks))
    check("persist keeps flush knobs", loaded.flush_hook_count == 7)

if failures:
    raise SystemExit(f"{len(failures)} failure(s): {', '.join(failures)}")
print("\nall tests passed")
