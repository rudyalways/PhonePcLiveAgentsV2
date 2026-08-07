# Omni-exp agent design

Experimental phone HTML camera+mic agent (`omni-exp`). **Not** voice-agent’s omni/webcam path — keep them separate.

Decision log and architecture for a third realtime agent beside voice-agent and livekit-agent.

Related protocol notes: [qwen-omni-realtime-turns.md](./qwen-omni-realtime-turns.md)  
Vendor: [Qwen-Omni-Realtime](https://www.alibabacloud.com/help/en/model-studio/realtime)

## Research: vision-triggered / always-on models (2026)

Question: is there an API or strong open-source model that **natively** decides to speak from camera (silent mic), like Thinking Machines Lab interaction models or AURA-style streaming VideoLLMs — good enough to base a **live-omni agent** on? (AURA itself excluded as too small per product choice.)

### Ideal reference (not shippable as API today)

**[Thinking Machines Lab — Interaction Models](https://thinkingmachines.ai/blog/interaction-models/)** (`TML-Interaction-Small`): always-on audio+video+text in **200ms micro-turns**; explicitly aimed at reacting to **visual cues without verbal prompts**; dual interaction + background model. Coverage: [MarkTechPost](https://www.marktechpost.com/2026/05/13/mira-muratis-thinking-machines-lab-introduces-interaction-models-a-native-multimodal-architecture-for-real-time-human-ai-collaboration/), [SiliconANGLE](https://siliconangle.com/2026/05/11/thinking-machines-drops-new-highly-responsive-model-designed-humanlike-interactions-real-time/). **Access:** limited research preview only; **no public developer API** as of research date — contact / wait for wider 2026 release. Do not block product on this.

**[AURA](https://arxiv.org/abs/2604.04184):** always-on streaming VideoLLM with proactive responses; demo ~2 FPS on 2×80G GPUs. **Skip** for our stack (size / ops).

### Production APIs (evidence on vision trigger)

| System | Vision in stream? | Does **camera alone** start a turn? | Official stance |
| --- | --- | --- | --- |
| [Qwen-Omni-Realtime](https://www.alibabacloud.com/help/en/model-studio/realtime) | Yes (frames / optional) | **No** | Audio required; VAD or manual `commit`+`response.create` |
| [Gemini Live API](https://ai.google.dev/gemini-api/docs/live-api/capabilities) | Yes (~1 fps frames) | **No** (by itself) | [Robotics streaming](https://ai.google.dev/gemini-api/docs/robotics-streaming): “video frames alone do not trigger a new reasoning turn. Video frames must be accompanied by a text or audio prompt.” Recommended pattern: **heartbeat** = frame + short text every ~1s. `proactive_audio` = model may **stay silent** on irrelevant input — not “speak when camera moves.” |
| [OpenAI Realtime](https://developers.openai.com/api/docs/guides/realtime-conversations) | Images as message parts | **No** | App chooses when to send images and when to `response.create`; VAD is for audio. [Launch note](https://openai.com/index/introducing-gpt-realtime/): not a live video stream brain — snapshots when you decide. |

**Verdict on APIs:** none of the major realtime omni APIs document a native “scene changed → speak” trigger. Closest production pattern is **Gemini Live heartbeat** (or our Qwen manual force) with **client** scene/policy gate.

### Open-source / research (learned when-to-speak)

| Project | Idea | Size / access | Fit for museum live-omni |
| --- | --- | --- | --- |
| [Proact-VL](https://proact-vl.github.io/) ([paper](https://arxiv.org/html/2603.03447), [code](https://github.com/microsoft/AnthropomorphicIntelligence/tree/main/Proact-VL)) | Streaming VideoLLM + **autonomous speak decision** (`<\|FLAG\|>` + gated head); backbones Qwen2/2.5/3-VL | HF e.g. [proactvl_base_qwen3vl](https://huggingface.co/oaaoaa/proactvl_base_qwen3vl) (~8B class) | Closest OSS “always-on companion”; needs GPU self-host; not a managed phone API; commentary-oriented |
| [StreamBridge](https://arxiv.org/html/2505.05467) | Lightweight **external activation MLLM** + main Video-LLM | Research / DIY | Same architecture we sketched (diff/ACT → then big model) |
| LiveCC / StreamingVLM / Dispider / VideoLLM-online / QueryStream | Streaming + proactive variants | Research | Useful ideas; not drop-in product APIs |

### Recommendation for **live-omni agent**

1. **Ship now (managed API):** parallel agent on **Gemini Live** and/or **Qwen Omni**, with **client vision-proactive clock** (scene-change + optional heartbeat text). This matches Google’s documented robotics pattern and our Qwen manual path. Model quality is “good enough”; trigger is ours.
2. **Do not wait on TML** for v1 — right architecture, wrong availability.
3. **Optional OSS track:** evaluate **Proact-VL (Qwen3-VL-8B)** later if we want a **learned** when-to-speak head instead of thumb-diff — separate skill/runtime, GPU cost, weaker omni-audio than Gemini/Qwen realtime.
4. Keep name **live-omni agent** for the product; provider backends pluggable (`gemini` | `qwen` | future `tml`).

---

## Product shape

**Parallel agent** — not a mode inside voice or LiveKit. Product name: **live-omni agent**.

| Agent | Entry | Role |
| --- | --- | --- |
| Voice | `src/voice-agent.ts` | Browser WS + bodhi; Gemini-first |
| LiveKit | `src/livekit-agent.py` | Phone/PC WebRTC room; Qwen audio today |
| **Omni** | `src/omni-exp-agent.py` (planned) | Continuous A/V + **VoiceTrigger** + **PromptTrigger** |

Share dependency-light helpers only. Own process so vision/silence experiments do not regress the other agents.

**v1 transport:** provider WebSocket (Qwen and/or Gemini Live). LiveKit room binding later if needed.

---

## Trigger architecture (voice vs prompt)

Continuous media ingest is **not** a trigger. Triggers only decide **when to ask the model for a response**.

```text
                    ┌── VoiceSensor (VAD speech_stopped / drain) ──► VoiceTrigger
Mic ──append──┐     │
Cam ──append──┼──► Session buffers (audio + frames, dedup)              │
              │     │                                                    ▼
              │     │              ┌── TimerSensor ──────────┐
              │     └─────────────►│── SceneChangeSensor ────┼──► PromptTrigger
              │                    │── HeartbeatSensor ──────┤         │
              │                    │── Manual/UI/tool ───────┘         ▼
              │                                         TurnGate (mutex, cooldown,
              │                                         priority, drain_mode)
              │                                                      │
              └──────────────────────────────────────────► Provider turn
                    Voice: VAD auto or commit audio
                    Prompt: inject text (+ audio pad if Qwen) + response.create
```

### Two trigger kinds only

| Kind | Meaning | Sources | Provider action |
| --- | --- | --- | --- |
| **VoiceTrigger** | User finished (or drained) spoken input | VAD `speech_stopped`; post-response drain modes | Server VAD commit **or** `commit` + `response.create` |
| **PromptTrigger** | App wants a turn without user speech | Scene-change, timer, heartbeat, manual | Inject **text prompt** (+ Qwen audio pad if needed) → create response |

Time-based and image-change are **sensors**, not a third kind. They emit a **PromptTrigger** (`reason` + template).

### VoiceTrigger

```text
on speech_stopped (or drain flush):
  TurnRequest { kind: "voice", utterance_ids: [...] }
  → TurnGate → provider respond
```

- Default `in_flight=barge_in`: new speech cancels current response.
- Optional `buffer` + drain modes 1/2/3 (see later section).
- Voice priority beats PromptTrigger (defer/drop proactive while user is speaking / voice turn pending).

### PromptTrigger

```text
TurnRequest {
  kind: "prompt",
  reason: "scene_change" | "timer" | "heartbeat" | "manual" | ...,
  prompt_text: "<template>",
  attach: { latest_keyframe?: bool, salient_ring?: bool }
}
```

| Sensor | Fires when | Example prompt |
| --- | --- | --- |
| SceneChange | Thumb-diff + stabilize/flash + cooldown | `[Proactive: scene_change] Briefly introduce what is clearly visible. If nothing notable, reply exactly [[NO_SPEAK]].` |
| Timer | Every T ms (optional; prefer gated by scene) | `[Proactive: timer] Check the latest view…` |
| Heartbeat | Periodic nudge (Gemini robotics style) | `[HEARTBEAT] Observe. If nothing worth saying, [[NO_SPEAK]].` |
| Manual | Button / API / tool | Free-form or fixed |

| Provider | How PromptTrigger hits the wire |
| --- | --- |
| Gemini Live | Realtime text input (+ video already streaming) |
| Qwen Omni | Text inject and/or synthetic audio pad + `commit` + `response.create`; frames already buffered |
| OpenAI Realtime | `conversation.item.create` + `response.create` |

### TurnGate (shared)

1. Mutex — one in-flight response (unless barge-in cancels)  
2. Cooldown — per `reason`  
3. Priority — `voice` > `manual` > `scene_change` > `heartbeat`/`timer`  
4. Dedup — same reason + similar scene hash within cooldown → drop  
5. `[[NO_SPEAK]]` — suppress playback on prompt path  

### Media vs control

| Plane | Always on? | Triggers response? |
| --- | --- | --- |
| Audio / frame append | Yes when devices on | **No** by itself |
| VoiceTrigger | Utterance end / drain | Yes |
| PromptTrigger | Sensor / manual | Yes |

### Modules

1. `OmniMediaPump` — mic/cam append + dedup/salient latch  
2. `VoiceTrigger` — VAD + optional drain  
3. `PromptTrigger` + sensors  
4. `TurnGate`  
5. `ProviderAdapter` (gemini \| qwen)  
6. `omni-exp-agent.py` — session, tools/`work`, config  

### v1 defaults

```json
{
  "provider": "qwen",
  "voice_trigger": {
    "enabled": true,
    "in_flight": "barge_in",
    "drain": { "mode": "flush_ready", "ready_merge": "latest" }
  },
  "prompt_trigger": {
    "scene_change": { "enabled": true, "stable_ms": 700, "cooldown_ms": 10000 },
    "timer": { "enabled": false, "interval_ms": 5000 },
    "heartbeat": { "enabled": false, "interval_ms": 1000 },
    "manual": { "enabled": true }
  }
}
```

Museum silent walk: `scene_change` PromptTrigger on; timer/heartbeat off unless needed. Voice Q&A: VoiceTrigger as usual.

---

## Implementation plan (phone HTML → PC omni → core/CC)

### Goal flow

```text
Phone browser (omni-exp-client.html)
  camera (full-bleed, first) + mic
       │  WSS: PCM chunks + JPEG ~1fps (+ control JSON)
       ▼
PC / cloud: omni-exp-agent.py
  MediaPump → VoiceTrigger / PromptTrigger → TurnGate → Qwen|Gemini
       │  work tool (non-trivial)
       ▼
Sutando core / Claude Code
  tasks/task-*.txt  →  process  →  results/task-*.txt
       │
       ▼
omni-agent result watcher → speak / text back on same session → phone plays audio
```

### What to add vs change

| Piece | Action | Notes |
| --- | --- | --- |
| [`src/omni-exp-client.html`](../src/omni-exp-client.html) (new) | **Add** | Camera-first phone UI; do **not** overload [`mobile-client.html`](../src/mobile-client.html) (that one is LiveKit screen-view / mic for remote control) |
| [`src/omni-exp-agent.py`](../src/omni-exp-agent.py) (new) | **Add** | Parallel agent process: WSS server + provider session + triggers + `work` |
| `src/omni_*.py` helpers (new) | **Add** | `media_pump`, `voice_trigger`, `prompt_trigger`, `turn_gate`, `provider_adapter` — keep thin; Qwen bits reuse [`qwen_realtime_compat.py`](../src/qwen_realtime_compat.py) where possible |
| Static/HTTPS serve | **Extend** | Serve `/omni-exp` HTML + WSS upgrade from existing token/static host pattern (see mobile docs) or small `omni-gateway` alongside; **phone `getUserMedia` needs HTTPS** (or localhost) |
| [`src/livekit-agent.py`](../src/livekit-agent.py) | **No change** (v1) | Stay parallel; optional later: LiveKit transport adapter if NAT requires it |
| [`src/voice-agent.ts`](../src/voice-agent.ts) | **No change** (v1) | Pattern donor only |
| Task/result dirs | **Reuse** | Same bridge as LiveKit `work()`: write `tasks/{user}/task-*.txt`, poll/watch `results/` |
| `.env.example` / start script | **Extend** | `OMNI_*` knobs + `bash src/start-omni-exp.sh` |
| Design notes | **Done** | This file |

### UI: `omni-exp-client.html` (camera first)

Phone Safari/Chrome open `https://<pc-host>/omni-exp`.

**Viewport 1 (composition):**
- Full-bleed `<video>` rear camera (`facingMode: environment` for museum; toggle front)
- Small status chip (connected / listening / thinking)
- One primary control: Hold-to-talk **or** always-on mic toggle (config)
- Secondary: mute cam / disconnect

**Media capture:**
- `getUserMedia({ video: { facingMode: "environment" }, audio: true })`
- Video: draw to canvas @ ≤1 fps → JPEG → send
- Audio: AudioWorklet/ScriptProcessor → PCM 16 kHz mono → send
- Receive: play agent PCM/TTS audio (AudioContext; need user-gesture unlock on iOS)

**Wire protocol (v1 JSON+binary over one WSS):**

```text
Client → Server
  { "type": "session.start", "user": "...", "auth": "..." }
  { "type": "audio", "format": "pcm16le_16k", "data": "<base64>" }   // or binary frames
  { "type": "image", "mime": "image/jpeg", "data": "<base64>" }
  { "type": "control", "action": "mute_mic"|"facing"|"prompt_manual", ... }

Server → Client
  { "type": "session.ready" }
  { "type": "audio.out", "data": "<base64 pcm>" }
  { "type": "transcript", "role": "user"|"assistant", "text": "..." }
  { "type": "status", "state": "listening"|"responding"|"working" }
```

Auth: reuse `users.json` / same secret pattern as mobile token server if co-hosted.

### Omni-agent server responsibilities

1. Accept phone WSS; authenticate user → per-user `tasks/{user}/`  
2. `OmniMediaPump`: forward audio/images into provider session  
3. `VoiceTrigger` + `PromptTrigger` (scene-change on inbound JPEGs) + `TurnGate`  
4. ProviderAdapter (start with **Qwen Omni**; Gemini adapter second)  
5. **`work` tool** (copy LiveKit pattern): enqueue task for core/CC; wait short path; else async result watcher speaks later  
6. Stream model audio + transcripts back to phone  

### Core / CC integration (no new bridge)

Reuse LiveKit’s contract:

```text
omni-agent work(task)
  → workspace/tasks/<user>/task-<id>.txt
       source: omni-voice   (or omni)
       access_tier: owner
  → sutando-core / CC watcher picks up (existing watch-tasks)
  → workspace/results/<user>/task-<id>.txt  (or flat results/ — match LiveKit layout)
  → omni-agent watcher → inject/speak on live session
```

Do **not** call CC via a new RPC in v1. File bridge only. Ensure core watcher is running (`start-cli` / startup) same as LiveKit voice path.

**Monitor fallback:** Canonical pickup is Claude Code `Monitor` → `watch-tasks-stream.sh`. On OpenRouter / non-Anthropic cores (no Monitor), `SUTANDO_TMUX_TASK_FEEDER=auto|1` installs launchd job `com.sutando.omni-exp-tmux-task-feeder` which injects `TASK_FILE:` into the `sutando-core` tmux pane.

**Task while core still booting:** Keep the file in `tasks/`. `/startup` Step 1 processes it from disk (and `mark-ready`s first if owner tasks exist). The feeder **holds** (no abandon) while `state/core-booting.json` is fresh; omni HUD shows WAITING, not BLOCKED.

**Optional owner crons (default OFF):** not required for omni-exp. Gated via `skills/schedule-crons/scripts/cron-entry-enabled.py` / manifest defaults `0`:
`SUTANDO_MORNING_BRIEFING_ENABLED`, `SUTANDO_DAILY_INSIGHT_ENABLED`, `SUTANDO_PENDING_QUESTIONS_CRON_ENABLED`, `SUTANDO_SYNC_MEMORY_CRON_ENABLED`. `/schedule-crons` skips + CronDeletes when off. Opt in with `=1` in `.env`.

**Boot session-recap (fast, default ON):** `/schedule-crons` runs `boot-recap.py` — `mark-ready` first, capped dialog extract (~48k chars), **no LLM**. Deep `/session-recap` stays on-demand. Set `SUTANDO_SESSION_RECAP_ON_BOOT=0` to skip.

**`OMNI_EXP_MODE=demo` (default):** one scrollable HTML page with exactly **4 topic sections** under `workspace/data/omni-demo/` — no TTS, auto-play, or multi-slide deck. Set `OMNI_EXP_MODE=research` for the full auto-play deck + meeting capture.

**Omni process survival:** Do **not** start omni as a child of an agent/Cursor shell (`nohup` alone is not enough — shell process-group SIGTERM kills it with no traceback). Use `bash src/start-omni-exp.sh --daemon` / `install-omni-exp-launchd.sh` → launchd KeepAlive. Runners live under `~/Library/Application Support/Sutando/omni-exp/` because macOS TCC blocks LaunchAgents from executing files under `~/Documents` (symptoms: exit 78 `EX_CONFIG` / “Operation not permitted”). Phone URL is **HTTPS only** (`https://127.0.0.1:7090/omni-exp`).

### Fresh Mac bootstrap (clone → run)

`git clone` alone is not enough. Code is portable; secrets, venv, launchd runners, and workspace state are **per-machine**.

**In git:** `src/omni-exp-*.py`, `omni_exp_*`, `requirements-omni-exp.txt`, `.env.example`, docs.

**Local / macOS (not shared via git):**

| Artifact | Location | Role |
|---|---|---|
| `.env` | repo root (gitignored) | `DASHSCOPE_API_KEY`, `REALTIME_BASE_URL`, `OMNI_EXP_*` |
| venv | `.venv/` | `pip install -r requirements-omni-exp.txt` |
| TLS | `state/server.crt` + `.key` | phone HTTPS (often auto-created) |
| Workspace | `workspace/` | tasks, results, research notes, capture persist JSON |
| launchd + env copy | `~/Library/LaunchAgents/com.sutando.omni-exp-*` + `~/Library/Application Support/Sutando/omni-exp/` | KeepAlive; TCC-safe (avoid `~/Documents`) |
| Feeder inbox | `~/Library/Application Support/Sutando/omni-exp-feeder/` | OpenRouter / no-Monitor task pickup |
| Deck open | macOS `open` | research stamp opens HTML |
| OCR (optional) | Homebrew `tesseract` + `chi_sim` | only if `OMNI_EXP_RESEARCH_BOARD_OCR=1` |
| sutando-core | tmux + Claude login / Keychain | `work` capture-flush and deep decks |

```bash
git clone <repo> && cd PhonePcLiveAgentsV2
python3 -m venv .venv
.venv/bin/pip install -r requirements-omni-exp.txt
cp .env.example .env
# Edit .env: DASHSCOPE_API_KEY, REALTIME_BASE_URL (CN vs INTL), OMNI_EXP_MODE=demo (default), …
bash src/install-omni-exp-launchd.sh          # materialize runner + copy .env → Application Support
# After every .env edit: re-run install (or --restart) so launchd sees new vars.

# Core path (needed for work / meeting notes / decks):
bash src/agent/claude/cli/start-cli.sh
# If core has no Monitor (e.g. OpenRouter): ensure feeder
#   SUTANDO_TMUX_TASK_FEEDER=1
#   bash src/install-omni-exp-tmux-task-feeder-launchd.sh   # if not already installed

# Optional whiteboard OCR:
#   brew install tesseract tesseract-lang
#   OMNI_EXP_RESEARCH_BOARD_INK=1
#   OMNI_EXP_RESEARCH_BOARD_OCR=1
#   OMNI_EXP_RESEARCH_MEETING_SCAN_S=120   # optional heartbeat

# Phone (same LAN): https://<mac-ip>:7090/omni-exp  — trust self-signed cert; allow mic/camera
# Dev foreground (no launchd): bash src/start-omni-exp.sh
```

**Do not copy between machines:** `.env`, Keychain tokens, `Application Support/Sutando/omni-exp*`, or a filled `workspace/state/`. Each host bootstraps those.

**Non-Mac:** agent can run foreground with Python + keys; launchd installers, Application Support paths, and `open <deck.html>` are macOS-shaped — use equivalents or skip daemon/open.

Research / whiteboard flags: [`omni-exp-whiteboard-meeting-capture.md`](./omni-exp-whiteboard-meeting-capture.md).

### Phased delivery

| Phase | Deliverable | Done when |
| --- | --- | --- |
| **P0** | `omni-exp-client.html` + `omni-exp-agent.py` WSS echo (mic+cam preview, loopback status) | Phone on LAN HTTPS sees camera; PCM/JPEG reach PC — **implemented** (`bash src/start-omni-exp.sh`) |
| **P1** | Provider VoiceTrigger only (Qwen) | Speak to phone → hear answer — **implemented** (VAD + audio.out) |
| **P2** | Frame append + PromptTrigger `scene_change` | Silent pan → intro; `[[NO_SPEAK]]` suppress — **implemented** |
| **P3** | `work` + result watcher | “Research X” → core/CC → spoken result on phone — **implemented** (Core… button / `control.work`) |
| **P4** | Drain modes / Gemini adapter / LiveKit transport | Optional hardening |

### Ops / constraints

- Phone browser: HTTPS + mic/camera permissions; iOS audio unlock on tap  
- PC: API keys (`DASHSCOPE_API_KEY` / Gemini), core alive for `work`  
- NAT: LAN or tunnel (Cloudflare/ngrok) for P0–P3; LiveKit optional if WebRTC needed later  
- Keep LiveKit mobile + omni HTML as **separate URLs** (`/mobile` vs `/omni-exp`)

### Out of scope v1

- Changing Flutter app (HTML path first)  
- Folding into `livekit-agent.py`  
- Native TML / Proact-VL self-host  

---

## Decision log

### D1 — Continuous ~1fps into the 240s window (not “1–3 keyframes only”)

**Earlier mistake:** “proactive turns should send only 1–3 keyframes” sounded like *replacing* the rolling vision stream. That underuses Omni.

**Vendor model:**

- Frames append into a **session**, not one HTTP “call with N images.”
- Retained vision context ≈ last **240s** of frame-time for `qwen3.5-omni-plus-realtime` (also **50 video turns**). Oldest discarded.
- Recommended send rate: **1 fps**, JPEG, ≤256KB base64, audio before first image.
- Repo caps: `src/realtime-provider/capabilities.ts` (`maxVisionFps: 1`, `maxImageBytes: 256KiB`, `maxVisionContextSeconds: 240`).

| Path | What we send | Why |
| --- | --- | --- |
| Vision on, normal | Append ~**1 fps** for the session | Fills the rolling 240s temporal window (motion, before/after, scrolling) |
| Near-duplicate / idle | **Skip** append; optional keepalive every N s | Avoid filling the window with identical desktops |
| Voice VAD turn | No special image batch | Model already sees rolling audio+video + committed utterance |
| Silence proactive turn | Trigger generate only | Context already in-session; proactive ≠ “upload three JPEGs instead of history” |

**1–3 keyframes** only for cold start (vision was off, buffer empty), e.g. user taps “look now.” Not steady-state.

At 1 fps, 10s of accepted frames ⇒ ~10 appends. Those are not the only images in the next turn — older retained frames inside the ~240s window may still be present.

### D2 — Skipping static frames

**Usually not static** for real screen/camera (mouse, video, scroll, UI). Skip is an idle/cost optimization, not the common path.

**Detection (cheap, local — before encode/upload):**

1. Downscale to small gray thumb (e.g. 64×36).
2. Compare to last **accepted** frame (mean abs diff or aHash/dHash).
3. `diff < threshold` → skip.
4. `diff >= threshold` → accept, append, update reference.
5. **Keepalive:** if skipped for K seconds (e.g. 5–10s) while vision on, force-append one frame.
6. Optional: slightly higher threshold while user is speaking (ignore cursor blink); more sensitive during silence watching.

| Situation | Behavior |
| --- | --- |
| Active screen share | Most 1Hz ticks accept → rich temporal window |
| Locked idle / frozen camera | Most ticks skip → sparse window |
| Silent 10s scrolling | ~10 diverse frames → next turn sees motion |
| Silent 10s frozen | ~0–1 keepalive; no spam speak unless scene gate or user talks |

Scene-change for **proactive speak** reuses the same diff: fire when an accepted frame landed + cooldown + response mutex free.

### D3 — Voice memory: sliding multi-turn window, not last segment only

**Vendor:** history retained until **turn and duration** limits; then oldest discarded.

| Model | Audio max turns | Audio max duration | Video max turns | Video max duration |
| --- | --- | --- | --- | --- |
| qwen3.5-omni-plus-realtime | 100 | 600s | 50 | 240s |
| qwen3.5-omni-flash-realtime | 80 | 480s | 50 | 120s |

Example: **20 VAD segments over 240s** fits comfortably inside plus-realtime audio caps. Session context looks like:

```text
[turn1, asst1, turn2, asst2, … turn20, …] + recent audio/video media
— not just [turn20]
```

**Voice-agent extra layer:** bodhi `conversationContext.items` accumulates in-session (reconnect may summarize; `end_session` clears). Still not automatic multi-day chat replay.

**LiveKit today:** provider session history for the room job + system-prompt inject (`voice-context.txt`, `user_profile.md`). Disk `conversation-*.log` is mostly logging/greeting hints, not full model replay.

**Omni implications:**

- Design for multi-turn sliding context (voice + vision) within one session.
- Do not assume each VAD turn is memoryless.
- Do not assume infinite memory — oldest drops after caps; durable facts go via `work` / core memory / notes.
- Cross-session baseline: profile inject + `work` (same as LiveKit) unless we later add transcript reload.

### D4 — Trigger policy

| Clock | Role |
| --- | --- |
| `semantic_vad` | Primary turn clock when user speaks |
| **Vision proactive clock** | When user is silent but camera/scene is changing — client forces a turn (museum mode) |
| Response mutex | One in-flight response; barge-in → `response.cancel` + clear playback |

Vision append runs **independently** of turn clocks (subject to skip/keepalive). Turns decide **when to generate**, not whether history exists.

Naive “every X seconds generate” is wrong: Omni’s turn clock is audio/VAD (or manual commit). Blind timers cause overlapping responses and token waste. X is a **check** interval, not a generate interval.

**Vendor `silence_duration_ms` (e.g. 800):** time-based **end-of-utterance** after `speech_started`, not “poll every 800ms.” Always-silent audio never crosses that threshold into a response. Details: [qwen-omni-realtime-turns.md](./qwen-omni-realtime-turns.md).

#### Primary silent+vision scenario: museum / walk-and-look

**Problem:** You walk through a museum, mic quiet, camera moving across exhibits. You want the agent to introduce what is on camera. Pure VAD **never fires** (no speech) even though frames are changing — so stock Omni session.update alone is not enough.

**Solution (omni-agent owns this; vendor does not):**

```text
camera frames ──1fps append──► session vision window (rolling ~240s)
                     │
              thumb-diff: scene/camera changed?
                     │
         yes + silent + cooldown + mutex free
                     │
         client forces a turn:
           ensure audio pad (Omni requires audio-before/with vision)
           commit + response.create
           (or session.say / generate_reply if plugin cannot manual-create under VAD)
                     │
         model sees recent frames in window → speaks intro
```

| Condition | Speak? |
| --- | --- |
| Silent + camera static (staring at same painting) | No (or rare keepalive frame only) |
| Silent + camera/scene moved enough (new exhibit) | **Yes** — one proactive intro, then cooldown |
| User asks “what’s this?” | VAD / normal voice turn (primary clock) |
| Agent still talking + user speaks | Barge-in cancel |

**Tuning for museum mode** (knobs, not code yet):

- Lower scene-diff threshold than “idle desktop” (panning camera should count as change).
- Longer `proactive_cooldown_ms` after each intro (e.g. 8–15s) so continuous pan does not narrate every second.
- Optional: require change to **persist** for ~0.5–1s (not a single blurry frame) before firing.
- Instructions: “When a new scene appears and the user is silent, briefly introduce what you see; don’t repeat if it’s the same work.”

This is why omni-agent is a **parallel agent with its own turn policy**, not a thin wrapper around default VAD.

#### Overlapping voice segments while a response is still generating

Important: Omni Realtime is a **persistent session**, not “HTTP call 1 finishes → HTTP call 2.” Audio/images keep `append`ing; VAD opens/closes **utterances**; each completed utterance normally starts a **response** in that same session. Images are in the **rolling context**, not packaged into a discrete “bundle at response.done.”

**Default realtime behavior (VAD + barge-in)** — what vendor examples and Qwen docs imply (`speech_started` while responding → `response.cancel` + clear playback; “Semantic interruption” for backchannels):

```text
12:00  utterance1 ends → response1 starts generating/playing
12:0x  user starts utterance2 → speech_started → cancel response1 (do NOT wait for 12:10)
12:05  utterance2 ends → speech_stopped → response2 starts
        (images appended the whole time stay in session window)
```

So: **it does not wait** for processing to finish at 12:10 before handling the next voice segment. The next turn is driven by the **next end-of-utterance**, after interrupting the in-flight response.

**Case 1 — utterance2 ends 12:05; no more speech until response would have ended 12:10**

| Your “wait until 12:10 then bundle” idea | Actual default |
| --- | --- |
| Queue utterance2 + images 12:05–12:10, new call at 12:10 | **No** |
| What happens | At ~utterance2 **start**: cancel response1. At **12:05**: start response2 on utterance2. Images from before/during continue to sit in the rolling vision window for response2 — not a special “bundle at 12:10.” Nothing waits for the old 12:10 finish time. |

**Case 2 — utterance3 12:05–12:08, utterance4 12:09–12:15; response1 was going to last until 12:10**

Timeline under barge-in:

```text
12:00  u1 ends → response1 playing
12:05  u3 starts → cancel response1
12:08  u3 ends → response3 starts
12:09  u4 starts → cancel response3
12:15  u4 ends → response4 starts
```

- **New model response at 12:10?** No — 12:10 is irrelevant once interrupted.  
- **At 12:15?** Yes for the response to **utterance4** (last completed utterance after interrupts).  
- **Bundle all images?** Continuously appended frames remain in the **session sliding vision window** (e.g. ~240s for Qwen plus). There is no separate “zip all images since 12:05 into one call.” response4 simply conditions on whatever history+media still remain in context.

**If we instead chose a strict voice queue (not recommended for conversation):** mutex until `response.done` → then one response for the latest buffered utterance (or concatenated audio). That *would* look like “wait until 12:10,” but fights natural barge-in and is **not** what Omni VAD examples do. Our design default: **barge-in for voice**; mutex mainly for **proactive vision** fires so they don’t stack on an active reply.

#### Configurable post-response drain modes (your 3 modes — refined)

These modes answer: **while a response is generating/playing, we did *not* barge-in (or we only buffered); when processing ends, how do we consume voice that arrived?**  
They are **orthogonal** to the in-flight policy:

| Knob | Values |
| --- | --- |
| `in_flight` | `barge_in` (default realtime) \| `buffer` (no cancel; queue audio/VAD) |
| `drain_mode` | `1_flush_ready` \| `2_wait_utterance` \| `3_gap_merge` — only matters when there is backlog or mid-speech at `response.done` |

Images: always append continuously (with dedup/skip). At drain time the model sees the **rolling session window up to “now”**, not a separate zip file of frames.

```text
during response (in_flight=buffer):
  VAD still runs → completed utterances go to ready_queue[]
  speech_started without stop → voice_active=true
  frames keep appending (deduped)

on response.done → run drain_mode
```

**Mode 1 — `flush_ready` (when processing ends, take ready segments + images now)**

- At `response.done`: if `ready_queue` non-empty → one new response over those segments (prefer **latest only** or **concat all** — sub-knob `ready_merge: latest|concat`).
- If user is **mid-utterance** (`voice_active`): do **not** wait; either ignore until that utterance later completes into `ready_queue`, or optionally start tracking it for the *next* drain (sub-knob `mid_speech: defer|ignore`).
- Images: whatever is already in the session window at drain time (dedup already applied on append).
- **Makes sense for:** snappy catch-up after a long reply; museum+voice hybrid when you don’t want to delay.
- **Risk:** drops/ignores speech still in progress at the exact `response.done` instant unless `mid_speech=defer` and a later VAD end triggers another turn.

**Mode 2 — `wait_utterance` (if voice still on, wait for that segment to finish)**

- At `response.done`: if `voice_active`, **block drain** until `speech_stopped` (that utterance becomes ready), then respond.
- If not voice_active but `ready_queue` non-empty → same as mode 1 flush.
- **Must have** `max_wait_ms` (e.g. 10–30s) or a stuck talker stalls the agent forever.
- **Makes sense for:** user started talking over the end of the assistant; you want the full question, not a flush of older queued bits only.
- **Risk:** unbounded wait without cap; feels laggy if user pauses mid-thought longer than VAD silence.

**Mode 3 — `gap_merge` (wait across short gaps between segments, max gap e.g. 500ms)**

- After a ready segment (at drain or after mode-2 completion), keep the turn **open**:
  - if next `speech_started` within `max_gap_ms` (e.g. 500ms), append that segment into the same super-turn;
  - if gap > `max_gap_ms`, close and `response.create`.
- Also need `max_merge_ms` / `max_segments` hard caps (e.g. 15s or 5 segments) so “uh… uh… uh…” cannot chain forever.
- **Makes sense for:** broken phrases / quick follow-ups (“and also…”, “wait, the blue one”) that VAD split into multiple segments.
- **Risk:** feels slow; 500ms gap feels like extra latency after every utterance; long storytelling hits caps.

**Do they make sense together?** Yes — as a **ladder**, not three unrelated products:

| Priority | When to use |
| --- | --- |
| Conversational default | `in_flight=barge_in` — modes 1–3 mostly idle |
| Polite / no-interrupt assistant | `in_flight=buffer` + mode **1** |
| “Let me finish my question” | `in_flight=buffer` + mode **2** (+ `max_wait_ms`) |
| “Merge split VADs” | mode **3** on top of 1 or 2 after the first ready segment |

Suggested config shape:

```json
{
  "in_flight": "barge_in",
  "drain": {
    "mode": "flush_ready",
    "ready_merge": "latest",
    "mid_speech": "defer",
    "max_wait_ms": 15000,
    "max_gap_ms": 500,
    "max_merge_ms": 15000,
    "max_segments": 5
  }
}
```

**Against your case-2 timeline (buffer, no barge-in):**

- Mode 1 @ response.done 12:10: flush whatever is already `ready` by 12:10; if u4 still talking, don’t wait (unless mid_speech handling schedules later).
- Mode 2 @ 12:10 with voice on: wait until current utterance ends (e.g. 12:15), then one response (images until then in window).
- Mode 3 @ after that end: if another segment starts within 500ms, keep merging until gap > 500ms or caps; else respond.

**Recommendation for live-omni v1:** ship `barge_in` + mode `flush_ready` (latest). Add mode 2/3 as config once buffer path exists. Always keep absolute caps on 2/3.

### D5 — Parallel agent

Third process for isolation and easy extension. Do not fold into `livekit-agent.py`.

---

## Architecture (implementation target)

1. `src/omni-exp-agent.py` — session lifecycle, tools/`work`, result inject
2. `OmniVisionPump` — capture → thumb-diff → optional JPEG append @ ≤1fps
3. `OmniTurnPolicy` — VAD events + silence/scene proactive + mutex
4. Shared Qwen compat/factory — no forked protocol stack

### Borrow vs own

| Borrow (patterns) | Own |
| --- | --- |
| LiveKit: Qwen session wiring, tools/`work` | Process, vision pump, turn/silence policy |
| Voice: vision-adapter rules, interrupt/mutex ideas | Direct WS client path (v1) |

---

## Implementation sequence

1. ~~Durable design note~~ (this file)
2. Scaffold parallel agent + Qwen connect smoke (audio-only)
3. Vision pump with duplicate-skip + keepalive; prove frames affect next voice turn
4. Turn policy + proactive scene gate
5. Tests: skip static; accept motion; multi-turn session coherent; barge-in

---

## FAQ

**If context is ~240s of frames, why only feed 1–3 keyframes?**  
We should not in steady-state. Continuous ~1fps into the rolling window. 1–3 keyframes only for cold start / vision-was-off.

**How to skip static frames? Isn’t the feed usually non-static?**  
Thumb-diff vs last accepted frame; skip below threshold; keepalive every few seconds. Active use mostly accepts; skip helps idle/frozen feeds.

**240s talk, 20 VAD segments — last segment only or sliding window?**  
Sliding multi-turn session history (audio duration + turn caps). Fits plus-realtime. Oldest drops only after caps. Not last-segment-only; not infinite cross-session memory.

**Always silent with VAD?**  
No speech → vendor VAD never responds. Frames may still append. For museum walk-and-look, omni-agent must add a **vision proactive clock** (scene-change → client-forced turn). See D4.

**Silent but camera moving (museum) — will default Omni introduce exhibits?**  
**No, not by itself.** Need our proactive policy: append frames continuously, detect visual change, force `commit`/`response.create` (with audio pad). VAD `silence_duration_ms` does not help here.

**Can omni-agent finish museum intro with only camera change (no user audio, no text, no button)?**  
**Yes for the user; no for the raw vendor wire.**

| Layer | Camera-only OK? |
| --- | --- |
| User | **Yes** — no speak, type, or tap required. Walking + pointing camera is enough. |
| Omni-agent policy | **Yes** — visual diff is the sole *user* trigger for proactive turns. |
| Qwen Omni protocol | **Not designed for camera-change triggers** — see evidence below. Agent must force a documented trigger (manual commit/`response.create`) and satisfy “audio before image.” |

#### Official triggers only (no guessing) — [Model Studio realtime](https://www.alibabacloud.com/help/en/model-studio/realtime)

Quoted from that page:

1. **Audio required, images optional:** “Audio input is required; image input is optional.”
2. **Two turn modes only:**
   - **VAD:** “automatically detects the start and end of speech”; with VAD on, “the server automatically submits data and triggers a response at end-of-utterance.” `silence_duration_ms` = “The silence duration in milliseconds (ms) that signals the end of an utterance.”
   - **Manual:** “press to speak, release to send” / client sends `input_audio_buffer.commit` then `response.create` (“required only in Manual mode”).
3. **Images are append-only context, not a turn clock:** `input_image_buffer.append`; “We recommend sending images at one frame per second”; “You must send audio data at least once before you send image data.”
4. **No official “camera moved → respond” event** appears in the documented client/server event tables (triggers listed are speech_started/stopped/committed or commit+response.create).

**Conclusion from vendor docs alone:** stock Omni is designed for **voice conversation** (optional vision in context). Museum silent walk-and-look is **our** scenario; trigger we must implement = **manual-mode forced turn** when *our* code sees visual change — not a vendor vision VAD.

#### Can we use a time-based trigger for museum mode?

**Vendor API:** no built-in “every N ms generate.” Official clocks are only VAD end-of-speech or manual `commit` + `response.create`.

**Our agent:** **yes.** A client timer can call that manual path on an interval. That is app policy, not an Omni feature.

| Client policy | Works silent+camera? | Tradeoff |
| --- | --- | --- |
| Every T seconds → `commit` + `response.create` | Yes | Speaks even when staring at the same exhibit; easy to over-narrate / burn tokens |
| Every T seconds **check**, fire only if scene changed (+ cooldown) | Yes | Time is the poll; vision diff is the gate (recommended in this design) |
| Only scene-change edge (no timer) | Yes | Need a frame loop anyway (~1 fps); equivalent to checking on each accepted frame |

So: **time-based is allowed as our trigger**, then mapped to the official **manual** API. Prefer “timer checks, scene-change fires” over blind “speak every T seconds.”

#### How scene-change detection works (concrete)

Run on **every camera frame** (or every thumb at capture rate), not only on a slow wall timer. The 1 fps limit is for **upload to Omni**; local diff can be cheaper/faster.

```text
frame in
  → downscale gray thumb (e.g. 64×36)
  → score = mean abs diff (or dHash Hamming) vs last_stable_thumb
  → if score < enter_threshold: static (maybe skip upload)
  → if score >= enter_threshold: mark candidate; keep this JPEG as keyframe_candidate
  → debounce:
       hold until score stays high then settles (new stable view) for stable_ms
       OR salient flash path (below)
  → if fire: manual commit+response.create; set cooldown; last_stable_thumb = current
```

Suggested defaults (tunable): `enter_threshold` for “enough change”, `stable_ms` ≈ 500–1000ms (museum pause on a work), `cooldown_ms` ≈ 8–15s after a proactive speak, upload ≤1 fps.

#### Brief glimpse problem (Mona Lisa in view only briefly)

**Yes, a slow time-based-only checker can miss it.** Two miss modes:

| Miss | Cause | Mitigation |
| --- | --- | --- |
| Never captured | View &lt; 1 sample period (e.g. 200ms flash, sample at 1 Hz) | Local thumb loop faster than upload (e.g. 5–10 Hz diff); on big score spike, **force-encode+append that keyframe** even if under 1 fps budget |
| Captured but never spoken | Diff saw it, but debounce waited for “stable” and camera already moved on; or cooldown blocked | Keep a short **salient keyframe ring** (last K high-score frames). When a proactive turn fires, those keyframes are already in the Omni window (or re-append best salient). Optional **flash path**: if score ≫ threshold, schedule fire after `flash_delay_ms` without waiting for full stable settle, still subject to cooldown |

Recommended museum policy:

1. **Fast local diff** (don’t rely on “every 3s poll” alone).  
2. **Stable-scene fire** (default): speak when a new view holds ~0.5–1s — good for walking up to a painting.  
3. **Salient-flash latch**: if a huge change appears even briefly, latch that keyframe into the buffer; next proactive turn (after stabilize or short flash_delay) can still describe it because it’s in the rolling vision context — unless the flash was so short it never got a thumb sample at all.

You cannot guarantee a 50ms flash at 1 Hz capture; physics of sampling. You *can* avoid missing ~0.3–1s glances with faster local sampling + keyframe latch.

#### No useful information — can the agent return no reply?

**Official Omni:** every successful `response.create` / VAD turn normally produces a model response (text/audio). There is **no documented** “vision empty → skip generation” server flag.

**What we can do:**

| Approach | Effect |
| --- | --- |
| **Don’t fire** | Client gate: blurry / too dark / score noise-only / “same exhibit id” → skip `response.create` entirely (best “no reply”) |
| **Instruct brevity** | System instructions: if nothing notable or unclear, say nothing meaningful — e.g. output a fixed token `[[NO_SPEAK]]` or one short filler |
| **Client suppress** | If transcript is empty, `[[NO_SPEAK]]`, or boilerplate “I don’t see anything”, drop audio playback / don’t speak |

So “no reply” is an **agent policy** (skip fire or suppress playback), not a vendor guarantee. Prefer **skip fire** when the view is useless so you don’t spend a turn.

**Which stream owns the turn?**  
Audio only. Images ride along. Text is mostly output / ASR, not a live turn trigger.

---

## How voice-agent manages cross-turn context

Voice does **not** keep only “the last voice segment.” It stacks several layers. “Last segment” is just the newest item in a growing in-session list — until provider roll-off or an explicit clear.

### Layer A — Provider live session (primary for “what did we just say?”)

Gemini Live (or Qwen via OpenAI-compat transport) holds the **live bidirectional session**. Across VAD/model turns in the same connection, the model continues with that session’s history. This is why turn N can refer to turn N−1 without Sutando re-uploading the whole transcript every time.

Limits are provider-side (context window / compression / session length). Sutando comments note Gemini voice can “forget” specifics after ~10 minutes of turns — that is roll-off of **this** layer, not loss of disk logs.

### Layer B — Bodhi `conversationContext.items` (in-process transcript mirror)

`bodhi-realtime-agent` keeps an in-memory list of `{role, content}` items for the `VoiceSession`:

- User / assistant transcripts append as turns complete (plus tool calls/results, uploads, typed text).
- `getRecentTurns(n)` → `items.slice(-n)` — used for goodbye detection, narration “what did I just say,” gates.
- On each `turn.end`, voice-agent walks **new** items since `lastLoggedIndex` and writes them to conversation log / live transcript — it does **not** truncate the list to one segment.

So after 20 voice segments, `items` typically holds many user+assistant pairs (subject to native-audio quirks where user turns sometimes do not populate).

**Reconnect / idle recovery (text summary, not raw audio):**

- Client reconnect while Gemini still active: inject last **10** user/assistant lines (each content truncated to 150 chars) as a system “continue naturally” message.
- Gemini was CLOSED (idle): reconnect transport, then inject “I’m back” + same last-**10** summary into a **new** provider session.
- Transport `reconnect({ conversationHistory: toReplayContent() })` can replay structured history into the provider when bodhi reconnects the model link.

**Explicit clears:** `end_session` / goodbye paths zero `conversationContext.items` so the next session does not replay “goodbye” and re-trigger end_session.

### Layer C — Durable “sticky” facts for when Layer A forgets

`state/voice-session-context.json` + tool `recent_context` ([`src/inline-tools.ts`](../src/inline-tools.ts)):

- Core writes small structured state: `active_drafts`, `pending_action`, `last_results` (~3 each).
- Voice calls `recent_context` when the user says “the draft / the post” and Gemini’s window lost it.
- Freshness annotation: stale if older than 6h (previous session leakage).

This is **not** a full transcript; it is a deliberate pocket for durable decisions.

### Layer D — Side-channel injects (same live session)

`injectText` / `sendContent` push framed text into the **current** provider session (task results, context-drop, note view, phone-call transcript). Those become more user-role context in the live turn stream — again cross-turn only while the session lives.

### Layer E — Disk logs (weak for model memory)

`conversation.log` / session DB rows are the durable record for humans and later tools. They are **not** automatically replayed into Gemini every turn. Greeting / product logic may notice history exists; full recall goes through `work` → core.

### Picture

```text
User utterance N (VAD end)
    → provider session already contains turns 1..N-1   ← Layer A (sliding)
    → bodhi items append user+assistant text            ← Layer B (grows in RAM)
    → optional recent_context / injectText              ← Layer C/D
    → log to disk                                        ← Layer E (not auto-fed back)
```

**Answer in one line:** cross-turn context for “last voice segment” is mainly the **provider session history** (grows, then drops oldest when over limit) plus bodhi’s **full in-session item list** (with last-10 text summary on reconnect). Not last-segment-only; not infinite; sticky nouns use `voice-session-context.json`.

### Is it a sliding window? What do turn N+1 / N+2 see?

Two different stores — do not conflate them.

| Store | Window shape | What turn **N+1** sees | What turn **N+2** sees |
| --- | --- | --- | --- |
| **Provider session** (Gemini Live / Qwen) — what the model actually conditions on | **Growing, then sliding:** keeps prior turns in the live session until provider limits / compression; then **oldest drop off** | Roughly turns **1…N** (user+assistant) that still fit, plus the new user utterance for N+1 — **not** only turn N | Roughly turns **1…N+1** still in window, plus new utterance — again not only the latest segment. If the session is long, early turns (1, 2, …) may already have fallen off |
| **Bodhi `conversationContext.items`** — local mirror | **Append-only growth** until `end_session` clears it — **not** a sliding trim in steady state | Full list accumulated so far (for logging / `getRecentTurns` / gates) | Same list plus the new turn’s items |
| **Reconnect inject** | Hard **last-10** text summary (150 chars/line) | Only if client/model reconnect path runs — not every normal turn | Same |
| **`recent_context` JSON** | Tiny sticky pocket, not a transcript window | Only if the model calls the tool | Same |

So for a normal uninterrupted call:

```text
After turn N completes:
  provider ≈ [T1, A1, T2, A2, … TN, AN]   (minus whatever already rolled off)

Turn N+1 user speaks:
  model sees ≈ [T1..AN that remain] + TN+1
  — includes last segment TN/AN AND earlier turns still in window

Turn N+2:
  model sees ≈ [… prior remainder…] + TN+1/AN+1 + TN+2
```

**Naming:**

- **Not** “only last VAD segment.”
- **Yes, eventually a sliding window** at the **provider** (drop oldest when over context / Omni turn-duration caps).
- **Before** hit limits, it behaves more like a **session-growing window** (almost full history of this call).
- Bodhi’s RAM list is **not** sliding unless you clear it; the **last-10** trim is only for reconnect summary text.

Qwen Omni makes the slide explicit (audio/video turn + duration caps). Gemini Live is the same idea with less documented turn counts — Sutando empirically treats ~10 minutes as when specifics get fuzzy, which is why `voice-session-context.json` exists.

### Omni takeaway

Match this layering: rely on Qwen session history for adjacent turns; keep a local transcript mirror if you need gates/logging; use a small sticky JSON (or `work`) for facts that must survive roll-off; do not expect disk conversation logs to be model memory unless you inject them.
