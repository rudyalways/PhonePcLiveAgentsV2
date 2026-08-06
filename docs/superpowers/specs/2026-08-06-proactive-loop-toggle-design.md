# proactive-loop 整体开关

**日期**：2026-08-06  
**状态**：已实现（含 skill unlink 修订）  
**修订**：2026-08-06 — 关闭时 unlink Claude skill symlink（owner：off means off）

## 问题

`proactive-loop` 是 Sutando 的自主工作引擎，由 `crons.json` 的 `main-loop`
条目驱动（默认 `*/5 * * * *`）。每次触发都会唤醒 core 跑完整一轮，其中步骤
0.7「Reconstruct context」要求每一遍都重新读取 `current-track.md`、Discord
频道消息、`pending-questions.md`、`relay-*.md` 和 `build_log.md` —— 这是持续
token 消耗的主要来源。

仓库已有 `SUTANDO_SELF_DEVELOPMENT_ENABLED`，但它在 SKILL.md 的**步骤 3.5**
才生效，只跳过步骤 4–8、10、11。步骤 0–3.5（含最贵的 0.7）照跑不误。所以它
无法回答「我这台机器不需要自主循环」这个诉求。

仅拦 cron 注册仍不够：`$CLAUDE_CONFIG_DIR/skills/proactive-loop` 的 symlink
还在时，技能仍会出现在 Claude 发现面，`/startup` / 手动 `/proactive-loop`
仍可烧 token；且 symlink 可能指向**兄弟 checkout**（陈旧路径）。

## 目标

提供一个开关，关闭后：

1. 循环不被 cron 唤醒（零定时 token 消耗）
2. skill symlink **不存在**（不可被发现 / 随意调用）
3. 不影响任务响应与故障告警（omni-exp / Discord / Telegram / voice、health-check launchd、其他 cron）

## 非目标

- 不做中间档位（如 `minimal`）。
- 不改动 `SUTANDO_SELF_DEVELOPMENT_ENABLED`（正交：管「醒了之后干不干活」）。

## 热关闭（已实现修订）

把 `.env` 写成 `SUTANDO_PROACTIVE_LOOP_ENABLED=0` 后，**不必等「从未开启」**：

1. **Skill 入口门** — `/proactive-loop` 与每轮 pass 在步骤 0 之前先 `source .env` + gate；`disabled` 则立刻结束，不跑 0.7。
2. **Cron 拆除** — `/schedule-crons` 在关闭态 `CronDelete` 已注册的 `main-loop` / `/proactive-loop`（含 `*/10` 兜底）。
3. **Skill unlink** — 下次 `start-cli` / `install.sh` 仍会 unlink 发现面。

symlink 同步仍建议重启 core；但已武装的 cron / 手动调用也会被入口门拦住。

## 行为

| | 开启（默认） | 关闭 |
|---|---|---|
| 定时唤醒 core | 每 5 分钟 | 不发生 |
| 上下文重建（步骤 0.7） | 每次 | 不发生 |
| 自主挑活（步骤 4–8） | 按配额 | 不发生 |
| Discord 频道巡视（步骤 10） | 每次 | 不发生 |
| Skill symlink | 指向**本 repo** `skills/proactive-loop` | **已 unlink** |
| 手动 `/proactive-loop` | 可用 | **不可发现** |
| 语音 / Discord / Telegram / omni-exp 任务 | 正常处理 | **正常处理** |
| 其他 host cron（morning-briefing 等） | 正常 | 正常 |
| 健康检查 | 循环内跑 | **由独立 launchd job 跑** |

**相对初版修订**：关闭态下**不再**保留手动 `/proactive-loop`。需要时把
`SUTANDO_PROACTIVE_LOOP_ENABLED=1` 写回 `.env` 并重启 core。

关闭态下仍能响应任务和告警，因为这两条路本就不经过 proactive-loop：

- 任务处理走 task 文件 + watcher/feeder / Stop hook
- 健康检查有独立的 `src/install-health-check-launchd.sh`

## 设计

### 开关读取

`skills/proactive-loop/scripts/proactive-loop-enabled.py`：

- 优先级 `env > manifest.json config`，遵循 skill-config 约定。
- 打印 `enabled` 或 `disabled` 到 stdout。
- 环境变量名 `SUTANDO_PROACTIVE_LOOP_ENABLED`。
- 真值 `{1, true, yes, on, enabled}`，假值 `{0, false, no, off, disabled}`，
  之外 fail-closed → disabled。

`manifest.json` `config` 默认 `"SUTANDO_PROACTIVE_LOOP_ENABLED": "1"`。

使用者在 repo `.env`（gitignore）写：

```bash
SUTANDO_PROACTIVE_LOOP_ENABLED=0
```

见 `.env.example`。

### 三层强制（缺一不可）

1. **Skill discovery** — `sync-skill-link.sh` 在启动路径 unlink/link  
2. **Cron registration + disarm** — `/schedule-crons` 跳过 `main-loop` 与 `*/10` 兜底，并 `CronDelete` 已存在的 loop job  
3. **Skill 入口门** — 即使 skill 仍在 / cron 仍偶发触发，`SKILL.md` 在步骤 0 前 abort

