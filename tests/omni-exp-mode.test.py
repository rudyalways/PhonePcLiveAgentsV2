#!/usr/bin/env python3
"""OMNI_EXP_MODE: normal_with_gui | no_gui | no_gui_html_output."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from omni_exp_mode import (  # noqa: E402
    build_omni_exp_instructions,
    format_work_task,
    normalize_omni_exp_mode,
    scene_prompt_for_mode,
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
    "default → research",
    normalize_omni_exp_mode("") == "research",
)
check(
    "unknown → research",
    normalize_omni_exp_mode("weird") == "research",
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
check("research canonical", normalize_omni_exp_mode("research") == "research")
check(
    "alias research_mode → research",
    normalize_omni_exp_mode("research_mode") == "research",
)

gui = build_omni_exp_instructions("normal_with_gui")
no_gui = build_omni_exp_instructions("no_gui")
html = build_omni_exp_instructions("no_gui_html_output")
research = build_omni_exp_instructions("research")

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
check("research voice hint", "MODE: research (active)" in research)
check(
    "research scene override",
    "SCENE CHANGE OVERRIDE (research mode)" in research,
)
stamp = task_system_suffix("research")
check(
    "research core stamp has MD then HTML deck",
    "MARKDOWN FIRST" in stamp
    and "HTML DECK" in stamp
    and "SOTA papers" in stamp,
)
check(
    "research HTML slide topics specified",
    "SLIDE 1 — 全景 / 论点" in stamp
    and "ONE MAJOR TOPIC PER SLIDE" in stamp
    and "主题 i/N" in stamp,
)
check(
    "research forbids fake 2/2 when many topics",
    "FORBIDDEN" in stamp and "2/2 misleading" in stamp,
)
check(
    "research narration 10–20s",
    "10–20 seconds" in stamp or "10–20s" in stamp,
)
check(
    "research HTML requires Simplified Chinese",
    "Simplified Chinese" in stamp
    and "zh-CN" in stamp
    and "播放/暂停" in stamp
    and "上一页" in stamp
    and "下一页" in stamp,
)
check(
    "research HTML requires human prev/next nav",
    "D6) Human navigation" in stamp
    and "data-action=prev" in stamp
    and "data-action=next" in stamp
    and "FORBIDDEN: auto-play-only" in stamp,
)
check(
    "research HTML format tokens",
    "100vw" in stamp
    and "human-friendly" in stamp.lower()
    and "#f7f4ef" in stamp
    and "#1c1917" in stamp
    and "high contrast" in stamp.lower(),
)
check(
    "research auto-play adapts to local TTS end",
    "onended → next slide" in stamp
    and "utterance.onend → next slide" in stamp
    and "clamp to [10s, 20s]" in stamp
    and "下载中" in stamp,
)
check(
    "research auto-explain downloads model in HTML",
    "IndexedDB" in stamp
    and "正在下载语音模型" in stamp
    and "on-device" in stamp
    and 'data-narration="' in stamp
    and "#explain" in stamp
    and "NOT depend on cloud TTS" in stamp,
)
check(
    "research work tool mentions deck",
    "auto-play HTML deck" in work_tool_description("research"),
)
check(
    "research scene prompt defaults NO_SPEAK",
    "Default reply: [[NO_SPEAK]]" in scene_prompt_for_mode("research")
    and "Call work ONLY" in scene_prompt_for_mode("research")
    and "Research capture:" in scene_prompt_for_mode("research"),
)
check(
    "default scene prompt keeps NO_SPEAK",
    "[[NO_SPEAK]]" in scene_prompt_for_mode("normal_with_gui")
    and "Call work ONLY" not in scene_prompt_for_mode("normal_with_gui"),
)
check(
    "research voice prefers accumulate",
    "Prefer accumulating" in research or "auto-buffers ASR" in research,
)
check(
    "research formats task tag",
    format_work_task("research", "look into transformers").startswith("[research-mode]"),
)
check(
    "research does not double-tag",
    format_work_task("research", "Research capture: foo") == "Research capture: foo",
)
override = "CUSTOM PROMPT ONLY"
check(
    "INSTRUCTIONS override wins",
    build_omni_exp_instructions("no_gui", override=override) == override,
)

if failures:
    raise SystemExit(f"{len(failures)} failure(s): {', '.join(failures)}")
print("\nall tests passed")
