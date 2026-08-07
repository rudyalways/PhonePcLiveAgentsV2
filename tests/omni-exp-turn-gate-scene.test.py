#!/usr/bin/env python3
"""Unit tests for omni TurnGate + SceneChangeSensor."""

from __future__ import annotations

import io
import sys
import time
import unittest
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from omni_exp_scene import BoardInkSensor, SceneChangeSensor  # noqa: E402
from omni_exp_turn_gate import TurnGate, TurnRequest  # noqa: E402


def _jpeg(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), color).save(buf, format="JPEG")
    return buf.getvalue()


def _split_jpeg(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> bytes:
    """320×180 with distinct top/bottom bands (for face-mask / ink tests)."""
    img = Image.new("RGB", (320, 180), top)
    bot = Image.new("RGB", (320, 90), bottom)
    img.paste(bot, (0, 90))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestTurnGate(unittest.TestCase):
    def test_voice_always_allowed(self):
        g = TurnGate()
        g.responding = True
        ok, _ = g.allow(TurnRequest(kind="voice"))
        self.assertTrue(ok)

    def test_prompt_blocked_while_busy(self):
        g = TurnGate()
        g.responding = True
        ok, why = g.allow(TurnRequest(kind="prompt", reason="scene_change"))
        self.assertFalse(ok)
        self.assertEqual(why, "busy")

    def test_scene_cooldown(self):
        g = TurnGate()
        g.cooldowns_ms["scene_change"] = 60_000
        req = TurnRequest(kind="prompt", reason="scene_change")
        self.assertTrue(g.allow(req)[0])
        g.mark_fired(req)
        ok, why = g.allow(req)
        self.assertFalse(ok)
        self.assertEqual(why, "cooldown")


class TestSceneChange(unittest.TestCase):
    def test_stabilize_fires_once(self):
        s = SceneChangeSensor(enter_threshold=10.0, stable_ms=40)
        self.assertFalse(s.observe(_jpeg((0, 0, 0))))
        self.assertFalse(s.observe(_jpeg((255, 255, 255))))
        time.sleep(0.05)
        self.assertTrue(s.observe(_jpeg((255, 255, 255))))
        self.assertFalse(s.observe(_jpeg((255, 255, 255))))

    def test_upload_dedupe_independent_of_high_scene_threshold(self):
        # High scene threshold (quiet fires) must not starve uploads the way
        # enter_threshold*0.35 did on omni-exp (threshold=28 → skip MAD<9.8).
        s = SceneChangeSensor(
            enter_threshold=28.0,
            upload_dedupe_threshold=6.3,
            upload_keepalive_s=999,
        )
        self.assertTrue(s.should_upload(_jpeg((0, 0, 0)), min_interval_s=0))
        # Tiny change — still under 6.3 → skip
        self.assertFalse(s.should_upload(_jpeg((2, 2, 2)), min_interval_s=0))
        # Large change — upload
        self.assertTrue(s.should_upload(_jpeg((255, 0, 0)), min_interval_s=0))

    def test_upload_keepalive_forces_static_refresh(self):
        s = SceneChangeSensor(
            enter_threshold=28.0,
            upload_dedupe_threshold=6.3,
            upload_keepalive_s=0.05,
        )
        frame = _jpeg((40, 40, 40))
        self.assertTrue(s.should_upload(frame, min_interval_s=0))
        self.assertFalse(s.should_upload(frame, min_interval_s=0))
        time.sleep(0.06)
        self.assertTrue(s.should_upload(frame, min_interval_s=0))

    def test_mask_upper_ignores_top_band_motion(self):
        # Top changes a lot (face/walker); bottom stays put → masked MAD quiet.
        s = SceneChangeSensor(
            enter_threshold=12.0,
            stable_ms=40,
            mask_upper_fraction=0.5,
        )
        self.assertFalse(s.observe(_split_jpeg((0, 0, 0), (40, 40, 40))))
        self.assertFalse(s.observe(_split_jpeg((255, 255, 255), (40, 40, 40))))
        time.sleep(0.05)
        self.assertFalse(s.observe(_split_jpeg((255, 0, 0), (40, 40, 40))))

    def test_mask_upper_still_fires_on_board_motion(self):
        s = SceneChangeSensor(
            enter_threshold=12.0,
            stable_ms=40,
            mask_upper_fraction=0.5,
        )
        self.assertFalse(s.observe(_split_jpeg((10, 10, 10), (0, 0, 0))))
        self.assertFalse(s.observe(_split_jpeg((10, 10, 10), (255, 255, 255))))
        time.sleep(0.05)
        self.assertTrue(s.observe(_split_jpeg((10, 10, 10), (255, 255, 255))))


class TestBoardInk(unittest.TestCase):
    def test_board_ink_fires_on_lower_change(self):
        s = BoardInkSensor(
            enter_threshold=8.0,
            stable_ms=40,
            mask_upper_fraction=0.5,
            cooldown_s=0.0,
            ocr_enabled=False,
        )
        self.assertFalse(s.observe(_split_jpeg((200, 200, 200), (255, 255, 255))))
        self.assertFalse(s.observe(_split_jpeg((200, 200, 200), (0, 0, 0))))
        time.sleep(0.05)
        self.assertTrue(s.observe(_split_jpeg((200, 200, 200), (0, 0, 0))))


if __name__ == "__main__":
    unittest.main()
