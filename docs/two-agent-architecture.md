# Two-agent architecture

Sutando splits work across two agents: a realtime VLM voice front-end, and a Claude Code (or Codex) core that does durable work. `sutando-core` is the session wrapper around that core — not a third agent loop.

## 1. VLM agent (voice / realtime front-end)

**Role:** Talk to the user in real time (audio ± vision). Answer tiny things inline; delegate almost everything else to the core via `work`.

**Prompt framing:** “You are the voice interface. The Claude Code session is the brain.”

### Surfaces (same role, different implementations)

| | Voice agent | LiveKit agent | Omni-exp (experimental) |
|---|---|---|---|
| File | `src/voice-agent.ts` | `src/livekit-agent.py` | `src/omni-exp-agent.py` |
| Surface | Web UI / desktop mic (bodhi `VoiceSession`); omni/webcam support lives here when enabled | Phone app over LiveKit room | Phone browser HTML camera+mic (`/omni-exp`) |
| Agent name in code | `main` | `SutandoAgent` | omni-exp session |
| Transport | Browser ↔ voice server ↔ Gemini/Qwen | Phone ↔ LiveKit ↔ Gemini/Qwen | Phone ↔ omni-exp WSS ↔ Qwen Omni Realtime |
| Tool set | Full | Small subset | `work` (+ core start/stop HUD) |
| Bridge to core | `work` → `tasks/` → `results/` | Same idea | Same idea |

They are **not** one shared agent class. LiveKit is a thinner, phone-oriented reimplementation of the same pattern — not the TypeScript voice agent reused. **Omni-exp** is a separate experimental stack (`omni-exp-*` files); do not fold it into voice-agent.

### Tool sets

**Web voice (`main`) — fuller set**

| Group | Tools |
|---|---|
| Bridge to core | `work`, `get_task_status` |
| Session / mode | `switch_mode`, `save_meeting_note` |
| Zoom (skill) | `join_zoom`, `summon`, `dismiss` |
| Inline (instant) | macOS/browser actions (`press_key`, `type_text`, `scroll`, tabs/URLs, `switch_app`, screen capture/describe/click/point, notes/views, vision start/stop/frame, artifact cache, volume/brightness/clipboard, `get_current_time`, `get_core_status`, `cancel_task`, Meet/call helpers, video controls, optional presenter tools, `switch_voice_config`, plus skill-manifest tools) |
| Built-in (optional) | `googleSearch` when enabled |

**LiveKit phone (`SutandoAgent`) — subset**

- `work`
- `get_current_time`
- `press_key`, `type_text`
- `open_url`, `switch_app`
- `describe_screen`, `capture_screen`

Default pattern on both: greetings / tiny lookups inline; non-trivial work → `work` → task file → core → result spoken back.

## 2. CC (Claude Code) and `sutando-core`

**Claude Code (or Codex)** is the real agent with the tool-use / agent loop — the “brain.”

**`sutando-core`** is the **tmux session name** and launch wrapper (`src/agent/start-cli.sh` → `src/agent/claude/cli/start-cli.sh` or the Codex launcher). It runs something like:

```text
claude --name sutando-core ... -- "/startup"
```

So:

- **CC / Codex** = agent loop + tools + skills
- **`sutando-core`** = orchestration around that CLI (tmux, env, config dir, hooks, heartbeat) — **not** its own agent runtime

Voice and LiveKit never call the core over a socket for work. They write `tasks/task-*.txt`; the core (or its watcher) processes them and writes `results/task-*.txt`.

## Flow (both voice surfaces)

```text
User speaks
  → VLM realtime session (voice-agent.ts or livekit-agent.py)
  → answer directly OR call work()
  → tasks/task-*.txt
  → sutando-core (claude/codex agent loop)
  → results/task-*.txt
  → VLM speaks the result
```
