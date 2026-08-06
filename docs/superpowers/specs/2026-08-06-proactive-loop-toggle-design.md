# proactive-loop 整体开关

**日期**：2026-08-06
**状态**：已批准，待实现

## 问题

`proactive-loop` 是 Sutando 的自主工作引擎，由 `crons.json` 的 `main-loop`
条目驱动（默认 `*/5 * * * *`）。每次触发都会唤醒 core 跑完整一轮，其中步骤
0.7「Reconstruct context」要求每一遍都重新读取 `current-track.md`、Discord
频道消息、`pending-questions.md`、`relay-*.md` 和 `build_log.md` —— 这是持续
token 消耗的主要来源。

仓库已有 `SUTANDO_SELF_DEVELOPMENT_ENABLED`，但它在 SKILL.md 的**步骤 3.5**
才生效，只跳过步骤 4–8、10、11。步骤 0–3.5（含最贵的 0.7）照跑不误。所以它
无法回答「我这台机器不需要自主循环」这个诉求。

## 目标

提供一个开关，关闭后循环彻底不被唤醒，零 token 消耗；同时不影响任务响应与
故障告警。

## 非目标

- 不做运行时切换。cron 注册只发生在 core 启动时，运行中改值也要等下次启动
  才生效，引入状态文件换不来实际收益。
- 不做中间档位（如 `minimal`）。多一档就多一份状态要维护。
- 不改动 `SUTANDO_SELF_DEVELOPMENT_ENABLED`。两个开关正交：新开关管「循环
  醒不醒」，旧开关管「醒了之后干不干活」。
- 不在 `proactive-loop/SKILL.md` 入口加第二道门。只拦 cron 注册这一层。

## 行为

| | 开启（默认） | 关闭 |
|---|---|---|
| 定时唤醒 core | 每 5 分钟 | 不发生 |
| 上下文重建（步骤 0.7） | 每次 | 不发生 |
| 自主挑活（步骤 4–8） | 按配额 | 不发生 |
| Discord 频道巡视（步骤 10） | 每次 | 不发生 |
| 语音 / Discord / Telegram 任务 | 正常处理 | **正常处理** |
| 健康检查 | 循环内跑 | **由独立 launchd job 跑** |
| 手动 `/proactive-loop` | 可用 | 仍可用 |

关闭态下仍能响应任务和收到告警，因为这两条路本就不经过 proactive-loop：

- 任务处理走 Stop hook（`workspace/scripts/check-pending-tasks-fixed.sh`），
  core 每轮结束时自查队列。
- 健康检查有独立的 `src/install-health-check-launchd.sh`。

## 设计

### 开关读取

新增 `skills/proactive-loop/scripts/proactive-loop-enabled.py`，与既有的
`self-development-enabled.py` 同形：

- 优先级 `env > manifest.json config`，遵循 CLAUDE.md 的 skill-config 约定
  （*"Skill config goes in the skill's `manifest.json` `config` block — not
  ad-hoc env vars"*）。
- 打印 `enabled` 或 `disabled` 到 stdout。
- 环境变量名 `SUTANDO_PROACTIVE_LOOP_ENABLED`。
- 真值集合 `{1, true, yes, on, enabled}`，假值集合
  `{0, false, no, off, disabled}`，两者之外一律 fail-closed。

`skills/proactive-loop/manifest.json` 的 `config` 块新增默认值：

```json
"config": {
  "SUTANDO_SELF_DEVELOPMENT_ENABLED": "1",
  "SUTANDO_PROACTIVE_LOOP_ENABLED": "1"
}
```

默认开启，与上游行为一致。使用者在 `.env` 里写
`SUTANDO_PROACTIVE_LOOP_ENABLED=0` 即可关闭。

### 拦截点

`skills/schedule-crons/SKILL.md` 加两道门，共用同一个脚本：

1. **步骤 3（注册 cron）之前** —— 关闭时跳过 `main-loop` 条目，`crons.json`
   里其他条目（morning-briefing 等）照常注册。
2. **步骤 4（兜底补建）之前** —— 关闭时不补 `*/10 * * * *` 那条，并 log 一行
   说明跳过原因。

第二道门是本设计成立的关键。步骤 4 的原文是：

> check whether any job in `crons.json` references `/proactive-loop` ...
> If none does, call `CronCreate` directly with `cron: "*/10 * * * *"`

它只看条目是否存在。若只做第一道门，`/startup` 会认为「用户忘了配」而自己补
一条回来 —— 2026-08-06 试跑时实测复现两次，每次都得手动删。

### 为何不用「保留条目但禁用」

`crons.json` 无禁用语义。schema（`skills/schedule-crons/SKILL.md`）只有
`name` / `cron` / `prompt` / `prompt_skill` / `loop` / `execution` /
`launchd`，权威读写实现 `src/dashboard_schedules.py` 也只处理增删，没有
`enabled` 字段。

技术上可以保留条目并把表达式设成永不触发的值（如 `0 0 31 2 *`）来骗过兜底
检查，但这是 hack：语义与实际行为矛盾、dashboard 显示会误导、上游若给兜底
检查加表达式合理性校验就会失效。故不采用。

### 数据流

```
core 启动
  → SessionStart hook 注入 /startup
      → /schedule-crons
          → 读 proactive-loop-enabled.py
              ├─ enabled  → 注册 main-loop（*/5）+ 其他 cron
              └─ disabled → 跳过 main-loop，只注册其他 cron
                            兜底检查也跳过，不补 */10
```

关闭态下 `crons.json` 里不存在 proactive-loop 条目，dashboard 上同样看不到。
状态与显示一致，没有「存在但不执行」的错位。

## 错误处理

| 情况 | 行为 | 理由 |
|---|---|---|
| 脚本文件不存在 | 视为 enabled | 不因一个可选脚本缺失就静默关掉使用者的循环 |
| 值无效（如 `maybe`） | fail-closed → disabled | 与 `self-development-enabled.py` 一致：宁可少跑，不可误跑 |
| manifest 读取失败 | 回退默认 `1` | 配置损坏不应改变默认行为 |

## 测试

新增 `tests/proactive-loop-toggle.test.py`，与既有的
`tests/proactive-loop-self-development.test.py` 同形（沿用该文件的命名前缀
与断言风格）：

| 场景 | 期望 |
|---|---|
| env 未设、manifest 为 `1` | enabled |
| env=`0` | disabled |
| env=`1`、manifest=`0` | enabled（env 优先） |
| env=`maybe` | disabled（fail-closed） |
| manifest 文件缺失 | enabled（默认） |

SKILL.md 那两道门是 markdown 指令，无法单测。验收靠实跑：设
`SUTANDO_PROACTIVE_LOOP_ENABLED=0` 后重启 core，确认 `crons.json` 中不含
proactive-loop 条目，且 `CronList` 为空。

## 影响面

| 文件 | 改动 |
|---|---|
| `skills/proactive-loop/scripts/proactive-loop-enabled.py` | 新增，约 40 行 |
| `skills/proactive-loop/manifest.json` | `config` 块加一个键 |
| `skills/schedule-crons/SKILL.md` | 步骤 3、4 各加一道门 |
| `tests/proactive-loop-toggle.test.py` | 新增 |

不触碰 `src/`，不改动任何运行时代码路径。
