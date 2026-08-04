# Upstream Sync Analysis — sonichi/sutando

> Generated: 2026-08-04  
> Your repo: [rudyalways/PhonePcLiveAgents](https://github.com/rudyalways/PhonePcLiveAgents)  
> Upstream: [sonichi/sutando](https://github.com/sonichi/sutando)

## Fork Point (Your Last Common Commit)

This is the commit your project diverged from upstream. Everything below describes changes **after** this point in upstream.

| Field | Value |
|-------|-------|
| **Full hash** | `f8e58670cbbc61ce412adc5f9b86800d397acab5` |
| **Short hash** | `f8e5867` |
| **Date** | 2026-04-21 20:34:27 -0700 |
| **Subject** | fix(sync-memory): assert + self-heal to main branch before sync work (#504) |
| **GitHub link** | https://github.com/sonichi/sutando/commit/f8e58670cbbc61ce412adc5f9b86800d397acab5 |

## Current Upstream HEAD

| Field | Value |
|-------|-------|
| **Full hash** | `dab9c9985dd3b409805018410e603098192fea5c` |
| **Short hash** | `dab9c99` |
| **Date** | 2026-08-03 17:32:05 -0700 |
| **Subject** | fix(gateway): honor an explicitly-mapped tier for a named sender (#2584) |

## Summary Stats

| Metric | Count |
|--------|------:|
| Upstream commits since fork | **1,066** |
| Your unique commits since fork | **39** |
| Files changed upstream | **1,102** |
| Lines added upstream | **+206,315** |
| Lines removed upstream | **−5,950** |
| New skills added upstream | **35** |

### Commit Type Breakdown (upstream since fork)

| Type | Count |
|------|------:|
| fix | 604 |
| feat | 278 |
| docs | 68 |
| chore | 33 |
| refactor | 22 |
| test | 20 |
| ci | 11 |
| other | ~30 |

### Commits Per Month (upstream)

| Month | Commits |
|-------|--------:|
| 2026-04 (from Apr 23) | 44 |
| 2026-05 | 411 |
| 2026-06 | 133 |
| 2026-07 | 365 |
| 2026-08 (through Aug 3) | 113 |

---

## Top Feature Areas (by `feat()` scope)

Most active scopes in upstream feature commits since your fork:

| Scope | Feat commits |
|-------|-------------:|
| discord-bridge | 15 |
| health-check | 14 |
| skills | 10 |
| sutando-app | 7 |
| discord-voice | 7 |
| voice-agent | 6 |
| sync-memory | 6 |
| core | 6 |
| voice | 5 |
| telemetry | 5 |
| startup | 5 |
| gateway | 5 |
| agent-room-ops | 5 |
| workspace | 4 |
| task-bridge | 4 |
| screen-companion | 4 |
| runtime | 4 |
| hooks | 4 |
| context-drop | 4 |
| config | 4 |
| bridges | 4 |

---

## Major Upstream Themes (Apr 2026 → Aug 2026)

### 1. Multi-node / Gateway Architecture
- **Gateway bridge** with per-sender `access_tier`, status JSON, reconnect visibility
- **GATEWAY_INSTANCE** — one core against multiple gateways (prod + dev)
- **Agent room ops** — events client, SSE streams, `@`-mention peers, authz envelopes
- **Sparrow** — human-action bridge (AskUserQuestion upgrade, CardPoster, DecisionHandler)
- **Peer watch** — heartbeat monitoring across nodes

### 2. Runtime & Core Infrastructure
- **Node-bundle runtime engine** (G1.5 unbundle)
- **Runtime API** — local approval/elicitation/capability RPC (daemon + CLI)
- **Core supervisor** — monitor, outbound communicator, easy restart/stop
- **Singleton locks** — per-workspace role locks for gateway bridge
- **Codex CLI runtime** — selectable alongside Claude
- **Auth-preflight boot gate** — abort loud if CLI can't authenticate
- **Session handoff** — carry conversation across compaction
- **Cron runner** — OS-level launchd scheduler

### 3. Health Check & Observability (massive expansion)
- 14+ feature commits; 600+ fix commits overall
- Stale checkout warnings, credential proxy checks, comm-sweep staleness
- Auto-fix unlinked skills, gateway bridge status, memory/vault checks
- **Telemetry Phase 2** — `task_processed`, `feature_used` from all sources
- **Obs collector** enabled by default
- **Services status JSON** for desktop Settings→Services

### 4. Discord & Messaging Bridges
- Discord bridge hardening (15 feat commits)
- Discord voice integration (7 feat commits)
- Bridge auto-ack for non-allowlist senders
- Per-channel team-collaborator "engage" path
- Telegram TOFU + tri-state access tests

### 5. New Skills (35 added)

```
agent-registry          agent-room-ops          audio-transcribe
context-drop            context-reconstruct     db-browser
deal-finder             doc-ingest              electron-overlay-dimming
email-find              gemini-tts              make-viral-video
meeting-scheduler       observe                 obsidian-vault
open-sutando-ref        openai-tts              overlay-apps
relay                   release                 report-feedback
screen-companion        self-upgrade            session-recap
skill-usage-report      startup                 submit-use-case
subscription-scanner    sutando-migrate         task-orphan-check
task-progress           trusted-capabilities    voice-agent-test-harness
whatsapp                zoom
```

Notable capabilities:
- **doc-ingest** — PDF/XLSX/CSV/DOCX/PPTX extraction
- **meeting-scheduler** — calendar + invite creation
- **self-upgrade** — safe detached restart upgrade
- **session-recap** — deep recall from raw transcripts
- **screen-companion** — vision_query, take_note, look_up_reference
- **make-viral-video** + **gemini-tts** / **openai-tts**
- **whatsapp** bridge skill
- **zoom** skill (with injection tests)

### 6. Desktop App (Sutando.app)
- Dashboard with editable cron schedules
- Services status integration
- Core restart/stop from app menu
- Onboarding checklist surface
- Chrome launch for Claude-in-Chrome browser control

### 7. Security & Credentials
- **Secret vault** — programmatic write path
- Twilio webhook auth fail-closed
- Gmail write guard hook
- Credential proxy quota state checks
- Discord access.json 0600 enforcement

### 8. Workspace / Config Refactor
- Root status files → `state/`, logs → `logs/`
- `sutando.config` for progress-stream toggle
- Runtime descriptor v0.3.0 (voice_ws, vision_control, call tiers)
- SUTANDO_WORKSPACE adoption enforcement + CI lint gates

---

## Your Local Changes (39 commits since fork)

These are **not** in upstream — you'll need to preserve them during merge:

| Theme | Commits |
|-------|---------|
| **LiveKit + Qwen Realtime** | Mobile voice agent, room-state API, reconnect, token users |
| **Flutter mobile app** | Remote control, audio unlock, 7081 defaults |
| **Multi-user isolation** | Per-room agent, user auth, task isolation |
| **Pipeline trace** | UI stabilization, inactive timeout, extra watcher layer |
| **Deploy/ports** | sub-8000 ports, port verification, `--stop`, Claude login check |
| **PC screen share** | Display fix for shared screen |
| **Python venv migration** | Runtime environment |
| **README/docs** | PhonePcLiveAgents architecture rewrite |

Full list:
```
5c09b35 check claude login when deploying
5989538 add back result watcher monitor/wather, another layer for pipeline trace
1de0ac3 fix: 修复显示pc共享屏幕
ca77c91 feat: sub-8000 service ports, mobile audio unlock, Flutter 7081 defaults
9cd3041 feat(livekit): Qwen realtime compat, room-state API, mobile reconnect
93aa53b deploy --stop service and core, update pipeline trace
3dc949c udpate md to not use playwright/chromnium which requires additional login
345ce26 mark trace inactive after 15 mins
e4c8027 add cookies to page, auto connect when hitting enter
99b2a9e Fix LiveKit restart lifecycle and stabilize pipeline trace UI
6116dc8 Fix LiveKit dispatch and restore persistent task watcher
dcc2567 port update, trace fix
0f0bc1a Revert "fix: 多个claude task并行执行"
a38e9f8 fix: 多个claude task并行执行
120eaf7 fix: 忽略users.json
4b7518e Update LiveKit token users (src/users.json)
b2a58ba Revert "Update deploy scripts, mobile control, pipeline-trace, and app config"
fb620c5 Update deploy scripts, mobile control, pipeline-trace, and app config
d8b108a add port verification before start
e74c397 feat: 增加服务状态监控页
09f9183 fix: 重构agent
48ab285 update user to xyz sutando-xyz as password
344b26d fix： 增加实时日志脚本
1408b4d fix: 修改部署脚本
bd42429 chore(deps-dev): bump @types/node
1fbb5ee chore(deps): bump ai
87ff5cf chore(deps): bump bodhi-realtime-agent
d523ab6 fix(deps): upgrade zod for claude-agent-sdk
b7e710e fix: 重构启动脚本
b4da7be feat: 开发远程控制功能
6fe4633 fix: 修改bug
25ec02d fix: 修改readme
4e9b0a4 fix：迁移python运行环境到venv
0435cba fix: 迁移python运行环境到venv
3ba3d8f fix bugs
1008f18 feat: multi-user isolation
7fbf5af feat: add Flutter mobile app and one-command service launcher
c9c2a34 docs: rewrite README for PhonePcLiveAgents architecture
67d111d feat: add LiveKit-based phone-to-PC voice agent with Qwen Realtime
```

---

## Merge Risk Assessment

**High conflict risk** (both you and upstream likely touched):
- `src/voice-agent.ts` — upstream refactored voice/LiveAgentRuntime; you added LiveKit/Qwen
- `src/deploy.sh` / startup scripts — upstream auth-preflight + obs collector; you have port/sub-8000 logic
- `src/task-bridge.ts` — upstream telemetry + routing changes
- `skills/phone-conversation/` — upstream continued evolving call infrastructure
- `.env` / config — upstream added many new env vars and `sutando.config`

**Low overlap** (upstream-only, safe to take):
- Gateway / agent-room-ops / sparrow (new subsystems)
- 35 new skills
- Health-check expansion
- Discord bridge improvements
- Desktop app dashboard

**Your-only** (must keep):
- `app/` Flutter mobile client
- LiveKit room-state / Qwen compat layer
- Pipeline trace customizations
- Multi-user auth (`src/users.json`)

---

## How to Sync

```bash
# One-time setup (if not done)
git remote add upstream https://github.com/sonichi/sutando.git

# Sync workflow
git fetch upstream
git checkout -b sync-upstream-20260804    # branch first, recommended
git merge upstream/main
# resolve conflicts, test, then:
git checkout main
git merge sync-upstream-20260804
git push origin main
```

Compare upstream changes interactively:
```bash
git log f8e5867..upstream/main --oneline
git diff f8e5867..upstream/main --stat
git diff f8e5867..upstream/main -- src/voice-agent.ts   # inspect specific files
```
