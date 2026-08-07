#!/usr/bin/env python3
"""Regression tests for fast boot session-recap (default ON, no LLM)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "skills/session-recap/scripts/session-recap-on-boot-enabled.py"
BOOT = REPO / "skills/session-recap/scripts/boot-recap.py"
MANIFEST = REPO / "skills/session-recap/manifest.json"
CRONS = REPO / "skills/schedule-crons/SKILL.md"
RECAP = REPO / "skills/session-recap/SKILL.md"
ENV_NAME = "SUTANDO_SESSION_RECAP_ON_BOOT"

spec = importlib.util.spec_from_file_location("recap_boot_gate", GATE)
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
check("shipped default is ON", cfg.get(ENV_NAME) == "1")
check("empty env → enabled (manifest)", gate.session_recap_on_boot_enabled({}))
check("env 0 → disabled", not gate.session_recap_on_boot_enabled({ENV_NAME: "0"}))
check("env 1 → enabled", gate.session_recap_on_boot_enabled({ENV_NAME: "1"}))

cli = subprocess.run(
    ["python3", str(GATE)],
    env={},
    capture_output=True,
    text=True,
    check=True,
)
check("CLI default enabled", cli.stdout.strip() == "enabled")

crons = CRONS.read_text(encoding="utf-8")
check("schedule-crons invokes boot-recap.py", "boot-recap.py" in crons)
check("schedule-crons forbids haiku on boot", "Do **not** spawn an Agent/haiku" in crons)
check("schedule-crons mark-ready before dump", "startup-before-recap" in crons)
check("missing gate script fail-open enabled", "Missing script → treat as enabled" in crons)

recap = RECAP.read_text(encoding="utf-8")
check("session-recap skill documents boot-recap", "boot-recap.py" in recap)
check("session-recap skill no LLM on boot", "no LLM" in recap)

# boot-recap: pending tasks → exit 2
boot_spec = importlib.util.spec_from_file_location("boot_recap", BOOT)
assert boot_spec and boot_spec.loader
boot = importlib.util.module_from_spec(boot_spec)
boot_spec.loader.exec_module(boot)

with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    (ws / "tasks").mkdir()
    (ws / "tasks" / "task-1.txt").write_text("x", encoding="utf-8")
    with mock.patch.object(boot, "_workspace", return_value=ws):
        rc = boot.main(["--skip-mark-ready"])
    check("pending tasks → exit 2", rc == 2)

example = (REPO / ".env.example").read_text(encoding="utf-8")
check("env.example documents flag", ENV_NAME in example)
check("env.example documents max chars", "SUTANDO_SESSION_RECAP_BOOT_MAX_CHARS" in example)

if failures:
    raise SystemExit(f"{len(failures)} failure(s): {', '.join(failures)}")
print("\nall tests passed")
