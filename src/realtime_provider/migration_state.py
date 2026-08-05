"""Durable migration checkpoint for realtime-provider rollout (Python mirror)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MIGRATION_SCHEMA_VERSION = 1
MIGRATION_FILENAME = "realtime-provider-migration.json"
PHASE_IDS = ("0", "1", "2", "3", "4")


def _repo_src() -> Path:
    return Path(__file__).resolve().parent.parent


def migration_state_path(state_dir: Path | None = None) -> Path:
    if state_dir is None:
        try:
            from workspace_default import resolve_workspace

            state_dir = resolve_workspace() / "state"
        except ImportError:
            state_dir = _repo_src().parent / "workspace" / "state"
    return state_dir / MIGRATION_FILENAME


def default_state() -> dict[str, Any]:
    phases = {pid: {"status": "complete" if pid == "0" else "pending"} for pid in PHASE_IDS}
    return {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_phase": 1,
        "phases": phases,
        "rollback": {
            "provider": "gemini",
            "use_factory": True,
            "vision_adapter": False,
        },
    }


def read_migration_state(state_dir: Path | None = None) -> dict[str, Any]:
    path = migration_state_path(state_dir)
    if not path.is_file():
        return default_state()
    try:
        raw = json.loads(path.read_text())
        if raw.get("schema_version") != MIGRATION_SCHEMA_VERSION:
            merged = default_state()
            merged.update(raw)
            merged["schema_version"] = MIGRATION_SCHEMA_VERSION
            return merged
        return raw
    except (json.JSONDecodeError, OSError):
        pass
    return default_state()


def write_migration_state(state: dict[str, Any], state_dir: Path | None = None) -> None:
    path = migration_state_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {**state, "updated_at": datetime.now(timezone.utc).isoformat()}
    tmp = path.with_suffix(f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.rename(path)


def mark_phase(
    phase: str | int,
    status: str,
    notes: str | None = None,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    state = read_migration_state(state_dir)
    key = str(phase)
    record = dict(state.get("phases", {}).get(key, {"status": "pending"}))
    now = datetime.now(timezone.utc).isoformat()
    record["status"] = status
    if notes:
        record["notes"] = notes
    if status == "in_progress" and "started_at" not in record:
        record["started_at"] = now
    if status in ("complete", "rolled_back"):
        record["completed_at"] = now
    state.setdefault("phases", {})[key] = record
    if status == "complete":
        try:
            n = int(phase)
            if n >= state.get("current_phase", 1):
                state["current_phase"] = min(n + 1, 4)
        except ValueError:
            pass
    write_migration_state(state, state_dir)
    return state
