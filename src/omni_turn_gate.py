"""TurnGate — mutex, priority, cooldown for omni VoiceTrigger / PromptTrigger."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

TriggerKind = Literal["voice", "prompt"]
PromptReason = Literal["scene_change", "timer", "heartbeat", "manual", "work_result"]

PRIORITY = {
    "voice": 100,
    "manual": 80,
    "work_result": 70,
    "scene_change": 50,
    "heartbeat": 20,
    "timer": 10,
}


@dataclass
class TurnRequest:
    kind: TriggerKind
    reason: str = "voice"
    prompt_text: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class TurnGate:
    responding: bool = False
    voice_active: bool = False
    _last_fire: dict[str, float] = field(default_factory=dict)
    cooldowns_ms: dict[str, int] = field(
        default_factory=lambda: {
            "scene_change": 10_000,
            "timer": 5_000,
            "heartbeat": 1_000,
            "manual": 0,
            "work_result": 0,
            "voice": 0,
        }
    )

    def begin_response(self) -> None:
        self.responding = True

    def end_response(self) -> None:
        self.responding = False

    def allow(self, req: TurnRequest) -> tuple[bool, str]:
        if req.kind == "voice":
            return True, "ok"
        if self.voice_active:
            return False, "voice_active"
        if self.responding:
            return False, "busy"
        reason = req.reason or "manual"
        cd = self.cooldowns_ms.get(reason, 0)
        now = time.time()
        last = self._last_fire.get(reason, 0.0)
        if cd > 0 and (now - last) * 1000 < cd:
            return False, "cooldown"
        return True, "ok"

    def mark_fired(self, req: TurnRequest) -> None:
        reason = req.reason if req.kind == "prompt" else "voice"
        self._last_fire[reason] = time.time()
