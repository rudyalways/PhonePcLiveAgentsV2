#!/usr/bin/env python3
"""Contract tests for realtime_provider.factory (Phase 1)."""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from realtime_provider.factory import use_factory_enabled, migration_status
from realtime_provider.migration_state import mark_phase, read_migration_state, default_state


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)


def test_use_factory_default():
    os.environ.pop("REALTIME_USE_FACTORY", None)
    check(use_factory_enabled() is True, "factory should default on")


def test_use_factory_off():
    os.environ["REALTIME_USE_FACTORY"] = "0"
    check(use_factory_enabled() is False, "REALTIME_USE_FACTORY=0")
    os.environ.pop("REALTIME_USE_FACTORY", None)


def test_migration_default():
    state = default_state()
    check(state["phases"]["0"]["status"] == "complete", "phase 0 complete")
    check(state["current_phase"] == 1, "current phase 1")


def test_mark_phase_tmp():
    tmp = Path(tempfile.mkdtemp(prefix="rt-migrate-"))
    state_dir = tmp / "state"
    mark_phase(1, "complete", "pytest", state_dir)
    state = read_migration_state(state_dir)
    check(state["phases"]["1"]["status"] == "complete", "marked complete")
    check(state["current_phase"] == 2, "advanced to phase 2")


def main():
    test_use_factory_default()
    test_use_factory_off()
    test_migration_default()
    test_mark_phase_tmp()
    print("OK: realtime-provider factory contract tests passed")


if __name__ == "__main__":
    main()
