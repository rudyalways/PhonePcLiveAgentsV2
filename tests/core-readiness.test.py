#!/usr/bin/env python3
"""Tests for src/core_readiness.py — alive vs ready-for-work."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
import core_readiness as cr  # noqa: E402


class TestCoreReadiness(unittest.TestCase):
    def test_mark_booting_then_ready(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "state" / "cores").mkdir(parents=True)
            cr.mark_booting(ws, reason="test")
            self.assertTrue(cr.booting_path(ws).is_file())
            self.assertFalse(cr.ready_path(ws).is_file())
            boot = cr.read_booting(ws)
            self.assertIsNotNone(boot)
            self.assertEqual(boot["reason"], "test")

            cr.mark_ready(ws, source="test")
            self.assertFalse(cr.booting_path(ws).is_file())
            self.assertTrue(cr.ready_path(ws).is_file())

    def test_probe_down_when_no_alive(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "state").mkdir()
            r = cr.probe_core_readiness(ws, host="testhost", alive_max_age_s=90)
            self.assertFalse(r["alive"])
            self.assertFalse(r["ready"])
            self.assertEqual(r["reason"], "core_down")

    def test_probe_booting_blocks_ready(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            host = "testhost"
            cores = ws / "state" / "cores"
            cores.mkdir(parents=True)
            alive = cores / f"{host}.alive"
            alive.write_text(json.dumps({"status": "running"}))
            # Fresh mtime
            os.utime(alive, None)
            cr.mark_booting(ws, reason="startup")
            r = cr.probe_core_readiness(ws, host=host, alive_max_age_s=90)
            self.assertTrue(r["alive"])
            self.assertTrue(r["booting"])
            self.assertFalse(r["ready"])
            self.assertEqual(r["reason"], "booting")

    def test_probe_ready_with_stamp(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            host = "testhost"
            cores = ws / "state" / "cores"
            cores.mkdir(parents=True)
            alive = cores / f"{host}.alive"
            alive.write_text("{}")
            os.utime(alive, None)
            cr.mark_ready(ws, source="skip-startup")
            r = cr.probe_core_readiness(ws, host=host, alive_max_age_s=90)
            self.assertTrue(r["alive"])
            self.assertTrue(r["ready"])
            self.assertFalse(r["booting"])
            self.assertEqual(r["reason"], "ready")

    def test_probe_ready_via_watcher_pid(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            host = "testhost"
            cores = ws / "state" / "cores"
            cores.mkdir(parents=True)
            alive = cores / f"{host}.alive"
            alive.write_text("{}")
            os.utime(alive, None)
            # Current process is alive
            cr.watcher_pid_path(ws).write_text(str(os.getpid()))
            r = cr.probe_core_readiness(ws, host=host, alive_max_age_s=90)
            self.assertTrue(r["ready"])
            self.assertTrue(r["watcher_alive"])

    def test_stale_booting_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            host = "testhost"
            cores = ws / "state" / "cores"
            cores.mkdir(parents=True)
            alive = cores / f"{host}.alive"
            alive.write_text("{}")
            os.utime(alive, None)
            bp = cr.booting_path(ws)
            bp.parent.mkdir(parents=True, exist_ok=True)
            bp.write_text(json.dumps({"ts": 1, "reason": "old"}))
            old = time.time() - (cr.BOOTING_STALE_S + 60)
            os.utime(bp, (old, old))
            cr.mark_ready(ws, source="manual")
            # mark_ready clears booting — rewrite stale for this case
            bp.write_text(json.dumps({"ts": 1, "reason": "old"}))
            os.utime(bp, (old, old))
            # ready stamp still present from mark_ready
            r = cr.probe_core_readiness(ws, host=host, alive_max_age_s=90)
            self.assertTrue(r["ready"])
            self.assertFalse(r["booting"])

    def test_pane_looks_booting(self):
        self.assertTrue(cr.pane_looks_booting("I'll continue schedule-crons first"))
        self.assertTrue(cr.pane_looks_booting("Invoking /startup"))
        self.assertFalse(cr.pane_looks_booting("Idling for next TASK_FILE"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
