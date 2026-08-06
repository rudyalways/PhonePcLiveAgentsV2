#!/usr/bin/env python3
"""OMNI_EXP_MODE: normal_with_gui | no_gui | no_gui_html_output."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from omni_exp_mode import (  # noqa: E402
    build_omni_exp_instructions,
    normalize_omni_exp_mode,
    task_system_suffix,
    work_tool_description,
)

failures: list[str] = []


def check(label: str, cond: bool) -> None:
    if cond:
        print(f"PASS  {label}")
    else:
        print(f"FAIL  {label}")
        failures.append(label)


check(
    "default → no_gui_html_output",
    normalize_omni_exp_mode("") == "no_gui_html_output",
)
check(
    "unknown → no_gui_html_output",
    normalize_omni_exp_mode("weird") == "no_gui_html_output",
)
check(
    "alias normal → normal_with_gui",
    normalize_omni_exp_mode("normal") == "normal_with_gui",
)
check("alias nogui → no_gui", normalize_omni_exp_mode("nogui") == "no_gui")
check(
    "alias no_gui_html → no_gui_html_output",
    normalize_omni_exp_mode("no_gui_html") == "no_gui_html_output",
)

gui = build_omni_exp_instructions("normal_with_gui")
no_gui = build_omni_exp_instructions("no_gui")
html = build_omni_exp_instructions("no_gui_html_output")

# normal = baseline voice prompt; core stamp is the true no-op.
check("normal_with_gui baseline lists browser/apps", "Open/close browser or apps" in gui)
check(
    "normal_with_gui core stamp is no-op",
    task_system_suffix("normal_with_gui") == "",
)
check(
    "normal_with_gui work tool is baseline",
    "open browser/URL" in work_tool_description("normal_with_gui"),
)

# Restricted modes: thin Qwen hint; fat policy only on core stamp.
check("no_gui voice hint is thin", "MODE: no_gui (active)" in no_gui)
check(
    "no_gui voice does not paste core HTML playbook",
    "workspace/data/omni-html" not in no_gui,
)
check(
    "no_gui core stamp has execution policy",
    "SEARCH: do not open Chrome" in task_system_suffix("no_gui"),
)
check("html voice hint is thin", "MODE: no_gui_html_output (active)" in html)
check(
    "html core stamp requires write+open HTML",
    "write a self-contained HTML" in task_system_suffix("no_gui_html_output")
    and "open that local file" in task_system_suffix("no_gui_html_output"),
)
check(
    "html work tool one-liner",
    "local HTML" in work_tool_description("no_gui_html_output")
    and "open that file" in work_tool_description("no_gui_html_output"),
)
check(
    "no_gui work tool one-liner",
    "without GUI" in work_tool_description("no_gui"),
)

override = "CUSTOM PROMPT ONLY"
check(
    "INSTRUCTIONS override wins",
    build_omni_exp_instructions("no_gui", override=override) == override,
)

if failures:
    raise SystemExit(f"{len(failures)} failure(s): {', '.join(failures)}")
print("\nall tests passed")
