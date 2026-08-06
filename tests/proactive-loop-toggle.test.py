#!/usr/bin/env python3
"""Regression tests for the proactive-loop whole-loop toggle."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills/proactive-loop/scripts/proactive-loop-enabled.py"
MANIFEST = REPO / "skills/proactive-loop/manifest.json"
ENV_NAME = "SUTANDO_PROACTIVE_LOOP_ENABLED"

spec = importlib.util.spec_from_file_location("proactive_loop_gate", SCRIPT)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

failures = []


def check(label: str, condition: bool) -> None:
    if condition:
        print(f"PASS  {label}")
    else:
        print(f"FAIL  {label}")
        failures.append(label)


manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
check("manifest declares the toggle", ENV_NAME in manifest.get("config", {}))
check("shipped manifest default is enabled", gate.proactive_loop_enabled({}))
check(
    "self-development flag still declared",
    "SUTANDO_SELF_DEVELOPMENT_ENABLED" in manifest.get("config", {}),
)

for value in ("1", "true", "YES", "on", "enabled"):
    check(f"truthy override {value!r}", gate.proactive_loop_enabled({ENV_NAME: value}))

for value in ("0", "false", "NO", "off", "disabled"):
    check(f"false override {value!r}", not gate.proactive_loop_enabled({ENV_NAME: value}))

check("invalid override fails closed", not gate.proactive_loop_enabled({ENV_NAME: "maybe"}))

check(
    "env overrides a disabled manifest",
    gate.proactive_loop_enabled({ENV_NAME: "1"}),
)

with tempfile.TemporaryDirectory() as td:
    missing = Path(td) / "missing-manifest.json"
    check(
        "missing manifest fails closed",
        not gate.proactive_loop_enabled({}, manifest_path=missing),
    )
    malformed = Path(td) / "malformed-manifest.json"
    malformed.write_text('{"config": []}', encoding="utf-8")
    check(
        "malformed manifest config fails closed",
        not gate.proactive_loop_enabled({}, manifest_path=malformed),
    )
    disabled_manifest = Path(td) / "disabled-manifest.json"
    disabled_manifest.write_text(
        json.dumps({"config": {ENV_NAME: "0"}}), encoding="utf-8"
    )
    check(
        "manifest can default to disabled",
        not gate.proactive_loop_enabled({}, manifest_path=disabled_manifest),
    )
    check(
        "env beats a disabled manifest",
        gate.proactive_loop_enabled({ENV_NAME: "1"}, manifest_path=disabled_manifest),
    )

disabled = subprocess.run(
    ["python3", str(SCRIPT)],
    env={ENV_NAME: "0"},
    capture_output=True,
    text=True,
    check=True,
)
check("CLI reports disabled", disabled.stdout.strip() == "disabled")

invalid = subprocess.run(
    ["python3", str(SCRIPT)],
    env={ENV_NAME: "surprise"},
    capture_output=True,
    text=True,
    check=True,
)
check("CLI invalid value reports disabled", invalid.stdout.strip() == "disabled")
check("CLI invalid value warns", "invalid" in invalid.stderr)


def run_main(value: str):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch.dict(os.environ, {ENV_NAME: value}, clear=True):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = gate.main()
    return result, stdout.getvalue(), stderr.getvalue()


rc, stdout, stderr = run_main("1")
check("main enabled path", rc == 0 and stdout.strip() == "enabled" and not stderr)
rc, stdout, stderr = run_main("0")
check("main disabled path", rc == 0 and stdout.strip() == "disabled" and not stderr)
rc, stdout, stderr = run_main("unexpected")
check(
    "main invalid path fails closed with warning",
    rc == 0 and stdout.strip() == "disabled" and "invalid" in stderr,
)

crons_text = (REPO / "skills/schedule-crons/SKILL.md").read_text(encoding="utf-8")
check(
    "both gates invoke the toggle script",
    crons_text.count("proactive-loop-enabled.py") >= 2,
)
check(
    "registration step skips the loop entry when off",
    "skip any entry whose `prompt_skill` is `proactive-loop`" in crons_text,
)
check(
    "fallback step is gated",
    "proactive-loop fallback skipped" in crons_text,
)
check(
    "a missing script is treated as enabled",
    "missing optional script must not silently stop" in crons_text,
)

if failures:
    raise SystemExit(f"{len(failures)} failure(s): {', '.join(failures)}")
print("\nall tests passed")
