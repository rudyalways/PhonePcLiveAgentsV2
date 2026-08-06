#!/usr/bin/env python3
"""Regression tests for proactive-loop skill symlink sync."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SYNC = REPO / "skills/proactive-loop/scripts/sync-skill-link.sh"
SKILL_SRC = (REPO / "skills/proactive-loop").resolve()
ENV_NAME = "SUTANDO_PROACTIVE_LOOP_ENABLED"

failures: list[str] = []


def check(label: str, condition: bool) -> None:
    if condition:
        print(f"PASS  {label}")
    else:
        print(f"FAIL  {label}")
        failures.append(label)


def run_sync(value: str, roots: list[Path], extra_env: dict[str, str] | None = None) -> str:
    env = os.environ.copy()
    # Prevent the script's .env safety net from overriding an explicit test value.
    env[ENV_NAME] = value
    env["SUTANDO_PROACTIVE_LOOP_SYNC_ROOTS"] = ":".join(str(p) for p in roots)
    env["SUTANDO_SUPPRESS_CCD_FALLBACK_BANNER"] = "1"
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        ["bash", str(SYNC)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise AssertionError(f"sync failed rc={proc.returncode}\n{out}")
    return out


check("sync script is executable", SYNC.is_file() and os.access(SYNC, os.X_OK))
check("skill source exists", (SKILL_SRC / "SKILL.md").is_file())

with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "skills"
    root.mkdir()
    link = root / "proactive-loop"

    # Seed a stale sibling-style symlink, then disable → must vanish.
    stale = Path(td) / "stale-skill"
    stale.mkdir()
    (stale / "SKILL.md").write_text("# stale\n", encoding="utf-8")
    link.symlink_to(stale)
    run_sync("0", [root])
    check("disabled removes existing symlink", not link.exists() and not link.is_symlink())

    # Enable → link points at THIS repo.
    run_sync("1", [root])
    check("enabled creates symlink", link.is_symlink())
    target = Path(os.readlink(link))
    if not target.is_absolute():
        target = (link.parent / target).resolve()
    else:
        target = target.resolve()
    check("enabled points at this repo skill", target == SKILL_SRC)

    # Replacing a stale absolute link with the correct one.
    link.unlink()
    link.symlink_to(stale.resolve())
    run_sync("1", [root])
    target = Path(os.readlink(link))
    if not target.is_absolute():
        target = (link.parent / target).resolve()
    else:
        target = target.resolve()
    check("enabled replaces stale sibling symlink", target == SKILL_SRC)

    # Real directory must not be deleted when disabled.
    link.unlink()
    link.mkdir()
    (link / "SKILL.md").write_text("# local copy\n", encoding="utf-8")
    out = run_sync("0", [root])
    check("disabled leaves real directory in place", link.is_dir() and not link.is_symlink())
    check(
        "disabled warns about real directory",
        "real directory" in out.lower() or "leave it" in out.lower(),
    )
    # Enable must also skip clobbering a real directory.
    out = run_sync("1", [root])
    check("enabled skips relink over real directory", link.is_dir() and not link.is_symlink())
    check("enabled skip message mentions directory", "real directory" in out.lower())

# Wiring: callers invoke sync.
install = (REPO / "skills/install.sh").read_text(encoding="utf-8")
start_cli = (REPO / "src/agent/start-cli.sh").read_text(encoding="utf-8")
check("install.sh invokes sync-skill-link", "sync-skill-link.sh" in install)
check("install.sh skips generic proactive-loop install", 'skill_name" = "proactive-loop"' in install or "proactive-loop" in install)
check("start-cli.sh invokes sync-skill-link", "sync-skill-link.sh" in start_cli)
check(
    "sync supports test roots override",
    "SUTANDO_PROACTIVE_LOOP_SYNC_ROOTS" in SYNC.read_text(encoding="utf-8"),
)

if failures:
    raise SystemExit(f"{len(failures)} failure(s): {', '.join(failures)}")
print("\nall tests passed")
