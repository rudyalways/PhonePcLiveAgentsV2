#!/usr/bin/env python3
"""Resolve whether a named host-cron entry should be registered / run.

Precedence (skill-config convention):

    environment override > schedule-crons/manifest.json config default

Shipped defaults for optional owner digests / hygiene crons are OFF (omni-exp /
work-bridge hosts should not register them unless opted in):
morning-briefing, daily-insight, pending-questions, sync-memory.

Invalid or unreadable values fail closed (disabled). A missing script is also
treated as disabled by consumers — opposite of proactive-loop's "missing =
enabled" so default-off features cannot silently arm.

Usage:
  python3 skills/schedule-crons/scripts/cron-entry-enabled.py <entry>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Mapping, Optional

TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})
FALSE_VALUES = frozenset({"0", "false", "no", "off", "disabled"})
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.json"

# name → env var. Defaults live in schedule-crons/manifest.json config.
ENTRY_ENV = {
    "morning-briefing": "SUTANDO_MORNING_BRIEFING_ENABLED",
    "daily-insight": "SUTANDO_DAILY_INSIGHT_ENABLED",
    "pending-questions": "SUTANDO_PENDING_QUESTIONS_CRON_ENABLED",
    "sync-memory": "SUTANDO_SYNC_MEMORY_CRON_ENABLED",
}


def _manifest_default(
    env_name: str, manifest_path: Path = MANIFEST_PATH
) -> Optional[str]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        config = manifest.get("config", {})
        if not isinstance(config, dict):
            return None
        value = config.get(env_name)
    except (OSError, ValueError, TypeError):
        return None
    return str(value) if value is not None else None


def cron_entry_enabled(
    entry: str,
    environ: Optional[Mapping[str, str]] = None,
    manifest_path: Path = MANIFEST_PATH,
) -> bool:
    """Return whether *entry* should be scheduled / executed."""
    env_name = ENTRY_ENV.get(entry)
    if env_name is None:
        raise ValueError(f"unknown cron entry gate: {entry!r}")

    env = os.environ if environ is None else environ
    raw = env.get(env_name)
    if raw is None:
        raw = _manifest_default(env_name, manifest_path)
    normalized = (raw or "").strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return False


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        names = ", ".join(sorted(ENTRY_ENV))
        print(f"usage: cron-entry-enabled.py <{'|'.join(sorted(ENTRY_ENV))}>", file=sys.stderr)
        print(f"known: {names}", file=sys.stderr)
        return 2
    entry = args[0].strip()
    if entry not in ENTRY_ENV:
        print(f"unknown entry {entry!r}; known: {', '.join(sorted(ENTRY_ENV))}", file=sys.stderr)
        return 2

    env_name = ENTRY_ENV[entry]
    enabled = cron_entry_enabled(entry)
    print("enabled" if enabled else "disabled")
    raw = os.environ.get(env_name)
    if raw is not None and raw.strip().lower() not in TRUE_VALUES | FALSE_VALUES:
        print(
            f"{env_name}={raw!r} is invalid; {entry} is disabled",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
