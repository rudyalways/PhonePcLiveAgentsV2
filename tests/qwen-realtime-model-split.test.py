#!/usr/bin/env python3
"""Omni-agent model env is separate from voice-agent REALTIME_MODEL."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from qwen_realtime_compat import (  # noqa: E402
    DEFAULT_QWEN_OMNI_REALTIME_MODEL,
    qwen_default_realtime_model,
    qwen_omni_realtime_model,
)


class QwenOmniModelSplitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in ("QWEN_OMNI_REALTIME_MODEL", "REALTIME_MODEL", "QWEN_REALTIME_MODEL")
        }
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_voice_unchanged_by_omni_var(self) -> None:
        os.environ["REALTIME_MODEL"] = "qwen3.5-omni-plus-realtime"
        os.environ["QWEN_OMNI_REALTIME_MODEL"] = "qwen3.5-omni-flash-realtime"
        self.assertEqual(qwen_default_realtime_model(), "qwen3.5-omni-plus-realtime")
        self.assertEqual(qwen_omni_realtime_model(), "qwen3.5-omni-flash-realtime")

    def test_omni_default(self) -> None:
        self.assertEqual(qwen_omni_realtime_model(), DEFAULT_QWEN_OMNI_REALTIME_MODEL)
        self.assertEqual(qwen_default_realtime_model(), "qwen3.5-omni-plus-realtime")

    def test_voice_ignores_qwen_realtime_model_alias(self) -> None:
        # QWEN_REALTIME_MODEL must not affect voice (omni-only split).
        os.environ["QWEN_REALTIME_MODEL"] = "should-not-affect-voice"
        self.assertEqual(qwen_default_realtime_model(), "qwen3.5-omni-plus-realtime")


if __name__ == "__main__":
    unittest.main()
