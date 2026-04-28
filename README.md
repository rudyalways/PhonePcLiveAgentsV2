# PhonePcLiveAgents

Use your phone to voice-control your PC through AI. Speak into your phone's browser, see your PC screen in real-time, and let the AI agent execute commands on your computer.

## How it works

```
┌──────────┐     LiveKit Cloud     ┌──────────────┐     Realtime API     ┌───────────┐
│  Phone   │ ◄──── WebRTC ────►   │  LiveKit Room │ ◄──────────────────► │ AI Model  │
│ (browser)│   audio + video       │              │                      │ (Qwen /   │
│          │                       │              │                      │  Gemini / │
└──────────┘                       └──────┬───────┘                      │  OpenAI)  │
                                          │                              └───────────┘
                                          │ subscribe
                                          ▼
                                   ┌──────────────┐
                                   │    Agent     │ ──► executes tools on PC
                                   │ (Python)     │     (open apps, press keys,
                                   │              │      type text, take screenshots,
                                   └──────────────┘      delegate tasks to Claude Code)
                                          ▲
                                          │ screen share
                                   ┌──────────────┐
                                   │  PC Browser  │ captures & publishes screen
                                   └──────────────┘
```

1. **PC** opens `https://<local-ip>:8080/` — shares screen to the LiveKit room
2. **Phone** opens `https://<local-ip>:8080/mobile` — joins the room, sends mic audio, sees PC screen
3. **Agent** subscribes to the phone's audio, sends it to a Realtime AI model (Qwen, Gemini, or OpenAI), publishes TTS audio back to the room
4. The AI model can call tools: open apps, press keys, type text, describe screen, or delegate complex tasks to a Claude Code backend

## Quick start

### Prerequisites

- Python 3.11+
- A [LiveKit Cloud](https://cloud.livekit.io) account (free tier works)
- An AI model API key (one of: DashScope for Qwen, Google AI for Gemini, or OpenAI)
- PC and phone on the same WiFi network

### 1. Install dependencies

```bash
pip install -r requirements-livekit.txt
```

### 2. Configure `.env`

```bash
cp .env.example .env
```

Required variables:

```env
# LiveKit Cloud
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your-api-key
LIVEKIT_API_SECRET=your-api-secret

# AI Model — pick one provider
REALTIME_PROVIDER=qwen          # qwen | gemini | openai | minimax

# Qwen (DashScope)
DASHSCOPE_API_KEY=sk-xxx

# Or Gemini
# GEMINI_API_KEY=xxx

# Or OpenAI
# OPENAI_API_KEY=sk-xxx
```

### 3. Start the services

```bash
# Terminal 1 — Token server (port 7850)
python3 src/livekit-token-server.py

# Terminal 2 — HTTPS web server (port 8080)
python3 src/screen-publisher-server.py

# Terminal 3 — AI agent
python3 src/livekit-agent.py
```

### 4. Connect

1. On your **PC browser**, open `https://localhost:8080/` — click "Publish Screen" to share your screen
2. On your **phone browser**, open `https://<pc-local-ip>:8080/mobile` — tap "Connect"
3. Speak to your phone — the AI agent will respond and execute commands on your PC

> Accept the self-signed certificate warning on both devices. HTTPS is required for WebRTC microphone access.

## Project structure

```
src/
├── livekit-agent.py            # AI agent — speech processing, tool execution, Qwen compat patches
├── livekit-token-server.py     # JWT token server for LiveKit room access (port 7850)
├── screen-publisher-server.py  # HTTPS server serving web clients + token proxy (port 8080)
├── screen-publisher.html       # PC client — captures and publishes screen to LiveKit room
└── mobile-client.html          # Phone client — mic input, screen view, audio playback
requirements-livekit.txt        # Python dependencies
voice-state.json                # Runtime state (connection status)
```

## Supported AI providers

| Provider | Model | Env var | Notes |
|----------|-------|---------|-------|
| **Qwen** | `qwen3.5-omni-plus-realtime` | `DASHSCOPE_API_KEY` | Chinese language optimized, includes Qwen compat patches |
| **Gemini** | `gemini-2.5-flash-native-audio` | `GEMINI_API_KEY` | Google's multimodal model |
| **OpenAI** | `gpt-4o-realtime-preview` | `OPENAI_API_KEY` | Standard OpenAI Realtime API |
| **MiniMax** | `minimax-realtime` | `MINIMAX_API_KEY` | Experimental |

Set `REALTIME_PROVIDER` in `.env` to switch. Default is `gemini`.

## Built-in tools

The agent can execute these tools on your PC via voice:

- **work** — delegate complex tasks to a Claude Code backend (file ops, research, coding)
- **open_url** — open any URL in the default browser
- **switch_app** — activate a macOS app by name
- **press_key** — press keyboard shortcuts (e.g., Cmd+Space, Cmd+Tab)
- **type_text** — type text into the focused field
- **describe_screen** — capture and describe what's on screen
- **get_current_time** — get current date and time

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LIVEKIT_URL` | — | LiveKit Cloud WebSocket URL |
| `LIVEKIT_API_KEY` | — | LiveKit API key |
| `LIVEKIT_API_SECRET` | — | LiveKit API secret |
| `REALTIME_PROVIDER` | `gemini` | AI provider: `qwen`, `gemini`, `openai`, `minimax` |
| `LIVEKIT_ROOM` | `sutando-room` | LiveKit room name |
| `REALTIME_MODEL` | per-provider | Override the default model |
| `REALTIME_VOICE` | per-provider | TTS voice name |
| `TOKEN_SERVER_PORT` | `7850` | Token server port |
| `CLIENT_PORT` | `8080` | Web client HTTPS port |

## Qwen compatibility

Qwen's Realtime API deviates from OpenAI's protocol in several ways. The agent includes automatic monkey-patches (`_patch_qwen_compat()`) that handle:

- **Event name mapping** — Qwen uses shorter names (`response.audio.delta` → `response.output_audio.delta`)
- **Missing fields** — auto-fills `output_index`, `content_index`, `item_id`
- **Out-of-order events** — auto-creates generation state when events arrive before `response.created`
- **Tool reply handling** — auto-resolves `conversation.item.create` futures, nudges response generation after tool output
- **Argument serialization** — handles Qwen's string-encoded arrays in function call arguments

## License

MIT
