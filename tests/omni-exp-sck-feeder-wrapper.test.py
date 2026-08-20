#!/usr/bin/env python3
"""Unit tests for screencapturekit_feeder_wrapper.py."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "visual_feeder"))

from screencapturekit_feeder_wrapper import FeederStats, ScreenCaptureKitFeeder


class TestFeederStatsParse(unittest.TestCase):
    SUMMARY = (
        "Capture started\n"
        "Frames sent: 42\n"
        "Frames failed: 3\n"
        "Duration: 84.0s\n"
        "Average FPS: 0.500\n"
        "Total data: 12.3MB\n"
    )

    def test_parse_frames_sent(self):
        stats = FeederStats(self.SUMMARY)
        self.assertEqual(stats.frames_sent, 42)

    def test_parse_duration_s(self):
        stats = FeederStats(self.SUMMARY)
        self.assertAlmostEqual(stats.duration_s, 84.0)

    def test_parse_avg_fps(self):
        stats = FeederStats(self.SUMMARY)
        self.assertAlmostEqual(stats.avg_fps, 0.500)

    def test_parse_malformed_line_no_raise(self):
        """A garbled line in the output should not raise."""
        bad = "Frames sent: not_a_number\nAverage FPS: also_bad\n"
        try:
            FeederStats(bad)
        except Exception as e:
            self.fail(f"FeederStats raised unexpectedly on malformed input: {e}")

    def test_empty_string_all_zeros(self):
        stats = FeederStats("")
        self.assertEqual(stats.frames_sent, 0)
        self.assertEqual(stats.frames_failed, 0)
        self.assertAlmostEqual(stats.duration_s, 0.0)
        self.assertAlmostEqual(stats.avg_fps, 0.0)


class TestScreenCaptureKitFeederStart(unittest.TestCase):
    def test_start_raises_file_not_found_when_swift_missing(self):
        """start() must raise FileNotFoundError when SWIFT_FEEDER doesn't exist."""
        import asyncio
        feeder = ScreenCaptureKitFeeder(fps=0.5, omni_port=7090)
        nonexistent = Path("/tmp/__no_such_feeder__.swift")
        import screencapturekit_feeder_wrapper as mod
        original = mod.SWIFT_FEEDER
        mod.SWIFT_FEEDER = nonexistent
        try:
            with self.assertRaises(FileNotFoundError):
                asyncio.run(feeder.start())
        finally:
            mod.SWIFT_FEEDER = original

    def test_cmd_contains_fps_and_port(self):
        """The subprocess cmd must include --fps and --port with the configured values."""
        import asyncio
        feeder = ScreenCaptureKitFeeder(fps=1.5, omni_port=9999)

        captured_cmd = []

        def fake_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            m = MagicMock()
            # readline returns empty string so _wait_for_startup exits quickly (process poll returns None → loop)
            # Make stdout.readline return "" after first call to trigger the empty-line path,
            # then return b"" to exit the while loop via poll()
            readline_mock = MagicMock(return_value="")
            m.stdout = MagicMock()
            m.stdout.readline = readline_mock
            m.poll.return_value = 1  # process exited immediately → raises RuntimeError
            return m

        import screencapturekit_feeder_wrapper as mod
        fake_swift = Path("/tmp/__fake_feeder__.swift")
        fake_swift.touch()
        original = mod.SWIFT_FEEDER
        mod.SWIFT_FEEDER = fake_swift

        try:
            with patch("subprocess.run") as mock_run, \
                 patch("subprocess.Popen", side_effect=fake_popen):
                mock_run.return_value = MagicMock(returncode=0)
                try:
                    asyncio.run(feeder.start())
                except (RuntimeError, FileNotFoundError, Exception):
                    pass
        finally:
            mod.SWIFT_FEEDER = original
            fake_swift.unlink(missing_ok=True)

        self.assertIn("--fps", captured_cmd)
        fps_idx = captured_cmd.index("--fps")
        self.assertEqual(captured_cmd[fps_idx + 1], "1.5")
        self.assertIn("--port", captured_cmd)
        port_idx = captured_cmd.index("--port")
        self.assertEqual(captured_cmd[port_idx + 1], "9999")


if __name__ == "__main__":
    unittest.main(verbosity=2)
