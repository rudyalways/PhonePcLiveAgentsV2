"""Scene-change sensor — thumb-diff on JPEG frames for PromptTrigger."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field

from PIL import Image


@dataclass
class SceneChangeSensor:
    """Local thumb-diff; upload/dedup policy stays in the agent."""

    enter_threshold: float = 18.0  # mean abs diff on 0–255 gray thumb
    stable_ms: int = 700
    thumb_size: tuple[int, int] = (64, 36)
    _last_stable: list[int] | None = None
    _candidate: list[int] | None = None
    _candidate_since: float = 0.0
    last_accepted_jpeg: bytes | None = field(default=None, repr=False)

    def _thumb(self, jpeg: bytes) -> list[int]:
        img = Image.open(io.BytesIO(jpeg)).convert("L").resize(self.thumb_size, Image.Resampling.BILINEAR)
        flat = getattr(img, "get_flattened_data", None)
        data = flat() if callable(flat) else img.getdata()
        return list(data)

    @staticmethod
    def _mad(a: list[int], b: list[int]) -> float:
        if len(a) != len(b) or not a:
            return 999.0
        return sum(abs(x - y) for x, y in zip(a, b)) / len(a)

    def observe(self, jpeg: bytes) -> bool:
        """Return True once when a new scene has stabilized (fire PromptTrigger)."""
        thumb = self._thumb(jpeg)
        now = time.time()
        if self._last_stable is None:
            self._last_stable = thumb
            self.last_accepted_jpeg = jpeg
            return False

        score = self._mad(thumb, self._last_stable)
        if score < self.enter_threshold:
            self._candidate = None
            return False

        if self._candidate is None:
            self._candidate = thumb
            self._candidate_since = now
            return False

        # Still changing a lot vs candidate → reset stabilize clock
        if self._mad(thumb, self._candidate) > self.enter_threshold * 0.5:
            self._candidate = thumb
            self._candidate_since = now
            return False

        if (now - self._candidate_since) * 1000 < self.stable_ms:
            return False

        self._last_stable = thumb
        self._candidate = None
        self.last_accepted_jpeg = jpeg
        return True

    def should_upload(self, jpeg: bytes, min_interval_s: float = 1.0) -> bool:
        """1 fps + skip near-duplicates vs last upload."""
        now = time.time()
        if not hasattr(self, "_last_upload_at"):
            self._last_upload_at = 0.0
            self._last_upload_thumb: list[int] | None = None
        if now - self._last_upload_at < min_interval_s:
            return False
        thumb = self._thumb(jpeg)
        if self._last_upload_thumb is not None:
            if self._mad(thumb, self._last_upload_thumb) < self.enter_threshold * 0.35:
                return False
        self._last_upload_at = now
        self._last_upload_thumb = thumb
        return True
