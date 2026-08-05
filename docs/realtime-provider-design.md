# Realtime provider abstraction — design

**Status:** Proposed  
**Target provider (v1):** [Alibaba Qwen-Omni-Realtime](https://www.alibabacloud.com/help/en/model-studio/realtime) (`qwen3.5-omni-plus-realtime`)  
**Related:** [`architecture-boundaries.md`](architecture-boundaries.md) · [`pointer-teacher-design.md`](pointer-teacher-design.md) · bodhi `LLMTransport` · `src/vision-tools.ts` · `src/observability/realtime-map.ts` · §4.5–4.6 (compatibility + tool inventory)

## 1. Summary

Upgrade Sutando's voice stack to support **swappable realtime LLM providers** with
[Qwen-Omni-Realtime](https://www.alibabacloud.com/help/en/model-studio/realtime) as the
primary new target, while keeping Gemini Live as the default fallback.

The design introduces a **shared provider factory** (`src/realtime-provider/`) that
sits at the existing bodhi transport seam. Orchestration (task bridge, tools,
session lifecycle) stays provider-neutral. Provider-specific wire I/O, vision frame
encoding, error taxonomy, and credential resolution stay at the **adapter edge**.

The web client already captures screen frames via `getDisplayMedia` and POSTs JPEGs
to `/vision/frame`. This doc specifies how that path generalizes across providers
(Gemini `sendFile` vs Qwen `input_image_buffer.append`) without coupling the
browser or vision pipeline to a single vendor.

---

## 2. Goals

| Goal | Detail |
|------|--------|
| **Env-level switching** | `REALTIME_PROVIDER=gemini\|qwen` (+ model/voice/base URL overrides) and restart — no code change to flip providers |
| **Cross-surface parity** | Web voice, phone, and LiveKit/mobile share one config schema and factory where possible |
| **Realtime vision** | Browser → `/vision/frame` → model image buffer works on Omni and Gemini with provider-specific adapters |
| **Observability upgrade** | Usage ticks, trace IDs, pipeline events, and activity-log snapshot all carry `provider`, `model`, and vision/frame metrics |
| **Incremental migration** | Ship factory + Qwen on web voice first; phone and Gemini-only features follow without blocking |

## 3. Non-goals (v1)

- Hot-swapping provider mid-session without restart (future; `switch_voice_config` stays Gemini-only initially)
- Abstracting **non-realtime** text models (`VOICE_MODEL` / subagent path) — separate concern
- Replacing bodhi's internal `LLMTransport` interface — we **configure and wrap** it, not duplicate it
- Full feature parity across providers (e.g. Gemini `googleSearch` grounding has no Qwen equivalent — guard, don't fake)
- WebRTC browser-direct-to-DashScope path (Alibaba supports WebRTC for low-latency browser voice; v1 keeps server-relay via bodhi)

---

## 4. Current state

### 4.1 Layer diagram (as-is)

```text
┌─────────────────────────────────────────────────────────────────┐
│ Surfaces (apps)                                                 │
│  web-client.ts ──► web-voice-transport.ts (PCM/JSON, neutral)   │
│  phone-conversation ──► VoiceSession (Gemini only)              │
│  livekit-agent.py ──► create_realtime_model() (4 providers)     │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ Orchestration (provider-neutral today)                            │
│  VoiceSession · task-bridge · live-agent-runtime · inline-tools │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ LLM transport (provider-specific, fragmented)                   │
│  GeminiLiveTransport (bodhi default)                            │
│  OpenAIRealtimeTransport (Qwen via voice-agent.ts inline fn)    │
│  LiveKit RealtimeModel plugins (Python, separate factory)       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                    Gemini / DashScope / OpenAI / MiniMax APIs
```

### 4.2 What already works

| Piece | Location | Notes |
|-------|----------|-------|
| bodhi `LLMTransport` | external dep | Correct abstraction seam |
| Qwen on web voice | `voice-agent.ts:buildQwenVoiceTransport()` | OpenAI-wire-compatible DashScope endpoint |
| Qwen on LiveKit | `livekit-agent.py` + `qwen_realtime_compat.py` | Protocol patches for VAD/transcription |
| Browser vision push | `web-client.ts` → `/vision/frame` → `vision-tools.ts:submitFrame()` | ~1 fps JPEG from `getDisplayMedia` |
| Gemini vision wire | `transport.sendFile(b64, 'image/jpeg')` | Gemini Live `realtime_input.video` slot |
| Usage client | `observability/realtime.ts` | Fire-and-forget POST to collector |
| Usage map | `observability/realtime-map.ts` | `provider` field exists; defaults to `gemini-live` |
| Activity log | `web-client.ts:/activity-log` | Exposes `realtimeProvider`; Gemini-specific geo-block heuristic |
| Pipeline trace | `pipeline_emit.py` | Phase events from watcher, LiveKit, bridges |

### 4.3 Gaps blocking a clean upgrade

| Gap | Impact |
|-----|--------|
| No shared `src/realtime-provider/` factory | Qwen wiring duplicated inline in `voice-agent.ts`; LiveKit has its own Python factory |
| Phone hardcoded to Gemini | Cannot switch phone to Omni without transport injection |
| Vision tied to `sendFile` | Qwen Omni uses `input_image_buffer.append` (OpenAI-compat event name) — may not route through bodhi's `sendFile` today |
| Credentials Gemini-only | `resolveCredential('gemini-voice')` runs even when `REALTIME_PROVIDER=qwen` |
| Error classifier Gemini-only | `voice-error-classifier.ts` pattern-matches Gemini close reasons |
| Observability defaults | Tickers often omit `provider`; trace attrs don't record vision frame counts |
| Activity log | Provider-aware chips partial; no vision-stream or frame-rate telemetry |

### 4.4 Vendor compatibility audit — [Qwen-Omni-Realtime](https://www.alibabacloud.com/help/en/model-studio/realtime)

Audit date: 2026-08-05. Source: Alibaba Model Studio docs + this repo at HEAD.

#### Summary verdict

| Area | Compatible? | Notes |
|------|-------------|-------|
| Audio I/O (16 kHz in / 24 kHz out PCM) | ✅ Yes | Matches `web-voice-transport.ts` and Qwen session config |
| VAD (`semantic_vad`) | ✅ Yes | Already in `buildQwenVoiceTransport()` / `qwen_realtime_compat.py` |
| Basic voice conversation | ⚠️ Partial | LiveKit path more battle-tested; web `voice-agent.ts` lacks Qwen protocol patches |
| Tool calling (`work` + inline tools) | ⚠️ Partial | Omni supports tools; **mutually exclusive with built-in web search**; web voice missing LiveKit compat layer; see §4.6 |
| Realtime screen vision (Watch → `/vision/frame`) | ❌ Not yet | Gemini `sendFile` today; Omni needs `input_image_buffer.append` + audio-first gate |
| Gemini `googleSearch` grounding | ❌ No | Gemini-only; Omni has separate `enable_search` (conflicts with tools) |
| Phone calls | ❌ No | Hardcoded Gemini; 8 kHz mu-law vs 16 kHz PCM |
| Long sessions (>120 min) | ⚠️ Reconnect | Omni hard cap: 120 min per WebSocket |

**Bottom line:** Omni is a **natural audio upgrade** on the bodhi transport seam, not a
drop-in replacement for full Sutando on Gemini. Choose **tools over Omni web search**
(`enable_search` must stay off).

#### Alibaba-documented limits (`qwen3.5-omni-plus-realtime`)

| Limit | Vendor value | Sutando impact |
|-------|--------------|----------------|
| WebSocket lifetime | **120 minutes** | Long voice sessions need reconnect |
| Audio turns | **100 turns** | OK for typical sessions |
| Video/image turns | **50 turns** | Continuous Watch (~0.7 fps) may hit cap in ~70 min |
| Audio context (cumulative) | **600 s** | OK for typical use |
| Video context (cumulative frames) | **240 s** | ~4 min of Watch history retained in model context |
| Image format | JPEG, 480–720p, ≤256 KB base64 | Web client 1280×720 q=0.6 (~80–150 KB) fits |
| Vision rate | ~1 fps recommended | Web client ~0.67 fps (`VISION_FRAME_INTERVAL_MS=1500`) fits |
| Audio before vision | **Required** | Watch-before-speak fails until vision adapter gates on `audioSent` |
| Regions | Singapore + Beijing (separate API keys) | May need workspace-scoped `REALTIME_BASE_URL` |
| Endpoint | `wss://{WorkspaceId}.{region}.maas.aliyuncs.com/api-ws/v1/realtime?model=…` | Defaults today: legacy `dashscope.aliyuncs.com/api-ws/v1[/realtime]` |

#### Tool calling vs web search (critical product constraint)

Alibaba docs:

> Web search and tool calling are **mutually exclusive**.

| Capability | Gemini (current default) | Qwen Omni |
|------------|--------------------------|-----------|
| Inline tools + `work` delegation | ✅ ~40+ tools via `mainAgentTools` (§4.6) | ✅ supported in principle |
| `googleSearch` grounding | ✅ optional (`voice-agent.json`) | ❌ N/A |
| Built-in web search | ❌ | ✅ `enable_search: true` — **only without tools** |

**Sutando policy on Omni:** keep **tool calling enabled**, leave **`enable_search` off**.
Route “current info” through `work` or non-realtime paths. Do not enable Omni web search
while the full inline tool surface is registered.

#### Protocol compat by surface

| Surface | Transport | Qwen compat patches | Tool-call follow-up | Vision |
|---------|-----------|---------------------|---------------------|--------|
| **LiveKit / mobile** | LiveKit OpenAI plugin | ✅ `qwen_realtime_compat.py` | ✅ `function_call_output` nudge + `auto_tool_reply_generation` | RTP (WebRTC); not web JPEG push |
| **Web voice** | bodhi `OpenAIRealtimeTransport` | ❌ none | ⚠️ unverified — **smoke test:** `scripts/test-qwen-realtime-tools.py` | ❌ `sendFile` (Gemini wire) |
| **Web client** | bodhi ClientTransport (neutral) | n/a | n/a | ✅ capture path OK; server adapter missing |
| **Phone** | bodhi Gemini default | n/a | Gemini only | Gemini only |
| **Task bridge / `work`** | provider-neutral | n/a | ✅ if realtime tool loop works | n/a |

LiveKit patches that web voice still needs (port to bodhi or shared TS layer):

- Event rename (`response.audio.delta` → `response.output_audio.delta`, etc.)
- Out-of-order `response.*` before `response.created`
- **`function_call_output` → nudge `response.create`** after tool results
- Session.update schema flattening (nested OpenAI GA `audio` → DashScope top-level)

#### Vision path compatibility

```text
Today (Gemini):
  web-client JPEG → /vision/frame → vision-tools.submitFrame() → transport.sendFile()

Omni requires (WebSocket):
  … → input_image_buffer.append (after ≥1 audio append)
```

WebRTC browser-direct mode sends images on a **video RTP track** — different architecture;
out of v1 scope (server-relay JPEG push remains the target).

#### Recommended switch strategy (phased)

```bash
# Phase A — voice + tools, no vision, no enable_search
REALTIME_PROVIDER=qwen
DASHSCOPE_API_KEY=sk-...
REALTIME_MODEL=qwen3.5-omni-plus-realtime
# googleSearch in voice-agent.json is ignored on Qwen transport
```

Validate **LiveKit first** (best compat), then web voice after bodhi/compat port.
Vision (Watch) is **Phase B** after `vision-adapter.ts`.

#### Spike scripts (Phase 0)

| Script | Proves |
|--------|--------|
| `scripts/test-qwen-realtime-audio.py` | PCM → VAD → transcription → auto response |
| `scripts/test-qwen-realtime-tools.py` | Tool register → function call → output → follow-up speech |

**Spike result (2026-08-05):** `test-qwen-realtime-tools.py` passes against
`dashscope.aliyuncs.com` with `qwen3.5-omni-plus-realtime` (~3s, exit 0) — vendor-layer
tool calling works (register → `echo` call → `function_call_output` → follow-up
`response.done`). Web voice (bodhi without `qwen_realtime_compat.py`) still unverified.

Earlier automated runs were **aborted** before completion: one hung ~2.7 min with no
output; another hit a WebSocket-close spin loop when the connection dropped immediately
(fixed in-script: set `ws_closed` when `ws.closed`, break reader on close).

| `scripts/test-qwen-realtime-vision.py` | (planned) audio append → `input_image_buffer.append` |

### 4.5 Multi-level compatibility — tools, agent, timeline, workflow

Sutando runs **two clocks** that only meet at injection points:

```text
┌──────────────── REALTIME CLOCK ─────────────────┐
│  speech → VAD turn → tool call OR speak         │
│  (sub-second to ~10s per turn)                  │
└────────────────────┬────────────────────────────┘
                     │ work() / result file
┌────────────────────▼────────────────────────────┐
│  DURABLE CLOCK (provider-neutral)               │
│  task file → core agent → result → inject/speak │
│  (seconds to minutes)                           │
└─────────────────────────────────────────────────┘
```

Omni mainly affects the **realtime clock**. The **durable clock** (tasks/results/core)
is already vendor-neutral; **handoffs** between clocks are provider-sensitive.

#### Summary by level

| Level | Tools | Agent | Timeline | Workflow |
|-------|-------|-------|----------|----------|
| **Concept** | ✅ Tool-first agent aligns with Omni | ✅ Split realtime/brain preserved | ⚠️ Dual-clock model still valid | ✅ Most workflows map; vision + search differ |
| **Design** | ✅ Execution decoupled; protocol not | ✅ Orchestration provider-neutral | ✅ Durable timeline independent | ✅ File bridge unchanged |
| **Execution** | ⚠️ Web lacks Qwen tool patches | ⚠️ Prompts Gemini-centric | ⚠️ Omni caps stricter than Gemini | ⚠️ Watch + long session gaps |
| **Runtime** | LiveKit ✅ vendor ✅ web ❓ | Env switch + restart | 120 min / 240s vision | LiveKit first; phone last |

#### Tools — by level

| Level | Assessment |
|-------|------------|
| **Concept** | Sutando is a **tool router** (~40+ inline + one async brain). Omni supports function calling but **`enable_search` ⊥ tools** — keep tools, disable Omni search. |
| **Design** | Tool **definitions** and **execute()** live in Sutando; bodhi only registers schemas and dispatches calls. Tool **reply protocol** is provider-specific (Qwen needs `function_call_output` + `response.create` nudge). |
| **Execution** | Three classes: **(A)** inline instant (scroll, describe_screen), **(B)** inline + external API (`point_at` uses Gemini REST — side channel), **(C)** async `work` (file bridge → core). Classes A/C execution is provider-neutral once the tool loop works. |
| **Runtime** | Vendor spike ✅; LiveKit has compat patches; web voice bodhi path unverified E2E. Large tool schema (~50 names) may add latency — stress test recommended, not a known hard cap. |

#### Agent — by level

| Level | Assessment |
|-------|------------|
| **Concept** | **Realtime persona** (Omni when `REALTIME_PROVIDER=qwen`) vs **execution brain** (core via `work`) — clean split; Omni replaces ears/mouth only. |
| **Design** | `VoiceSession` + `MainAgent` above transport; `live-agent-runtime` + `task-bridge` below. Everything below `LLMTransport` is provider-neutral. |
| **Execution** | Web: `voice-agent.ts`; LiveKit: `livekit-agent.py`; Phone: Gemini-only today. `googleSearch` is a session flag, not a tool. |
| **Runtime** | Switch provider via env + restart of realtime leaf only; core agent + bridges unchanged. |

#### Timeline — by level

| Timeline | User model | Omni constraint |
|----------|------------|-----------------|
| **Turn** | "I spoke, it answered" | `semantic_vad`; semantic interruption |
| **Task arc** | "Fix the PR" | Bounded by `work` `timeout_minutes`, not Omni |
| **Session arc** | Long conversation | **120 min** WebSocket max |
| **Vision arc** | Continuous Watch | **240 s** cumulative frame context; **50** video turns |

Injection points (`injectText` for task results) are provider-neutral in logic; transport method may differ (`sendRealtimeInput` vs `sendContent`).

#### Workflow — by level

| Workflow | Gemini | Omni |
|----------|--------|------|
| Quick Q&A (no tool) | ✅ | ✅ |
| Inline action (scroll, volume, …) | ✅ | ⚠️ tool loop on transport |
| Deep work (`work` → inject result) | ✅ | ✅ durable path; ⚠️ tool + inject |
| Watch + "what do you see" | ✅ | ❌ vision adapter + audio-first |
| Proactive / Discord / cron tasks | ✅ | ✅ (no realtime model) |
| Multi-channel task bridge | ✅ | ✅ |

**Rollout order (workflow-first):** LiveKit + Omni (tools + `work`) → web voice + tools → vision adapter → phone → session renewal policy for long Watch.

#### Capability descriptor (factory output)

Downstream layers should consume **capability flags**, not only transport:

```typescript
interface RealtimeSessionDescriptor {
  provider: 'gemini-live' | 'dashscope-omni';
  capabilities: {
    toolCalling: boolean;
    builtinWebSearch: boolean;      // enable_search — mutually exclusive with tools
    googleSearch: boolean;          // Gemini-only session flag
    visionInject: 'sendFile' | 'input_image_buffer' | 'none';
    requiresAudioBeforeVision: boolean;
    maxSessionMinutes: number;
    maxVisionContextSeconds: number;
  };
}
```

### 4.6 Tool surface inventory (not just `work`)

The voice agent is **not** a single delegate tool. Registration in `voice-agent.ts`:

```typescript
mainAgentTools = [workTool, getTaskStatus, switchModeTool, saveMeetingNoteTool, ...inlineTools]
```

#### Orchestration tools (4)

| Tool | Role |
|------|------|
| `work` | Delegate non-trivial tasks to core agent via `tasks/` → `results/` |
| `get_task_status` | Check pending/in-flight `work` tasks |
| `switch_mode` | active / meeting / presenter mode |
| `save_meeting_note` | Meeting-mode note capture |

#### Inline tools (~39 base, `execution: 'inline'`)

| Category | Tools |
|----------|-------|
| Browser / screen | `scroll`, `switch_tab`, `close_tab`, `open_url`, `describe_screen`, `click`, `point_at`, `scroll_and_describe`, `capture_screen`, `screen_record` |
| macOS control | `press_key`, `switch_app`, `type_text`, `volume`, `brightness`, `clipboard`, `open_file` |
| Tasks / state | `cancel_task`, `toggle_tasks`, `get_current_time`, `get_core_status`, `recent_context` |
| Meetings / phone | `join_gmeet`, `lookup_meeting_id`, `call_contact` |
| Notes / UI | `show_view`, `read_note`, `save_note`, `delete_note` |
| Vision (Watch) | `send_vision_frame`, `start_vision`, `stop_vision` |
| Artifacts | `set_active_artifact`, `query_active_artifact`, `clear_active_artifact` |
| Video playback | `play_video`, `pause_video`, `resume_video`, `replay_video`, `close_video` |
| Presenter (conditional) | `slide_control`, `fullscreen` |
| Config | `switch_voice_config` — **Gemini-only** (model preset + `googleSearch` + restart) |

#### Skill manifest tools (optional, if installed)

| Skill | Tools |
|-------|-------|
| zoom | `summon`, `dismiss`, `join_zoom` |
| screen-companion | `activate_screen_companion`, `deactivate_screen_companion`, `vision_query`, `take_note`, `look_up_reference` (+ dynamic `updateTools()` shrink) |
| gws-gmail-voice | `triage_email`, `read_email`, `search_email` (if `gws` CLI present) |
| obsidian-vault | `add_to_vault`, `run_dream` |

**Total:** ~43 base + up to ~12 skill tools ≈ **55** at full install.

#### Not tools (related capabilities)

| Item | What it is |
|------|------------|
| `googleSearch` | Gemini-native web grounding on the session — **not** a function tool |
| `coreDocumentedSkills` | Prompt text only ("use `work` for …") — skills run in core, not inline |
| `end_session` | Defined in code but **deliberately not registered** in the tool list |

#### Per-tool Omni compatibility

| Category | Omni compatible? |
|----------|------------------|
| Orchestration (`work`, status, cancel) | ✅ execution; ⚠️ realtime tool loop on web voice |
| Inline macOS/browser/control | ✅ once tool loop works |
| `describe_screen`, `point_at` | ✅ via **separate Gemini REST API** (not realtime provider) |
| Realtime vision (`send_vision_frame`, Watch) | ❌ needs `vision-adapter.ts` |
| `googleSearch`, `switch_voice_config` | ❌ Gemini-only |
| Skill tools (zoom, gmail, obsidian) | ✅ execute(); screen-companion `updateTools` ⚠️ |

**Policy on Omni:** `work` is the preferred path for heavy/current-info tasks — keeps
`enable_search` off while preserving the full inline tool surface.

---

## 5. Target architecture

### 5.1 Dependency direction (aligned with architecture-boundaries.md)

```text
schemas / config
    ↓
src/realtime-provider/     ← NEW: factory + config + capability matrix
    ↓
bodhi LLMTransport         ← external; GeminiLiveTransport | OpenAIRealtimeTransport
    ↓
vendor APIs

src/vision-tools.ts        ← provider-neutral capture orchestration
    ↓
realtime-provider/vision-adapter   ← NEW: per-provider frame injection
    ↓
LLMTransport

src/observability/         ← provider-neutral contracts (extend payloads)
```

**Rule:** Core modules (`task-bridge`, `live-agent-runtime`, `web-voice-transport`) must
not import vendor SDKs. They receive a resolved `RealtimeSessionDescriptor` (provider id,
model, capabilities) from the factory at session start.

### 5.2 Module layout (proposed)

```text
src/realtime-provider/
  index.ts              # createRealtimeTransport(config) → LLMTransport
  config.ts             # resolveRealtimeConfig(): RealtimeConfig from env + workspace
  types.ts              # RealtimeProviderId, RealtimeCapabilities, RealtimeConfig
  capabilities.ts       # feature matrix: vision, tools, grounding, sample rates
  gemini.ts             # buildGeminiTransport(config)
  openai-compat.ts      # buildOpenAICompatTransport(config) — qwen, openai, minimax
  vision-adapter.ts     # injectVisionFrame(transport, frame) — provider dispatch
  errors/
    index.ts            # classifyTransportClose(provider, code, reason)
    gemini.ts
    qwen.ts
  python/
    __init__.py         # create_realtime_model() — migrate from livekit-agent.py
    qwen_compat.py      # move/re-export qwen_realtime_compat.py
```

Python factory lives under `realtime-provider/python/` so LiveKit and future Python
surfaces import one module. TypeScript factory serves `voice-agent.ts` and
`phone-conversation`.

### 5.3 Config schema

**Resolution order:** CLI flags > env > skill/workspace manifest > defaults  
(same convention as [`skills/MANIFEST.md`](../skills/MANIFEST.md))

```typescript
interface RealtimeConfig {
  provider: 'gemini' | 'qwen' | 'openai' | 'minimax';
  model: string;
  voice: string;
  baseUrl?: string;           // OpenAI-compat providers
  apiKey: string;             // resolved via credential layer
  capabilities: RealtimeCapabilities;
  turnDetection?: TurnDetectionConfig;
  transcription?: TranscriptionConfig | null;
}

interface RealtimeCapabilities {
  nativeAudio: boolean;       // speech-in / speech-out on realtime socket
  vision: 'sendFile' | 'input_image_buffer' | 'none';
  toolCalling: boolean;
  googleSearch: boolean;      // Gemini-only; false elsewhere
  inputSampleRate: number;    // e.g. 16000
  outputSampleRate: number;   // e.g. 24000
  maxVisionFps: number;       // provider limit (Omni: 1 fps recommended)
  maxImageBytes: number;      // Omni: 256 KB base64
  requiresAudioBeforeVision: boolean;  // Omni: true
}
```

**Env mapping (unchanged names, centralized parsing):**

| Variable | Default (gemini) | Default (qwen) |
|----------|------------------|----------------|
| `REALTIME_PROVIDER` | `gemini` | `qwen` |
| `REALTIME_MODEL` | from `voice-agent.json` | `qwen3.5-omni-plus-realtime` |
| `REALTIME_BASE_URL` | — | `https://dashscope.aliyuncs.com/api-ws/v1` |
| `DASHSCOPE_API_KEY` | — | required |
| `QWEN_REALTIME_VOICE` | — | `Ethan` |

Workspace JSON (`config/voice-agent.json`) remains the **Gemini tuning surface**
(model preset, `googleSearch`). OpenAI-compat knobs stay env-driven until a
provider-neutral manifest block is added in phase 2.

### 5.4 Factory API

```typescript
// src/realtime-provider/index.ts
export function resolveRealtimeConfig(): RealtimeConfig;
export function createRealtimeTransport(config: RealtimeConfig): LLMTransport;
export function describeRealtimeSession(config: RealtimeConfig): RealtimeSessionDescriptor;
```

`voice-agent.ts` becomes:

```typescript
const rtConfig = resolveRealtimeConfig();
const transport = createRealtimeTransport(rtConfig);
const session = new VoiceSession({ transport, /* ... */ });
```

Phone conversation adopts the same call after mu-law ↔ PCM validation.

---

## 6. Realtime vision — web/HTML → model

### 6.1 Current browser path (unchanged at the edge)

```text
User clicks Watch
  → getDisplayMedia (screen/window/tab)
  → canvas resize (720p) → JPEG q≈0.6
  → POST /vision/frame (binary body)
  → web-client proxies to voice-agent :vision-control
  → vision-tools.ts:submitFrame()
  → transport.sendFile(b64, 'image/jpeg')   ← Gemini-specific today
```

The **browser contract stays fixed**: JPEG POST to `/vision/frame`. No provider
logic in `web-client.ts` beyond telemetry (frame count, fps, errors).

### 6.2 Provider vision adapters

Alibaba [documents](https://www.alibabacloud.com/help/en/model-studio/realtime)
WebSocket vision as `input_image_buffer.append` with constraints:

- JPG/JPEG, 480p–720p recommended, max ~256 KB base64 (~190 KB raw)
- ~1 fps recommended; audio must be sent at least once before first image
- Context retains cumulative frame duration (e.g. 240 s window)

Gemini uses bodhi's `sendFile(base64, mimeType)` → `realtime_input` video slot.

**New adapter** (`vision-adapter.ts`):

```typescript
export interface VisionInjectResult {
  ok: boolean;
  error?: string;
  bytesSent?: number;
}

export function injectVisionFrame(
  transport: LLMTransport,
  capabilities: RealtimeCapabilities,
  frame: { data: Buffer; mimeType: string },
  ctx: { audioSent: boolean; framesSent: number },
): VisionInjectResult;
```

| Provider | Mechanism | Adapter behavior |
|----------|-----------|------------------|
| **gemini** | `sendFile(b64, 'image/jpeg')` | Direct delegate to bodhi |
| **qwen/omni** | `input_image_buffer.append` | OpenAI-compat transport event; enforce size cap + audio-first gate |
| **openai** | `conversation.item.create` image part | Map when OpenAI realtime vision is enabled |
| **none** | — | Return `{ ok: false, error: 'vision unsupported' }` |

`vision-tools.ts` changes:

1. Replace direct `getSendFile()` with `injectVisionFrame()` using session's
   resolved `RealtimeCapabilities`.
2. Track `audioSent` on the session (first PCM chunk from client → true).
3. Drop frames that exceed `maxImageBytes` with a logged warning (activity log event).
4. Throttle to `min(requestedFps, capabilities.maxVisionFps)`.

### 6.3 Web client telemetry (activity log)

Extend `/activity-log` snapshot and client-side `logActivity()`:

```json
{
  "vision": {
    "mode": "push",
    "source": "browser",
    "streaming": true,
    "fpsTarget": 1,
    "fpsActual": 0.9,
    "framesSent": 142,
    "framesDropped": 3,
    "lastFrameBytes": 87432,
    "lastError": null,
    "providerSupportsVision": true
  }
}
```

Right-panel activity log gets a **Vision** chip (alongside Voice / LLM / Core /
Watcher / Bridge): `Vision: 1 fps · 142 frames` or `Vision: unsupported (qwen — no audio yet)`.

### 6.4 LiveKit / mobile vision

LiveKit path receives video as RTP tracks (Alibaba WebRTC mode). That is **out of
scope for v1** web push-mode work but shares the same `RealtimeCapabilities.vision`
flag so the agent prompt can say "I can see your screen" only when true.

---

## 7. Observability, logging, and tracing

### 7.1 Design principles

1. **Hot path never blocks** — keep fire-and-forget POST semantics (`observability/realtime.ts`).
2. **One trace per session** — preserve `voice-sess:<id>` / `phone-call:<sid>` scheme (`realtime-map.ts`).
3. **Provider on every record** — stop defaulting silently to `gemini-live`.
4. **Raw client, map in collector** — surfaces send raw payloads; `RealtimeNormalizer` maps (unchanged split).

### 7.2 Usage payload extensions

Extend `RawVoiceUsage` / session recorder:

```typescript
interface RawVoiceUsage {
  // existing fields…
  provider: string;           // required, e.g. 'dashscope-omni' | 'gemini-live'
  visionFrames?: number;      // cumulative frames injected this session
  visionBytes?: number;       // cumulative JPEG bytes
  transportCloseCategory?: string;  // from error classifier on session end
}
```

Provider ID registry (collector-side constants):

| Provider key | Source |
|--------------|--------|
| `gemini-live` | Gemini Live native audio |
| `dashscope-omni` | Qwen-Omni-Realtime via DashScope |
| `openai-realtime` | OpenAI Realtime API |
| `minimax-realtime` | MiniMax |

`live-agent-runtime.ts` `createSessionRecorder` passes `provider` into
`startVoiceTicker({ provider, … })` from resolved config.

### 7.3 Structured session events (pipeline + obs)

New pipeline phases (via `pipeline_emit.py`):

| Phase | When |
|-------|------|
| `realtime_provider_selected` | voice-agent / phone / LiveKit session start |
| `realtime_transport_connected` | LLM WS open |
| `realtime_transport_closed` | LLM WS close (+ category) |
| `vision_stream_started` | `/vision/start` success |
| `vision_frame_sent` | sampled (every N frames, not every frame) |
| `vision_frame_dropped` | size/rate/provider gate |
| `vision_stream_stopped` | `/vision/stop` or session end |

Obs events (collector spine):

```json
{
  "kind": "realtime.session.started",
  "data": { "provider": "dashscope-omni", "model": "qwen3.5-omni-plus-realtime", "surface": "web" }
}
```

Trace correlation: all events for one voice session share `trace_id: voice-sess:<sessionId>`.

### 7.4 Activity log (`/activity-log`)

Upgrade `buildActivityLogSnapshot()` in `web-client.ts`:

```typescript
{
  realtimeProvider: 'qwen',
  realtimeModel: 'qwen3.5-omni-plus-realtime',
  realtimeCapabilities: { vision: true, googleSearch: false },
  voice: { connected, wsConnected, llmReady, bytesSent, bytesRecv },
  vision: { streaming, framesSent, fpsActual, lastError },
  // existing core, watcher, pipeline…
}
```

Client rendering rules:

- **LLM chip** — provider-aware ready signal (remove Gemini-only `geminiBlocked` assumption; add Qwen-specific stall detection: WS up, zero bytes after N s).
- **Vision chip** — new.
- **Pipeline section** — prefix provider switch events.

### 7.5 Error classification

Replace Gemini-only `voice-error-classifier.ts` with provider dispatch:

```typescript
classifyTransportClose(provider: RealtimeProviderId, code: number, reason: string): ClassifiedClose
```

Qwen patterns to add (from DashScope docs / empirical logs):

- Invalid API key / workspace ID
- Region mismatch (Singapore vs Beijing key)
- Image buffer errors (audio-not-sent-yet, oversize frame)
- Quota / rate limit

Surfaces user-facing messages through existing voice-agent disconnect path and
activity log `err` entries.

### 7.6 Debug / session recorder

`live-agent-runtime` session JSON (conversation store) adds:

```json
{
  "realtime": {
    "provider": "dashscope-omni",
    "model": "qwen3.5-omni-plus-realtime",
    "visionFrames": 142
  }
}
```

Export path unchanged; fields are additive for regression search.

---

## 8. Credentials

Extend `credential-resolver.ts` with capabilities:

| Capability | Used when |
|------------|-----------|
| `gemini-voice` | `REALTIME_PROVIDER=gemini` |
| `dashscope-realtime` | `REALTIME_PROVIDER=qwen` |
| `openai-realtime` | `REALTIME_PROVIDER=openai` |

`resolveRealtimeConfig()` calls the matching capability; startup fails fast with
provider-specific remediation text (same pattern as `assertDashScopeKey` today).

Vault key: `DASHSCOPE_API_KEY` via existing secret-vault skill.

---

## 9. Surface rollout matrix

| Surface | Phase | Work |
|---------|-------|------|
| **Web voice** (`voice-agent.ts`) | **P0** | Extract factory; wire vision adapter; observability |
| **LiveKit** (`livekit-agent.py`) | **P0** | Move factory to `realtime-provider/python/` |
| **Web client** | **P0** | Activity log vision chip; provider-neutral LLM stall heuristic |
| **Phone** | **P1** | Inject transport; validate 8 kHz mu-law with Qwen output rate |
| **Vision tools / prompts** | **P1** | De-Gemini-ify tool descriptions ("you (Gemini)" → neutral) |
| **switch_voice_config** | **P2** | Provider-aware presets or document env-only switch |
| **Credential managed tier** | **P2** | DashScope in `managed-credentials.json` schema |

---

## 10. Migration plan

**Principles:** each phase is **revertable** (env flags), **recoverable** (fail-fast validation),
**continuable** (durable checkpoint at `<workspace>/state/realtime-provider-migration.json`).

**Operator commands:**

```bash
bash scripts/realtime-provider-migrate.sh status    # read checkpoint + .env
bash scripts/realtime-provider-migrate.sh init      # create checkpoint file
bash scripts/realtime-provider-migrate.sh verify 0  # run vendor tool spike
bash scripts/realtime-provider-migrate.sh verify 1  # run factory contract tests
bash scripts/realtime-provider-migrate.sh rollback  # print safe .env rollback
```

**Instant rollback (no code change):**

```bash
REALTIME_PROVIDER=gemini
REALTIME_USE_FACTORY=1
REALTIME_VISION_ADAPTER=0
# restart voice-agent / livekit-agent
```

**Legacy inline Qwen (pre-factory code path):** `REALTIME_USE_FACTORY=0 REALTIME_PROVIDER=qwen`

### Phase 0 — Design + spike (this doc)

- [x] Vendor compatibility audit (§4.4–4.6)
- [x] Run `scripts/test-qwen-realtime-tools.py` (vendor-layer tool calling OK 2026-08-05)
- [x] Run `scripts/test-qwen-realtime-audio.py` (VAD/transcription OK 2026-08-05)
- [x] Add `scripts/test-qwen-realtime-vision.py` (audio-first → image append OK 2026-08-05)
- [ ] Document region/workspace URL format from Alibaba docs

### Phase 1 — Factory extraction (no default change)

- [x] Add `src/realtime-provider/` with config + capabilities + openai-compat builders
- [x] Refactor `voice-agent.ts` to use factory (`REALTIME_USE_FACTORY=1` default)
- [x] Move `livekit-agent.py:create_realtime_model()` to `src/realtime_provider/factory.py`
- [x] Contract tests: `tests/realtime-provider-config.test.ts`, `tests/realtime-provider-factory.test.py`
- [x] Migration checkpoint: `migration-state.ts` + `scripts/realtime-provider-migrate.sh`

### Phase 2 — Vision adapter + observability

- [x] Stub + wire `vision-adapter.ts`; Qwen Watch gated on `REALTIME_VISION_ADAPTER=1`
- [x] Provider-dispatch error classifier (`realtime-provider/errors/`)
- [x] Pass `provider` through usage tickers (`createSessionRecorder`)
- [x] Activity log: `voice-agent.json` realtime fields + `/activity-log` snapshot
- [ ] Pipeline phases (`realtime_provider_selected`, vision_frame_sent, …)
- [ ] Full bodhi `sendEvent` validation on live Qwen web voice session

### Phase 3 — Qwen/Omni as opt-in production path

- [x] `.env.example` update
- [x] `scripts/test-realtime-provider-e2e.sh` + `tests/realtime-provider-e2e.test.ts`
- [x] Vendor E2E: tools + audio + config resolution (with `DASHSCOPE_API_KEY`)
- [ ] Live web voice round-trip with `REALTIME_PROVIDER=qwen` (manual / post-restart)
- [ ] LiveKit parity check on same env block

### Phase 4 — Phone + cleanup

- [ ] Phone transport injection
- [ ] Remove inline `buildQwenVoiceTransportLegacy` dead code (after Phase 3 stable)
- [ ] Gemini-specific guards (`googleSearch`, geo-block) gated on capabilities

---

## 11. Testing strategy

| Layer | Test |
|-------|------|
| Config | Unit: env combos, missing keys, workspace JSON ignored for qwen |
| Factory | Unit: gemini → GeminiLiveTransport; qwen → OpenAIRealtimeTransport with base URL |
| Vision adapter | Unit: mock transport; audio-first gate; oversize drop |
| Integration | `test-qwen-realtime-audio.py`, `test-qwen-realtime-tools.py`, `test-qwen-realtime-vision.py` (planned) |
| Live path | Manual: Watch toggle → activity log vision chip → ask "what do you see" |
| Observability | Assert `provider: dashscope-omni` in collector ingest fixture |
| Regression | Gemini default unchanged: existing voice-agent tests green |

---

## 12. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Qwen protocol drift from OpenAI realtime | Keep `qwen_realtime_compat.py`; version-pin compat patches |
| Omni requires audio before vision | Track `audioSent`; queue/drop frames with user-visible activity log message |
| bodhi `sendFile` doesn't map to Omni | Vision adapter bypasses `sendFile` for `input_image_buffer` providers |
| 256 KB frame limit | Resize/compress in `submitFrame()` before adapter (720p q=0.6 usually OK; monitor drops) |
| Phone audio rate mismatch | P1 gate; test Twilio 8 kHz path explicitly |
| Cost / context growth from vision | Keep default Watch off; log `visionBytes` in usage attrs for future throttle |

---

## 13. Open questions

1. **bodhi fork vs upstream** — Does upstream bodhi need a PR for `input_image_buffer.append`, or does OpenAIRealtimeTransport already expose a generic `sendEvent()`?
2. **Provider ID naming** — `qwen` vs `dashscope-omni` in telemetry (recommend `dashscope-omni` for billing clarity, `qwen` for env switch)?
3. **Workspace manifest** — Should `config/voice-agent.json` gain a `provider` block, or stay env-only for v1?
4. **WebRTC direct** — Worth a phase-5 spike for browser → DashScope WebRTC (bypasses server relay latency)?

---

## 14. Success criteria

- [ ] `REALTIME_PROVIDER=qwen` + `DASHSCOPE_API_KEY` → web voice works with restart only
- [ ] Watch → model answers screen-content questions on Qwen Omni
- [ ] Activity log shows provider, model, vision fps/frames
- [ ] Usage collector receives `provider: dashscope-omni` on voice ticks
- [ ] `REALTIME_PROVIDER=gemini` remains default with no regression
- [ ] Switching back to Gemini is env + restart only (no code change)
