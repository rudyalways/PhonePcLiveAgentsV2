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

1. **PC** opens `https://<local-ip>:7081/` — publishes screen to the LiveKit room (or `http://<local-ip>:7080/` for the Sutando voice web UI)
2. **Phone** uses the Flutter app (default server port `7081`) or `https://<local-ip>:7081/mobile` in the browser — joins the room, sends mic audio, sees PC screen
3. **Agent** subscribes to the phone's audio, sends it to a Realtime AI model (Qwen, Gemini, or OpenAI), publishes TTS audio back to the room
4. The AI model can call tools: open apps, press keys, type text, describe screen, or delegate complex tasks to a Claude Code backend

## Quick start

### Prerequisites

- Python 3.11+
- A [LiveKit Cloud](https://cloud.livekit.io) account (free tier works)
- An AI model API key (one of: DashScope for Qwen, Google AI for Gemini, or OpenAI)
- PC and phone on the same WiFi network

### 1. Configure `.env`

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

### 2. Add a user

Each connecting client must authenticate with a username and secret. Add at least one user before starting:

```bash
python3 src/add-user.py <username> <secret>
```

Manage users:

```bash
python3 src/add-user.py --list                    # list all users
python3 src/add-user.py <username> <secret> --update  # change secret
python3 src/add-user.py <username> --delete        # remove user
```

### 3. Start the services

```bash
bash src/deploy.sh
```

On first run, this automatically creates a Python virtual environment (`.venv-livekit/`) and installs dependencies from `requirements-livekit.txt`. Subsequent runs reuse the existing venv.

This starts all services in the background:
- **Token server** (port 7850) — JWT authentication
- **Screen publisher server** (port 7081) — HTTPS web server + token proxy
- **Mobile control server** (port 7901) — Remote control API
- **Screen capture server** (port 7900) — HTTP `/capture` for agents and tools
- **Pipeline trace** (port 7902) — Pipeline Trace UI
- **AI agent** (port 7082) — LiveKit worker + speech / Realtime AI
- **sutando-core** — Task processing engine (auto-starts if not running)

Logs are written to `logs/`. 

**Stop all services:**
```bash
bash src/deploy.sh --stop
```

**Restart all services:**
```bash
bash src/deploy.sh --restart
```

**Follow live logs (for debugging):**
```bash
bash src/deploy.sh --logs
```

### 4. Connect

1. On your **PC browser**, open `https://localhost:7081/` — click "Publish Screen" to share your screen
2. On your **phone**, use the Flutter app (recommended) or open `https://<pc-local-ip>:7081/mobile` in the browser — tap "Connect"
3. Speak to your phone — the AI agent will respond and execute commands on your PC

> Accept the self-signed certificate warning on both devices. HTTPS is required for WebRTC microphone access.

## Mobile app (Flutter)

Native iOS/Android app replacing the web-based mobile client. Provides better audio handling, automatic landscape fullscreen, and native controls.

### Build & run

```bash
cd app
flutter pub get
flutter run          # connected device or emulator
```

### Tech stack

- **State management**: GetX
- **HTTP**: Dio (with self-signed cert support for LAN)
- **WebRTC**: livekit_client

### Features

- Voice input (mic) to AI agent
- Real-time PC screen viewing (remote video)
- AI TTS audio playback
- Mute / disconnect controls
- Auto landscape fullscreen (immersive mode on rotation)

## Project structure

```
src/
├── livekit-agent.py            # AI agent — speech processing, tool execution, Qwen compat patches
├── livekit-token-server.py     # JWT token server for LiveKit room access (port 7850)
├── screen-publisher-server.py  # HTTPS server serving web clients + token proxy (port 7081)
├── screen-publisher.html       # PC client — captures and publishes screen to LiveKit room
├── mobile-client.html          # Phone client (web fallback) — mic input, screen view, audio playback
├── start-livekit.sh            # One-command launcher for all three services
app/                            # Flutter mobile app (iOS + Android)
├── lib/
│   ├── main.dart               # App entry point, self-signed cert handling
│   ├── controllers/            # GetX controllers (connect, room)
│   ├── pages/                  # UI pages (connect, room with video layer)
│   ├── services/               # Token service (Dio)
│   └── config/                 # App constants
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

Screen and browser preference: for "what's on my screen" or logged-in sites, use the user's existing Mac/browser session first (`describe_screen`, `/usr/sbin/screencapture`, or macOS Accessibility via `macos-use`). Do not switch to Playwright, Chromium, or another browser profile if that would require a separate login, lose cookies, or change session context; use browser automation only when explicitly requested or when no extra login/session setup is needed.

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
| `CLIENT_PORT` | `7081` | Screen publisher HTTPS port (`screen-publisher-server.py`) |
| `PORT` | `7980` | Voice agent WebSocket / browser WS to voice session |
| `LIVEKIT_WORKER_PORT` | `7082` | Python LiveKit agent worker HTTP health/bind port |

## Qwen compatibility

Qwen's Realtime API deviates from OpenAI's protocol in several ways. The agent includes automatic monkey-patches (`_patch_qwen_compat()`) that handle:

- **Event name mapping** — Qwen uses shorter names (`response.audio.delta` → `response.output_audio.delta`)
- **Missing fields** — auto-fills `output_index`, `content_index`, `item_id`
- **Out-of-order events** — auto-creates generation state when events arrive before `response.created`
- **Tool reply handling** — auto-resolves `conversation.item.create` futures, nudges response generation after tool output
- **Argument serialization** — handles Qwen's string-encoded arrays in function call arguments

## License

MIT
