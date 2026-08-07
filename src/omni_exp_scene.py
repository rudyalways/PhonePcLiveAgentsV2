"""Scene-change sensor — thumb-diff on JPEG frames for PromptTrigger.

Upload policy is deliberately separate from scene-fire threshold: a high
``enter_threshold`` (quiet proactive fires) must not starve the rolling
vision window that Qwen needs to answer "what's in the camera?".

Research / whiteboard extras (docs/omni-exp-whiteboard-meeting-capture.md):
- ``mask_upper_fraction`` ignores the top of the frame for MAD (faces / walkers)
- ``BoardInkSensor`` watches the lower board crop for ink/text change
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger("omni-exp-scene")

# Match main omni-agent default enter_threshold (18) * 0.35 for near-dupe skip.
DEFAULT_UPLOAD_DEDUPE = 18.0 * 0.35  # ≈6.3 MAD on 0–255 gray thumb
# Design D2: push a frame every N seconds even if the scene looks static.
DEFAULT_UPLOAD_KEEPALIVE_S = 8.0


def _mad_skip_top(a: list[int], b: list[int], *, width: int, height: int, skip_top_frac: float) -> float:
    """Mean abs-diff, optionally skipping the top ``skip_top_frac`` of rows."""
    if len(a) != len(b) or not a or width <= 0 or height <= 0:
        return 999.0
    skip_rows = max(0, min(height - 1, int(height * max(0.0, min(0.9, skip_top_frac)))))
    start = skip_rows * width
    if start >= len(a):
        return 999.0
    n = len(a) - start
    if n <= 0:
        return 999.0
    return sum(abs(x - y) for x, y in zip(a[start:], b[start:])) / n


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
    # Research: ignore upper face / walker band for scene MAD (0 = full frame).
    mask_upper_fraction: float = 0.0
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

    def _mad(self, a: list[int], b: list[int]) -> float:
        w, h = self.thumb_size
        return _mad_skip_top(a, b, width=w, height=h, skip_top_frac=self.mask_upper_fraction)

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
            # Upload dedupe stays full-frame (vision needs the face too).
            if _mad_skip_top(
                thumb,
                self._last_upload_thumb,
                width=self.thumb_size[0],
                height=self.thumb_size[1],
                skip_top_frac=0.0,
            ) < self.upload_dedupe_threshold:
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


@dataclass
class BoardInkSensor:
    """Detect stabilized ink/board changes on the lower frame (faces masked).

    Optional OCR via system ``tesseract`` when present (fail-open).
    """

    enter_threshold: float = 14.0
    mask_upper_fraction: float = 0.35
    stable_ms: int = 600
    thumb_size: tuple[int, int] = (96, 54)
    cooldown_s: float = 20.0
    ocr_enabled: bool = True
    _last_stable: list[int] | None = None
    _candidate: list[int] | None = None
    _candidate_since: float = 0.0
    _last_fire_at: float = 0.0
    last_ocr_text: str = ""

    def _board_thumb(self, jpeg: bytes) -> list[int]:
        img = Image.open(io.BytesIO(jpeg)).convert("L")
        w, h = img.size
        top = int(h * max(0.0, min(0.9, self.mask_upper_fraction)))
        crop = img.crop((0, top, w, h))
        # Autocontrast + light edge boost: ink strokes pop; solid board fills still
        # register when the board region itself changes (tests + marker wipes).
        crop = ImageOps.autocontrast(crop)
        edges = crop.filter(ImageFilter.FIND_EDGES)
        # Blend so uniform board color changes still produce MAD.
        crop = Image.blend(crop, edges, 0.35)
        crop = crop.resize(self.thumb_size, Image.Resampling.BILINEAR)
        flat = getattr(crop, "get_flattened_data", None)
        data = flat() if callable(flat) else crop.getdata()
        return list(data)

    @staticmethod
    def _mad(a: list[int], b: list[int]) -> float:
        if len(a) != len(b) or not a:
            return 999.0
        return sum(abs(x - y) for x, y in zip(a, b)) / len(a)

    def observe(self, jpeg: bytes) -> bool:
        """True once when board ink/text region has a new stable delta."""
        try:
            thumb = self._board_thumb(jpeg)
        except Exception as e:
            logger.debug("board thumb failed: %s", e)
            return False
        now = time.time()
        if self._last_stable is None:
            self._last_stable = thumb
            return False
        if now - self._last_fire_at < self.cooldown_s:
            return False

        score = self._mad(thumb, self._last_stable)
        if score < self.enter_threshold:
            self._candidate = None
            return False
        if self._candidate is None:
            self._candidate = thumb
            self._candidate_since = now
            return False
        if self._mad(thumb, self._candidate) > self.enter_threshold * 0.5:
            self._candidate = thumb
            self._candidate_since = now
            return False
        if (now - self._candidate_since) * 1000 < self.stable_ms:
            return False

        self._last_stable = thumb
        self._candidate = None
        self._last_fire_at = now
        self.last_ocr_text = self._maybe_ocr(jpeg) if self.ocr_enabled else ""
        return True

    def _maybe_ocr(self, jpeg: bytes) -> str:
        """Best-effort board OCR; empty if tesseract missing or fails."""
        if not shutil.which("tesseract"):
            return ""
        try:
            img = Image.open(io.BytesIO(jpeg)).convert("L")
            w, h = img.size
            top = int(h * self.mask_upper_fraction)
            crop = img.crop((0, top, w, h))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            r = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                input=buf.getvalue(),
                capture_output=True,
                timeout=8,
            )
            text = (r.stdout or b"").decode("utf-8", errors="replace").strip()
            # Keep short — buffer / task bodies should stay speakable.
            text = " ".join(text.split())
            return text[:280]
        except Exception as e:
            logger.debug("board OCR skipped: %s", e)
            return ""
