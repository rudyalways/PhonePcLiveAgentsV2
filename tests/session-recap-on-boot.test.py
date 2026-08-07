#!/usr/bin/env python3
"""Regression tests for SUTANDO_SESSION_RECAP_ON_BOOT gate."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills/session-recap/scripts/session-recap-on-boot-enabled.py"
MANIFEST = REPO / "skills/session-recap/manifest.json"
CRONS = REPO / "skills/schedule-crons/SKILL.md"
RECAP = REPO / "skills/session-recap/SKILL.md"
ENV_NAME = "SUTANDO_SESSION_RECAP_ON_BOOT"
SKIP = "SUTANDO_SKIP_STARTUP"

spec = importlib.util.spec_from_file_location("recap_boot_gate", SCRIPT)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

failures: list[str] = []


def check(label: str, condition: bool) -> None:
    if condition:
        print(f"PASS  {label}")
    else:
        print(f"FAIL  {label}")
        failures.append(label)


manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
cfg = manifest.get("config", {})
check("manifest declares toggle", ENV_NAME in cfg)
check("shipped default is OFF", cfg.get(ENV_NAME) == "0")
check("empty env → disabled", not gate.session_recap_on_boot_enabled({}))
check("env 1 → enabled", gate.session_recap_on_boot_enabled({ENV_NAME: "1"}))
check("env 0 → disabled", not gate.session_recap_on_boot_enabled({ENV_NAME: "0"}))
check(
    "SKIP_STARTUP forces off even when env 1",
    not gate.session_recap_on_boot_enabled({ENV_NAME: "1", SKIP: "1"}),
)
check("invalid fails closed", not gate.session_recap_on_boot_enabled({ENV_NAME: "maybe"}))

with tempfile.TemporaryDirectory() as td:
    missing = Path(td) / "missing.json"
    check(
        "missing manifest fails closed",
        not gate.session_recap_on_boot_enabled({}, manifest_path=missing),
    )

cli = subprocess.run(
    ["python3", str(SCRIPT)],
    env={},
    capture_output=True,
    text=True,
    check=True,
)
check("CLI default disabled", cli.stdout.strip() == "disabled")

cli_on = subprocess.run(
    ["python3", str(SCRIPT)],
    env={ENV_NAME: "1"},
    capture_output=True,
    text=True,
    check=True,
)
check("CLI enabled", cli_on.stdout.strip() == "enabled")

crons = CRONS.read_text(encoding="utf-8")
check("schedule-crons invokes gate script", "session-recap-on-boot-enabled.py" in crons)
check(
    "schedule-crons skip log",
    "session-recap on boot skipped (SUTANDO_SESSION_RECAP_ON_BOOT=0)" in crons,
)
check(
    "schedule-crons mark-ready before dump",
    "startup-before-recap" in crons,
)
check(
    "missing script treated as disabled",
    "Missing script → treat as disabled" in crons,
)
check("mid-recap yield to tasks", "abort recap immediately" in crons)

recap = RECAP.read_text(encoding="utf-8")
check("session-recap skill documents gate", "session-recap-on-boot-enabled.py" in recap)
check("session-recap skill default OFF", "default OFF" in recap)

example = (REPO / ".env.example").read_text(encoding="utf-8")
check("env.example documents flag", ENV_NAME in example)

if failures:
    raise SystemExit(f"{len(failures)} failure(s): {', '.join(failures)}")
print("\nall tests passed")
