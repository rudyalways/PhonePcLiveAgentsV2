# proactive-loop 整体开关 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `SUTANDO_PROACTIVE_LOOP_ENABLED` 开关，关闭时不注册 `main-loop` cron 也不触发兜底补建，使 proactive-loop 彻底不被唤醒。

**Architecture:** 一个读配置的 Python 脚本（`env > manifest.json` 优先级），在 `skills/schedule-crons/SKILL.md` 的两处（注册循环、兜底补建）作为门使用。不触碰 `src/`，不改动任何运行时代码路径。

**Tech Stack:** Python 3（stdlib only：`json` / `os` / `sys` / `pathlib` / `typing`）；Markdown 指令文件。

## ⚠️ 与 spec 的一处偏离（需确认）

Spec 的「错误处理」表写道：**manifest 读取失败 → 回退默认 `1`**。

本计划改为 **fail-closed（disabled）**，与同目录的
`self-development-enabled.py:57` 保持一致 —— 该脚本的现有测试
（`tests/proactive-loop-self-development.test.py:52`）已明确契约
`"missing manifest fails closed"`。两个同目录同形脚本若采用相反的兜底语义，
是可预见的 bug 源。

实际影响很小：更可能发生的故障是「脚本未部署」，而那一路由 SKILL.md 的门
处理，明确视为 `enabled`（见 Task 2）。

若不接受此偏离，请在执行前告知，改回 `return True` 并同步修改 Task 1 的
测试断言。

## Global Constraints

- 环境变量名：`SUTANDO_PROACTIVE_LOOP_ENABLED`（exact）
- manifest 默认值：`"1"`（字符串，与同目录既有键一致）
- 优先级：`环境变量 > manifest.json config`，遵循 CLAUDE.md 的
  skill-config 约定（*"Skill config goes in the skill's `manifest.json`
  `config` block — not ad-hoc env vars"*）
- 真值集合：`{"1", "true", "yes", "on", "enabled"}`（大小写不敏感）
- 假值集合：`{"0", "false", "no", "off", "disabled"}`（大小写不敏感）
- 两集合之外的值一律 fail-closed（disabled）
- Python 3.9 兼容 —— 仓库有 `.github/workflows/python39-compat.yml` CI 检查；
  用 `from __future__ import annotations`，不用 `X | Y` 类型语法
- 不修改 `src/` 下任何文件
- 不修改 `SUTANDO_SELF_DEVELOPMENT_ENABLED` 的任何行为
- 分支 `feat/proactive-loop-toggle` 已建，spec 已提交（commit `a204fe2`）
- 提交信息结尾附：`Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## File Structure

| 文件 | 职责 |
|---|---|
| `skills/proactive-loop/scripts/proactive-loop-enabled.py` | 纯策略解析：读 env/manifest，输出 `enabled`/`disabled`。无副作用，可被 import 也可当 CLI 跑 |
| `skills/proactive-loop/manifest.json` | 声明开关的默认值 |
| `skills/schedule-crons/SKILL.md` | 消费方：两处门，决定是否注册/补建 cron |
| `tests/proactive-loop-toggle.test.py` | 契约测试：脚本行为 + SKILL.md 两处门存在性 |

脚本与 manifest 是一个不可分的交付物（脚本无 manifest 默认值就永远
fail-closed），故合并为 Task 1。SKILL.md 的门是独立可评审的交付物 —— 评审者
可能认可 Task 1 的脚本却否决 Task 2 的措辞，故拆开。

---

### Task 1: 开关脚本 + manifest 默认值

**Files:**
- Create: `skills/proactive-loop/scripts/proactive-loop-enabled.py`
- Modify: `skills/proactive-loop/manifest.json`
- Test: `tests/proactive-loop-toggle.test.py`

**Interfaces:**
- Consumes: 无（本任务是起点）
- Produces:
  - `proactive_loop_enabled(environ: Optional[Mapping[str, str]] = None, manifest_path: Path = MANIFEST_PATH) -> bool`
  - `main() -> int`（CLI 入口，stdout 打印 `enabled` 或 `disabled`）
  - 模块级常量 `ENV_NAME: str = "SUTANDO_PROACTIVE_LOOP_ENABLED"`
  - Task 2 依赖的是 CLI 契约：`python3 skills/proactive-loop/scripts/proactive-loop-enabled.py` 打印 `enabled` / `disabled`，退出码恒为 0

- [ ] **Step 1: 写失败的测试**

创建 `tests/proactive-loop-toggle.test.py`：

```python
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

if failures:
    raise SystemExit(f"{len(failures)} failure(s): {', '.join(failures)}")
