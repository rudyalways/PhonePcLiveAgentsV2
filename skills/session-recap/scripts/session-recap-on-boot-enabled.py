#!/usr/bin/env python3
"""Resolve whether boot session-recap should run during /schedule-crons.

Precedence:

    environment override > session-recap/manifest.json config default

Shipped default is ON. Set SUTANDO_SESSION_RECAP_ON_BOOT=0 to skip.
Boot path uses the fast mechanical ``boot-recap.py`` (no LLM); deep LLM
recap stays on-demand via ``/session-recap``.

Invalid values fail closed (disabled). A missing script is treated as
enabled by consumers so boot catchup does not silently disappear.

Usage:
  python3 skills/session-recap/scripts/session-recap-on-boot-enabled.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Mapping, Optional

ENV_NAME = "SUTANDO_SESSION_RECAP_ON_BOOT"
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


def session_recap_on_boot_enabled(
    environ: Optional[Mapping[str, str]] = None,
    manifest_path: Path = MANIFEST_PATH,
) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get(ENV_NAME)
    if raw is None:
        raw = _manifest_default(manifest_path)
    if raw is None:
        return True  # fail-open when undeclared
    normalized = (raw or "").strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return False


def main() -> int:
    enabled = session_recap_on_boot_enabled()
    print("enabled" if enabled else "disabled")
    raw = os.environ.get(ENV_NAME)
    if raw is not None and raw.strip().lower() not in TRUE_VALUES | FALSE_VALUES:
        print(
            f"{ENV_NAME}={raw!r} is invalid; boot session-recap is disabled",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
