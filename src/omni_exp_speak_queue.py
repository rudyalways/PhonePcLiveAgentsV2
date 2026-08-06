"""Speak / PromptTrigger queue for omni — buffer while a response is in flight.

Matches docs/omni-exp-agent-design.md drain knobs for *prompt* backlog (work results,
nudges): while `responding`, enqueue; on idle / response.done, drain.

`merge` mirrors ready_merge for work-result speak prompts:
  - serial: one turn per item (default — start next when last finishes)
  - concat: one turn summarizing all buffered items
  - latest: drop older items; speak only the newest
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SpeakMerge = Literal["serial", "concat", "latest"]


@dataclass
class SpeakItem:
    reason: str
    prompt_text: str
    task_id: str | None = None
    preview: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class SpeakQueue:
    def __init__(self, merge: str = "serial", max_items: int = 32) -> None:
        m = (merge or "serial").strip().lower()
        if m not in ("serial", "concat", "latest"):
            m = "serial"
        self.merge: SpeakMerge = m  # type: ignore[assignment]
        self.max_items = max(1, int(max_items))
        self._items: list[SpeakItem] = []

    def __len__(self) -> int:
        return len(self._items)

    def push(self, item: SpeakItem) -> None:
        self._items.append(item)
        while len(self._items) > self.max_items:
            self._items.pop(0)

    def push_front(self, item: SpeakItem) -> None:
        self._items.insert(0, item)
        while len(self._items) > self.max_items:
            self._items.pop()

    def clear(self) -> None:
        self._items.clear()

    def take(self) -> SpeakItem | None:
        if not self._items:
            return None
        if self.merge == "latest":
            item = self._items[-1]
            dropped = len(self._items) - 1
            self._items.clear()
            if dropped:
                item.meta = {**item.meta, "dropped": dropped}
            return item
        if self.merge == "concat":
            items = self._items[:]
            self._items.clear()
            if len(items) == 1:
                return items[0]
            ids = [it.task_id or "?" for it in items]
            body = "\n\n---\n\n".join(it.prompt_text for it in items)
            return SpeakItem(
                reason=items[-1].reason,
                prompt_text=(
                    f"[System: {len(items)} core results ready — "
                    f"summarize briefly for the user.]\n\n{body}"
                )[:6000],
                task_id=",".join(ids)[:160],
                preview=f"{len(items)} results",
                meta={"merged": len(items)},
            )
        # serial
        return self._items.pop(0)