### Skill sync

`skills/proactive-loop/scripts/sync-skill-link.sh`：

- 经 gate 脚本判定 enabled/disabled
- 若进程未 export 该变量，从 repo `.env` 读一行作为安全网
- 同步这些根目录（去重）：
  - `<workspace>/.claude-sutando/skills`（sutando-core CCD，主路径）
  - `$CLAUDE_CONFIG_DIR/skills`（若已设且不同）
  - `~/.claude/skills`（交互 CLI）
- disabled：只删 **symlink**（绝不 `rm -rf` 真目录）
- enabled：`ln -s` 到 **本 repo** `$REPO/skills/proactive-loop`（替换陈旧兄弟路径）

测试可用 `SUTANDO_PROACTIVE_LOOP_SYNC_ROOTS`（冒号分隔）覆盖同步根，避免碰
真实 CCD。

### 调用点

| 调用方 | 何时 | `.env` |
|---|---|---|
| `skills/install.sh` | 每次 `startup.sh`；通用循环跳过 proactive-loop | 若未设变量则 source `.env` |
| `src/agent/start-cli.sh` | 每次 core start / `--restart` | 已 source `.env` |
| `src/session-guardian.sh` | 若启用：死后/卡住重启 | source `.env` 后走 `start-cli.sh --restart`（不再硬编码 `/proactive-loop`） |
| `skills/schedule-crons/SKILL.md` | core 会话内 | 进程环境已带变量 |

### Cron 拦截点

`skills/schedule-crons/SKILL.md` 两道门（共用 gate 脚本）：

1. **步骤 3** — 关闭时跳过 `prompt_skill: proactive-loop` / 正文含 `/proactive-loop` 的条目，并 `CronDelete` 已注册的 loop job  
2. **步骤 4** — 关闭时不补 `*/10` 兜底，并 log
   `proactive-loop fallback skipped (SUTANDO_PROACTIVE_LOOP_ENABLED=0)`

第二道门关键：只做第一道时，步骤 4 会以为「用户忘了配」而自己补一条回来。
拆除（CronDelete）关键：只跳过注册时，先前已武装的 `main-loop` 会继续烧钱。

### 为何不用「保留 crons.json 条目但禁用」

`crons.json` 无 `enabled` 字段。门拦的是「注册」动作，不是模板内容。
`crons.json` 里仍可有 `main-loop` 声明 —— 那不代表已注册。验收只看会话
`CronList`，不要用 `crons.json` 内容做证明。

### 数据流

```
bash src/startup.sh
  → configure_startup_runtime（source .env）
  → skills/install.sh
        → sync-skill-link.sh → gate
              ├─ disabled → unlink skill
              └─ enabled  → link → 本 repo
  → exec src/agent/start-cli.sh
        → source .env
        → sync-skill-link.sh（再跑一遍）
        → sutando-core
              → /startup → /schedule-crons → gate
                    ├─ disabled → 跳过 main-loop + */10
                    └─ enabled  → 注册 main-loop
```

仅重启 core（菜单栏 / health recovery）：`start-cli.sh` 一条路径即可。

## 错误处理

| 情况 | 行为 | 理由 |
|---|---|---|
| gate 脚本不存在 | sync/schedule-crons 视为 enabled | 不因可选脚本缺失静默关掉循环 |
| 值无效（如 `maybe`） | fail-closed → disabled | 与 self-development 一致 |
| skill 是真目录非 symlink | 保留并警告 | 不破坏本地 copy install |
| symlink 指向兄弟 checkout | enabled 时换成开仓；disabled 时 unlink | 避免陈旧技能 |

## 测试

- `tests/proactive-loop-toggle.test.py` — gate 脚本  
- `tests/proactive-loop-sync-skill-link.test.py` — sync：disabled 缺链 / enabled 指本 repo / 真目录不删

手动验收：

```bash
grep PROACTIVE .env   # =0
bash src/agent/start-cli.sh --restart
test ! -e "$(bash scripts/sutando-config.sh workspace)/.claude-sutando/skills/proactive-loop"
python3 skills/proactive-loop/scripts/proactive-loop-enabled.py   # disabled
```

## 影响面

| 文件 | 改动 |
|---|---|
| `skills/proactive-loop/scripts/proactive-loop-enabled.py` | gate |
| `skills/proactive-loop/scripts/sync-skill-link.sh` | skill unlink/link |
| `skills/proactive-loop/manifest.json` | config 默认 |
| `skills/install.sh` | 跳过通用安装 proactive-loop；调用 sync |
| `src/agent/start-cli.sh` | source `.env` 后调用 sync |
| `src/session-guardian.sh` | 经 start-cli 重启；禁硬编码 `/proactive-loop` |
| `skills/schedule-crons/SKILL.md` | 步骤 3、4 门 + CronDelete |
| `skills/proactive-loop/SKILL.md` | 入口 / 每 pass 0.0 kill switch |
| `skills/startup/SKILL.md` | 注明 fallback 受 gate 约束 |
| `.env.example` | 文档 |
| `tests/proactive-loop-toggle.test.py` | gate |
| `tests/proactive-loop-sync-skill-link.test.py` | sync |
