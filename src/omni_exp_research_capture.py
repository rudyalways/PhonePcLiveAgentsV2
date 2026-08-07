"""Research / whiteboard meeting session buffer (omni-exp).

Audio-first capture for topics, todos, research follow-ups, and summary bullets.
See docs/omni-exp-whiteboard-meeting-capture.md.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

FlushKind = Literal["capture-flush", "deep", "none"]

logger = logging.getLogger("omni-exp-research-capture")

# EN + ZH cues for meeting / whiteboard talk.
_TODO_RE = re.compile(
    r"\b(todo|to-?do|action\s*items?|follow[- ]?ups?|next\s+steps?|we\s+should|"
    r"i'?ll\s+(send|do|write|fix)|assign)\b|"
    r"(待办|跟进|下一步|行动项|我来|你来|记得|别忘了)",
    re.I,
)
_RESEARCH_RE = re.compile(
    r"\b(research|look\s+into|investigate|deep\s*dive|competitor|sota)\b|"
    r"(研究|查一下|调研|竞品|论文|跟进研究)",
    re.I,
)
_SUMMARY_RE = re.compile(
    r"\b(summary|summarize|recap|wrap\s*up|takeaways?)\b|"
    r"(总结|回顾|纪要|要点)",
    re.I,
)
_DEEP_RE = re.compile(
    r"\b(deep\s*research|full\s*deck|make\s+(a\s+)?(deck|ppt|slides?)|"
    r"html\s*deck|auto[- ]?play)\b|"
    r"(深度研究|做个?deck|做个?ppt|幻灯片|自动播放)",
    re.I,
)


@dataclass
class CaptureHook:
    kind: str  # topic | todo | research | note | summary
    text: str
    source: str  # asr | scene | ink | ocr | scan
    ts: float = field(default_factory=time.time)


@dataclass
class ResearchSessionBuffer:
    """In-memory meeting capture buffer for one omni phone session."""

    max_asr: int = 40
    max_hooks: int = 30
    flush_idle_s: float = 90.0
    flush_min_interval_s: float = 45.0
    min_hooks_for_idle_flush: int = 2
    # Flush after this many new hooks even before idle (debounced by min interval).
    flush_hook_count: int = 5

    asr_lines: list[tuple[float, str]] = field(default_factory=list)
    hooks: list[CaptureHook] = field(default_factory=list)
    scene_notes: list[tuple[float, str]] = field(default_factory=list)
    last_flush_at: float = 0.0
    last_append_at: float = 0.0
    hooks_since_flush: int = 0
    schema_version: int = 1

    def add_asr(self, text: str) -> list[CaptureHook]:
        """Append final ASR; return any new hooks inferred from cues."""
        t = (text or "").strip()
        if not t:
            return []
        now = time.time()
        self.asr_lines.append((now, t))
        if len(self.asr_lines) > self.max_asr:
            self.asr_lines = self.asr_lines[-self.max_asr :]
        self.last_append_at = now
        new_hooks = self._hooks_from_utterance(t, source="asr")
        for h in new_hooks:
            self._push_hook(h)
        return new_hooks

    def add_scene_note(self, text: str, *, source: str = "scene") -> None:
        t = (text or "").strip()
        if not t:
            return
        now = time.time()
        self.scene_notes.append((now, t[:240]))
        if len(self.scene_notes) > 20:
            self.scene_notes = self.scene_notes[-20:]
        self.last_append_at = now
        self._push_hook(CaptureHook(kind="note", text=t[:240], source=source, ts=now))

    def add_ink_note(self, *, ocr_text: str = "") -> None:
        """Board-ink delta note (optional OCR snippet)."""
        if ocr_text.strip():
            self.add_scene_note(f"board ink/OCR: {ocr_text.strip()[:220]}", source="ocr")
        else:
            self.add_scene_note("board ink change detected (camera)", source="ink")

    def _push_hook(self, hook: CaptureHook) -> None:
        # Dedupe exact recent text.
        for prev in self.hooks[-8:]:
            if prev.kind == hook.kind and prev.text == hook.text:
                return
        self.hooks.append(hook)
        if len(self.hooks) > self.max_hooks:
            self.hooks = self.hooks[-self.max_hooks :]
        self.hooks_since_flush += 1

    def _hooks_from_utterance(self, text: str, *, source: str) -> list[CaptureHook]:
        out: list[CaptureHook] = []
        if _TODO_RE.search(text):
            out.append(CaptureHook(kind="todo", text=text, source=source))
        if _RESEARCH_RE.search(text):
            out.append(CaptureHook(kind="research", text=text, source=source))
        if _SUMMARY_RE.search(text):
            out.append(CaptureHook(kind="summary", text=text, source=source))
        # Generic topic if sentence looks substantive and no other cue.
        if not out and len(text) >= 12:
            out.append(CaptureHook(kind="topic", text=text, source=source))
        return out

    def wants_deep(self, text: str) -> bool:
        return bool(_DEEP_RE.search(text or ""))

    def should_flush(self, *, force: bool = False, deep: bool = False) -> FlushKind:
        now = time.time()
        if deep:
            return "deep"
        if force and self.hooks_since_flush > 0:
            if now - self.last_flush_at < 5.0:
                return "none"
            return "capture-flush"
        if self.hooks_since_flush <= 0:
            return "none"
        if now - self.last_flush_at < self.flush_min_interval_s:
            return "none"
        # Cue-driven: any todo/research/summary since last flush → flush when interval ok.
        recent = self.hooks[-self.hooks_since_flush :]
        if any(h.kind in ("todo", "research", "summary") for h in recent):
            return "capture-flush"
        # Enough new hooks (topics/notes/ink) → flush without waiting for idle.
        if self.hooks_since_flush >= self.flush_hook_count:
            return "capture-flush"
        # Idle flush: content sitting unused.
        if (
            self.hooks_since_flush >= self.min_hooks_for_idle_flush
            and self.last_append_at
            and now - self.last_append_at >= self.flush_idle_s
        ):
            return "capture-flush"
        return "none"

    def build_flush_task(self, kind: FlushKind) -> str:
        """Task body for enqueue_work."""
        topics = [h.text for h in self.hooks if h.kind == "topic"][-8:]
        todos = [h.text for h in self.hooks if h.kind == "todo"][-8:]
        research = [h.text for h in self.hooks if h.kind == "research"][-8:]
        notes = [h.text for h in self.hooks if h.kind == "note"][-8:]
        summary = [h.text for h in self.hooks if h.kind == "summary"][-6:]
        asr_tail = [t for _, t in self.asr_lines[-12:]]
        scene_tail = [t for _, t in self.scene_notes[-6:]]

        def _bullets(label: str, items: list[str]) -> str:
            if not items:
                return f"{label}: (none)\n"
            return label + ":\n" + "\n".join(f"- {x}" for x in items) + "\n"

        body = (
            f"[research-{kind}] Whiteboard/meeting capture flush.\n"
            + _bullets("topics", topics)
            + _bullets("todos", todos)
            + _bullets("research_followups", research)
            + _bullets("notes", notes)
            + _bullets("summary_cues", summary)
            + _bullets("recent_asr", asr_tail)
            + _bullets("scene_notes", scene_tail)
        )
        if kind == "capture-flush":
            body += (
                "Action: update meeting notes Markdown only "
                "(workspace/data/omni-research/meeting-YYYYMMDD.md); "
                "short spoken result. Do NOT build the HTML research deck.\n"
            )
        else:
            body += (
                "Action: run full research-deep pipeline on the primary research "
                "follow-ups (MD then Chinese auto-play HTML deck).\n"
            )
        return body.strip()

    def mark_flushed(self) -> None:
        self.last_flush_at = time.time()
        self.hooks_since_flush = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "asr": len(self.asr_lines),
            "hooks": len(self.hooks),
            "hooks_since_flush": self.hooks_since_flush,
            "scene_notes": len(self.scene_notes),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "saved_at": time.time(),
            "flush_idle_s": self.flush_idle_s,
            "flush_min_interval_s": self.flush_min_interval_s,
            "flush_hook_count": self.flush_hook_count,
            "min_hooks_for_idle_flush": self.min_hooks_for_idle_flush,
            "last_flush_at": self.last_flush_at,
            "last_append_at": self.last_append_at,
            "hooks_since_flush": self.hooks_since_flush,
            "asr_lines": [[ts, t] for ts, t in self.asr_lines],
            "scene_notes": [[ts, t] for ts, t in self.scene_notes],
            "hooks": [
                {"kind": h.kind, "text": h.text, "source": h.source, "ts": h.ts}
                for h in self.hooks
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchSessionBuffer:
        buf = cls(
            flush_idle_s=float(data.get("flush_idle_s") or 90.0),
            flush_min_interval_s=float(data.get("flush_min_interval_s") or 45.0),
            flush_hook_count=int(data.get("flush_hook_count") or 5),
            min_hooks_for_idle_flush=int(data.get("min_hooks_for_idle_flush") or 2),
        )
        buf.last_flush_at = float(data.get("last_flush_at") or 0.0)
        buf.last_append_at = float(data.get("last_append_at") or 0.0)
        buf.hooks_since_flush = int(data.get("hooks_since_flush") or 0)
        for row in data.get("asr_lines") or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                buf.asr_lines.append((float(row[0]), str(row[1])))
        for row in data.get("scene_notes") or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                buf.scene_notes.append((float(row[0]), str(row[1])))
        for h in data.get("hooks") or []:
            if not isinstance(h, dict):
                continue
            buf.hooks.append(
                CaptureHook(
                    kind=str(h.get("kind") or "note"),
                    text=str(h.get("text") or ""),
                    source=str(h.get("source") or "asr"),
                    ts=float(h.get("ts") or time.time()),
                )
            )
        # Cap after load.
        buf.asr_lines = buf.asr_lines[-buf.max_asr :]
        buf.hooks = buf.hooks[-buf.max_hooks :]
        buf.scene_notes = buf.scene_notes[-20:]
        return buf


def research_capture_state_path(workspace: Path, username: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", (username or "anon").strip()) or "anon"
    return workspace / "state" / f"omni-research-capture-{safe}.json"


def load_research_buffer(
    workspace: Path,
    username: str,
    *,
    flush_idle_s: float,
    flush_min_interval_s: float,
    flush_hook_count: int = 5,
) -> ResearchSessionBuffer:
    path = research_capture_state_path(workspace, username)
    if not path.is_file():
        return ResearchSessionBuffer(
            flush_idle_s=flush_idle_s,
            flush_min_interval_s=flush_min_interval_s,
            flush_hook_count=flush_hook_count,
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        buf = ResearchSessionBuffer.from_dict(data)
        # Env knobs win over stale persisted values.
        buf.flush_idle_s = flush_idle_s
        buf.flush_min_interval_s = flush_min_interval_s
        buf.flush_hook_count = flush_hook_count
        logger.info(
            "loaded research capture buffer from %s (%s)",
            path,
            buf.snapshot(),
        )
        return buf
    except Exception as e:
        logger.warning("research capture load failed (%s): %s", path, e)
        return ResearchSessionBuffer(
            flush_idle_s=flush_idle_s,
            flush_min_interval_s=flush_min_interval_s,
            flush_hook_count=flush_hook_count,
        )


def save_research_buffer(workspace: Path, username: str, buf: ResearchSessionBuffer) -> None:
    path = research_capture_state_path(workspace, username)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(buf.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.warning("research capture save failed (%s): %s", path, e)
