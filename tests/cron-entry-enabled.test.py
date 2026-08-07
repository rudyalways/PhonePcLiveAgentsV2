#!/usr/bin/env python3
"""Regression tests for morning-briefing / daily-insight cron env gates."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills/schedule-crons/scripts/cron-entry-enabled.py"
MANIFEST = REPO / "skills/schedule-crons/manifest.json"
CRONS_SKILL = REPO / "skills/schedule-crons/SKILL.md"
BRIEF_SKILL = REPO / "skills/morning-briefing/SKILL.md"
DAILY = REPO / "src/daily-insight.py"
ENV_MB = "SUTANDO_MORNING_BRIEFING_ENABLED"
ENV_DI = "SUTANDO_DAILY_INSIGHT_ENABLED"
ENV_PQ = "SUTANDO_PENDING_QUESTIONS_CRON_ENABLED"
ENV_SM = "SUTANDO_SYNC_MEMORY_CRON_ENABLED"

spec = importlib.util.spec_from_file_location("cron_entry_gate", SCRIPT)
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
for env_name in (ENV_MB, ENV_DI, ENV_PQ, ENV_SM):
    check(f"manifest declares {env_name}", env_name in cfg)
    check(f"shipped {env_name} default is OFF", cfg.get(env_name) == "0")

for entry in ("morning-briefing", "daily-insight", "pending-questions", "sync-memory"):
    check(
        f"empty env → {entry} disabled (manifest default)",
        not gate.cron_entry_enabled(entry, {}),
    )

for value in ("1", "true", "YES", "on", "enabled"):
    check(
        f"morning-briefing truthy {value!r}",
        gate.cron_entry_enabled("morning-briefing", {ENV_MB: value}),
    )
    check(
        f"daily-insight truthy {value!r}",
        gate.cron_entry_enabled("daily-insight", {ENV_DI: value}),
    )

for value in ("0", "false", "NO", "off", "disabled"):
    check(
        f"morning-briefing false {value!r}",
        not gate.cron_entry_enabled("morning-briefing", {ENV_MB: value}),
    )

check(
    "invalid fails closed",
    not gate.cron_entry_enabled("morning-briefing", {ENV_MB: "maybe"}),
)

with tempfile.TemporaryDirectory() as td:
    missing = Path(td) / "missing.json"
    check(
        "missing manifest fails closed",
        not gate.cron_entry_enabled("morning-briefing", {}, manifest_path=missing),
    )
    enabled_m = Path(td) / "on.json"
    enabled_m.write_text(json.dumps({"config": {ENV_MB: "1"}}), encoding="utf-8")
    check(
        "manifest can default on",
        gate.cron_entry_enabled("morning-briefing", {}, manifest_path=enabled_m),
    )
    check(
        "env 0 beats manifest 1",
        not gate.cron_entry_enabled(
            "morning-briefing", {ENV_MB: "0"}, manifest_path=enabled_m
        ),
    )

cli = subprocess.run(
    ["python3", str(SCRIPT), "morning-briefing"],
    env={ENV_MB: "0"},
    capture_output=True,
    text=True,
    check=True,
)
check("CLI morning-briefing disabled", cli.stdout.strip() == "disabled")

cli_on = subprocess.run(
    ["python3", str(SCRIPT), "daily-insight"],
    env={ENV_DI: "1"},
    capture_output=True,
    text=True,
    check=True,
)
check("CLI daily-insight enabled", cli_on.stdout.strip() == "enabled")

crons = CRONS_SKILL.read_text(encoding="utf-8")
check("schedule-crons invokes cron-entry-enabled.py", "cron-entry-enabled.py" in crons)
check(
    "schedule-crons documents skip log pattern",
    "<name> skipped (<ENV>=0)" in crons,
)
check(
    "schedule-crons lists pending-questions gate",
    ENV_PQ in crons and "pending-questions" in crons,
)
check(
    "schedule-crons lists sync-memory gate",
    ENV_SM in crons and "sync-memory" in crons,
)
check(
    "schedule-crons disarms with CronDelete",
    "CronDelete" in crons and "<name> cron deleted (<ENV>=0)" in crons,
)
check(
    "missing gate script treated as disabled",
    "Missing script → treat as disabled" in crons,
)

brief = BRIEF_SKILL.read_text(encoding="utf-8")
check("morning-briefing skill has kill switch", "cron-entry-enabled.py" in brief)
check(
    "morning-briefing aborts when disabled",
    "morning-briefing skipped (SUTANDO_MORNING_BRIEFING_ENABLED=0)" in brief,
)

daily_src = DAILY.read_text(encoding="utf-8")
check("daily-insight.py self-gates", "_daily_insight_enabled" in daily_src)
check(
    "daily-insight.py skip line",
    "daily-insight skipped (SUTANDO_DAILY_INSIGHT_ENABLED=0)" in daily_src,
)

example = (REPO / ".env.example").read_text(encoding="utf-8")
for env_name in (ENV_MB, ENV_DI, ENV_PQ, ENV_SM):
    check(f"env.example documents {env_name}", env_name in example)

pq_src = (REPO / "src/check-pending-questions.py").read_text(encoding="utf-8")
check("check-pending-questions self-gates", "_pending_questions_cron_enabled" in pq_src)
sm = (REPO / "scripts/sync-memory.sh").read_text(encoding="utf-8")
check(
    "sync-memory.sh self-gates",
    "cron-entry-enabled.py" in sm and 'sync-memory' in sm,
)
check("sync-memory.sh force bypass", "SUTANDO_SYNC_MEMORY_FORCE" in sm)

if failures:
    raise SystemExit(f"{len(failures)} failure(s): {', '.join(failures)}")
print("\nall tests passed")
