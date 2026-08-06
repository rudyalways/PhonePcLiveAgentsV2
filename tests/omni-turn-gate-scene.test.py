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

from omni_scene import SceneChangeSensor  # noqa: E402
from omni_turn_gate import TurnGate, TurnRequest  # noqa: E402


def _jpeg(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), color).save(buf, format="JPEG")
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


if __name__ == "__main__":
    unittest.main()
