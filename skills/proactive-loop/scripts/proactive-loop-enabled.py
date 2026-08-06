#!/usr/bin/env python3
"""Resolve whether the proactive loop should be scheduled at all.

Precedence follows the skill-config convention:

    environment override > manifest.json config default

This is the WHOLE-LOOP gate: when disabled, `/schedule-crons` neither
registers the `main-loop` entry nor arms the `*/10` bootstrap fallback, deletes
any already-armed loop cron, and `proactive-loop/SKILL.md` aborts before step 0
so an already-on loop stops costing tokens. It is deliberately separate from
`SUTANDO_SELF_DEVELOPMENT_ENABLED`, which only gates SKILL.md steps 4-8/10/11
*after* a pass has already started (and after step 0.7's context
reconstruction, the dominant token cost).

The shipped manifest defaults to enabled. An invalid or unreadable value fails
closed, matching `self-development-enabled.py` — two sibling gates with
opposite fallback semantics would be a bug source. A missing script file is a
different case and is handled by the consumer: `skills/schedule-crons/SKILL.md`
treats an absent script as `enabled`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Mapping, Optional

ENV_NAME = "SUTANDO_PROACTIVE_LOOP_ENABLED"
TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})
FALSE_VALUES = frozenset({"0", "false", "no", "off", "disabled"})
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.json"


def _manifest_default(manifest_path: Path = MANIFEST_PATH) -> Optional[str]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        config = manifest.get("config", {})
        if not isinstance(config, dict):
            return None
        value = config.get(ENV_NAME)
    except (OSError, ValueError, TypeError):
        return None
    return str(value) if value is not None else None


def proactive_loop_enabled(
    environ: Optional[Mapping[str, str]] = None,
    manifest_path: Path = MANIFEST_PATH,
) -> bool:
    """Return whether the proactive loop should be scheduled.

    Missing configuration uses the manifest default. Missing/broken manifest
    data and unrecognized values fail closed.
    """

    env = os.environ if environ is None else environ
    raw = env.get(ENV_NAME)
    if raw is None:
        raw = _manifest_default(manifest_path)
    normalized = (raw or "").strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return False


def main() -> int:
    enabled = proactive_loop_enabled()
    print("enabled" if enabled else "disabled")
    raw = os.environ.get(ENV_NAME)
    if raw is not None and raw.strip().lower() not in TRUE_VALUES | FALSE_VALUES:
        print(
            f"{ENV_NAME}={raw!r} is invalid; the proactive loop is disabled",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