print("\nall tests passed")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python3 tests/proactive-loop-toggle.test.py
```

Expected: 因 `SCRIPT` 不存在，`spec.loader.exec_module(gate)` 抛
`FileNotFoundError`，测试在加载阶段即中止。

- [ ] **Step 3: 写脚本**

创建 `skills/proactive-loop/scripts/proactive-loop-enabled.py`：

```python
#!/usr/bin/env python3
"""Resolve whether the proactive loop should be scheduled at all.

Precedence follows the skill-config convention:

    environment override > manifest.json config default

This is the WHOLE-LOOP gate: when disabled, `/schedule-crons` neither
registers the `main-loop` entry nor arms the `*/10` bootstrap fallback, so the
loop is never woken and costs nothing. It is deliberately separate from
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
```

- [ ] **Step 4: 加 manifest 默认值**

修改 `skills/proactive-loop/manifest.json` 的 `config` 块 —— 保留既有键，
新增一个：

```json
  "config": {
    "SUTANDO_SELF_DEVELOPMENT_ENABLED": "1",
    "SUTANDO_PROACTIVE_LOOP_ENABLED": "1"
  },
```

- [ ] **Step 5: 加可执行位**

```bash
chmod +x skills/proactive-loop/scripts/proactive-loop-enabled.py
```

- [ ] **Step 6: 运行测试确认通过**

```bash
python3 tests/proactive-loop-toggle.test.py
```

Expected: 全部 `PASS`，结尾 `all tests passed`。

- [ ] **Step 7: 确认未破坏既有开关**

```bash
python3 tests/proactive-loop-self-development.test.py
```

Expected: 全部 `PASS`（本任务给 manifest 加了键，不能影响既有断言）。

- [ ] **Step 8: 提交**

```bash
git add skills/proactive-loop/scripts/proactive-loop-enabled.py \
        skills/proactive-loop/manifest.json \
        tests/proactive-loop-toggle.test.py
git commit -m "$(cat <<'EOF'
feat(proactive-loop): add SUTANDO_PROACTIVE_LOOP_ENABLED gate script

Resolves the whole-loop toggle with env > manifest precedence, mirroring
self-development-enabled.py. Ships enabled by default; invalid and unreadable
values fail closed so the two sibling gates behave identically.

Consumer wiring lands in the next commit.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: schedule-crons 的两处门

**Files:**
- Modify: `skills/schedule-crons/SKILL.md:43-52`
- Test: `tests/proactive-loop-toggle.test.py`（追加断言）

**Interfaces:**
- Consumes: Task 1 的 CLI 契约 ——
  `python3 skills/proactive-loop/scripts/proactive-loop-enabled.py`
  打印 `enabled` 或 `disabled`，退出码恒为 0
- Produces: 无（终点任务）

- [ ] **Step 1: 追加失败的测试**

在 `tests/proactive-loop-toggle.test.py` 的 `if failures:` 之前插入：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python3 tests/proactive-loop-toggle.test.py
```

Expected: 4 条新断言 `FAIL`，结尾
`4 failure(s): both gates invoke the toggle script, ...`

- [ ] **Step 3: 加第一道门（注册步骤）**

在 `skills/schedule-crons/SKILL.md` 第 3 步的项目符号列表中，紧跟在
`- **Skip any entry with `"launchd": true`**` 那一条之后，插入新的一条：

```markdown
   - **Skip the proactive-loop entry when the whole-loop toggle is off.** Run `python3 skills/proactive-loop/scripts/proactive-loop-enabled.py` (prints `enabled` / `disabled`). When it prints `disabled`, skip any entry whose `prompt_skill` is `proactive-loop` or whose `prompt` body invokes `/proactive-loop`; every other entry registers normally. When the script is absent, treat it as `enabled` — a missing optional script must not silently stop the owner's loop. This is the WHOLE-LOOP gate and is distinct from `SUTANDO_SELF_DEVELOPMENT_ENABLED`, which only narrows what a pass does once it has already started.
```

- [ ] **Step 4: 加第二道门（兜底步骤）**

把 `skills/schedule-crons/SKILL.md` 第 52 行整段替换为：

```markdown
4. **Fallback — ensure `/proactive-loop` is scheduled (unless the toggle is off).** First run `python3 skills/proactive-loop/scripts/proactive-loop-enabled.py`. If it prints `disabled`, SKIP this entire step and log exactly one line: `proactive-loop fallback skipped (SUTANDO_PROACTIVE_LOOP_ENABLED=0)`. Without this guard the toggle silently self-reverts — step 3 skips the entry, this step then sees no `/proactive-loop` job, concludes the owner forgot to configure one, and arms a `*/10` cron; observed twice on 2026-08-06 while validating the toggle. A missing script counts as `enabled`, same as step 3. Otherwise, after step 3, check whether any job in `crons.json` references `/proactive-loop` (either `"prompt_skill": "proactive-loop"` or a `"prompt"` whose body invokes the loop). If none does, call `CronCreate` directly with `cron: "*/10 * * * *"` and `prompt: "/proactive-loop"` as a bootstrap-safety net. Rationale: post-#954 the CLI boots with `-- "/schedule-crons"` and exits after step 5, so if `crons.json` is missing/empty/forgot-to-include-the-loop-entry the session goes idle with no recurring work driver. The fallback guarantees the loop runs at least every 10 min regardless of config state. Idempotent: if the user has a custom `*/5 * * * *` or `*/15 * * * *` entry, that satisfies the check and the fallback is skipped (no duplicate cron).
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python3 tests/proactive-loop-toggle.test.py
```

Expected: 全部 `PASS`，结尾 `all tests passed`。

- [ ] **Step 6: 实跑验证关闭态**

markdown 指令无法单测，用真实启动验证。

```bash
WS="$(bash scripts/sutando-config.sh workspace)"
HOST="$(bash scripts/sutando-config.sh host-label)"
rm -f "$WS/hosts/$HOST/crons.json"
tmux -S /tmp/sutando-tmux.sock kill-server 2>/dev/null
SUTANDO_PROACTIVE_LOOP_ENABLED=0 SUTANDO_ACCEPT_BYPASS_PERMISSIONS=1 \
  bash src/agent/start-cli.sh
```

等 core 跑完 `/startup`（约 2-3 分钟）后，在 core 会话里运行 `CronList`。

Expected: 列表中**没有**任何引用 `/proactive-loop` 的 job（也没有 `*/5` 或
`*/10` 的循环 job）；其他 cron（morning-briefing 等）正常出现。

> **不要用 `crons.json` 的内容做验收。** 该文件是 core 从
> `skills/schedule-crons/crons.example.json` 整体拷贝来的**配置模板**，
> `main-loop` 条目存在其中只表示「被声明过」，不表示「被注册了」。2026-08-06
> 首次执行本计划时我就是这么误判的 —— 文件里有 `main-loop`，但 CronList 为
> 空，两道门其实都正常工作。运行时状态只看 CronList。

补充证据（可选）：在 core 的 scrollback 里应能看到步骤 4 的日志行
`proactive-loop fallback skipped (SUTANDO_PROACTIVE_LOOP_ENABLED=0)`，
以及所有 `CronCreate` 调用中没有 proactive-loop。

- [ ] **Step 7: 实跑验证开启态**

确认开关不会误伤默认行为。

```bash
WS="$(bash scripts/sutando-config.sh workspace)"
HOST="$(bash scripts/sutando-config.sh host-label)"
rm -f "$WS/hosts/$HOST/crons.json"
tmux -S /tmp/sutando-tmux.sock kill-server 2>/dev/null
SUTANDO_ACCEPT_BYPASS_PERMISSIONS=1 bash src/agent/start-cli.sh
```

等 `/startup` 跑完后，在 core 会话里跑 `/cron-list`。
Expected: 出现引用 `/proactive-loop` 的 job（来自 `crons.example.json` 的
`main-loop` 或兜底补建的 `*/10`）。

> 验证完毕后按需清理：`rm -f "$WS/hosts/$HOST/crons.json"` 并重启 core，
> 恢复到你日常使用的状态。

- [ ] **Step 8: 提交**

```bash
git add skills/schedule-crons/SKILL.md tests/proactive-loop-toggle.test.py
git commit -m "$(cat <<'EOF'
feat(schedule-crons): gate loop registration on SUTANDO_PROACTIVE_LOOP_ENABLED

Two gates, both reading proactive-loop-enabled.py:

- step 3 skips the proactive-loop entry when the toggle is off
- step 4 skips the */10 bootstrap fallback for the same reason

The second gate is what makes the toggle stick. Without it step 3 removes the
entry, step 4 sees no /proactive-loop job, concludes the owner forgot to
configure one, and arms a */10 cron anyway — observed twice on 2026-08-06.

A missing script counts as enabled in both gates, so an incomplete deployment
never silently stops the owner's loop.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 验收标准

| 条件 | 期望 |
|---|---|
| `python3 tests/proactive-loop-toggle.test.py` | all tests passed |
| `python3 tests/proactive-loop-self-development.test.py` | all tests passed（未回归） |
| `SUTANDO_PROACTIVE_LOOP_ENABLED=0` 启动 core | `CronList` 中无 proactive-loop job（`crons.json` 里有 `main-loop` 条目是正常的 —— 那是配置模板，非注册状态） |
| 不设该变量启动 core | `CronList` 中出现引用 `/proactive-loop` 的 job（默认行为不变） |
| `git diff --stat main -- src/` | 空（不触碰 `src/`） |
