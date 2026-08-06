"""Scene-change sensor — thumb-diff on JPEG frames for PromptTrigger.

Upload policy is deliberately separate from scene-fire threshold: a high
``enter_threshold`` (quiet proactive fires) must not starve the rolling
vision window that Qwen needs to answer "what's in the camera?".
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field

from PIL import Image

# Match main omni-agent default enter_threshold (18) * 0.35 for near-dupe skip.
DEFAULT_UPLOAD_DEDUPE = 18.0 * 0.35  # ≈6.3 MAD on 0–255 gray thumb
# Design D2: push a frame every N seconds even if the scene looks static.
DEFAULT_UPLOAD_KEEPALIVE_S = 8.0


@dataclass
class SceneChangeSensor:
    """Local thumb-diff; upload/dedup policy stays in the agent."""

    enter_threshold: float = 18.0  # mean abs diff on 0–255 gray thumb (scene fire)
    # Near-dupe skip for uploads — independent of enter_threshold so raising
    # scene threshold (fewer proactive fires) does not starve vision.
    upload_dedupe_threshold: float = DEFAULT_UPLOAD_DEDUPE
    upload_keepalive_s: float = DEFAULT_UPLOAD_KEEPALIVE_S
    stable_ms: int = 700
    thumb_size: tuple[int, int] = (64, 36)
    _last_stable: list[int] | None = None
    _candidate: list[int] | None = None
    _candidate_since: float = 0.0
    last_accepted_jpeg: bytes | None = field(default=None, repr=False)
    _last_upload_at: float = 0.0
    _last_upload_thumb: list[int] | None = None

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
        """1 fps + skip near-duplicates, with keepalive for static scenes."""
        now = time.time()
        if self._last_upload_at and now - self._last_upload_at < min_interval_s:
            return False
        thumb = self._thumb(jpeg)
        # Keepalive: refresh Qwen's vision window even when the frame looks static.
        if (
            self._last_upload_at
            and self.upload_keepalive_s > 0
            and now - self._last_upload_at >= self.upload_keepalive_s
        ):
            self._mark_upload(now, thumb)
            return True
        if self._last_upload_thumb is not None:
            if self._mad(thumb, self._last_upload_thumb) < self.upload_dedupe_threshold:
                return False
        self._mark_upload(now, thumb)
        return True

    def note_upload(self, jpeg: bytes) -> None:
        """Record that jpeg was force-appended (speech / Ask view keyframe)."""
        try:
            thumb = self._thumb(jpeg)
        except Exception:
            thumb = None
        self._mark_upload(time.time(), thumb)

    def _mark_upload(self, now: float, thumb: list[int] | None) -> None:
        self._last_upload_at = now
        if thumb is not None:
            self._last_upload_thumb = thumb
