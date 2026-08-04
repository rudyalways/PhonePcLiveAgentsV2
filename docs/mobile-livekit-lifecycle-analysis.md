# Mobile LiveKit Lifecycle Analysis

## Facts

### Components and Responsibilities

| Component | Location | Responsibility |
| --- | --- | --- |
| iPhone Flutter app | `app/lib/controllers/connect_controller.dart`, `app/lib/controllers/room_controller.dart` | Collects server/user credentials, fetches a LiveKit JWT, joins the LiveKit room, publishes microphone audio, subscribes to remote tracks, displays room status, and sends remote-control input over HTTP. |
| iOS runtime | `app/ios/Runner/Info.plist` | Grants microphone/camera/local-network permissions and declares `UIBackgroundModes` with `audio`. This helps active audio continue in background but does not guarantee indefinite WebRTC or AVAudioSession health. |
| Token server | `src/livekit-token-server.py` | Authenticates the user, returns a 24-hour LiveKit JWT for the user's configured room, exposes `/room-state`, and dispatches the local LiveKit agent when `phone-user` requests a token. |
| LiveKit Cloud/server | external | Owns rooms, participants, tracks, signaling, SFU media routing, reconnection, participant disconnect events, and agent dispatch. |
| LiveKit agent worker | `src/livekit-agent.py` | Accepts one job per room, connects as an agent participant, creates an `AgentSession`, links input to `phone-user`, connects to Gemini/Qwen realtime model, exposes tools, and watches task results. |
| Gemini/Qwen realtime provider | external websocket | Receives streamed audio/text/tool events from LiveKit Agents and returns assistant audio/transcript/tool calls. |
| Sutando core session | file bridge through `tasks/` and `results/` | Executes non-trivial work requested by the voice agent. It is not directly called over a socket by the phone app or LiveKit; communication is via task/result files. |
| Result watcher | `src/livekit-agent.py` | Watches `results/task-*.txt`, consumes matching result files, archives task/result files, and asks the agent session to speak the result into the LiveKit room. |

### Protocols and Connecting Points

| Link | Protocol | Initiator | Notes |
| --- | --- | --- | --- |
| iPhone app to token server | HTTPS/HTTP JSON | Phone app | `ConnectController.connect()` calls token service, receives `jwt`, `room`, `url`, and user metadata. |
| iPhone app to LiveKit | WebRTC plus LiveKit signaling | Phone app | `RoomController._initRoom()` creates `Room`, connects with the JWT, then enables microphone. |
| Token server to LiveKit API | LiveKit server API | Token server | Used to list participants, remove stale agents, delete stale dispatches, and create agent dispatch. |
| LiveKit server to agent worker | LiveKit Agents job dispatch | LiveKit | Dispatches one job for the user's room when the token server requests it. |
| Agent worker to LiveKit room | LiveKit RTC | Agent worker | `ctx.connect()` connects the worker to the room as an agent participant. |
| Agent worker to Gemini/Qwen | Provider websocket | LiveKit Agents runtime | Created through `AgentSession(llm=realtime_model)` and `session.start(...)`. |
| Agent worker to Sutando core | File bridge | Agent tool call | `work()` writes `tasks/task-*.txt`; core later writes `results/task-*.txt`. |
| Agent result watcher to user | LiveKit realtime media | Agent session | `session.generate_reply(...)` produces audio into the room. There is no durable LiveKit voice queue. |

### Join Time Sequence

```text
User taps Connect in iPhone app
  -> ConnectController normalizes server URL and validates username/secret
  -> token service calls token server /token
  -> token server validates secret against users.json
  -> token server creates JWT for the user's room and identity phone-user
  -> token server starts background ensure_agent_dispatched(room)
  -> app navigates to Room screen with jwt/livekitUrl/serverUrl/username/secret
  -> RoomController creates LiveKit Room
  -> RoomController attaches listeners for tracks, disconnect, participant changes
  -> RoomController connects to LiveKit using jwt/livekitUrl
  -> RoomController enables microphone
  -> LiveKit sees phone-user join/publish mic
  -> LiveKit dispatches local agent if no current agent participant exists
  -> livekit-agent entrypoint starts for the room
  -> livekit-agent calls ctx.connect()
  -> livekit-agent creates SutandoAgent and realtime model
  -> AgentSession starts with participant_identity=phone-user
  -> AgentSession opens Gemini/Qwen realtime session
  -> Result watcher starts
  -> Greeting may be generated for non-Qwen providers
```

### User Task Time Sequence

```text
User speaks in iPhone app
  -> phone mic publishes audio to LiveKit
  -> LiveKit routes phone-user audio to agent participant
  -> AgentSession forwards audio to Gemini/Qwen realtime websocket
  -> realtime model either answers directly or calls tool work()
  -> work() writes tasks/task-*.txt
  -> sutando-core task watcher processes the file
  -> sutando-core writes results/task-*.txt
  -> livekit-agent result watcher sees the result
  -> result watcher archives active task/result files
  -> result watcher calls session.generate_reply(...)
  -> realtime model generates answer audio
  -> LiveKit publishes answer audio to room
  -> phone hears it only if online, connected, subscribed, and audio session is healthy
```

### Leave and Rejoin Facts

Manual leave in the current app calls `room.disconnect()` and returns to the previous screen. The app does not send a separate "end product session" command to the token server, LiveKit agent, realtime LLM, or Sutando core.

The LiveKit agent logs `participant_disconnected` but starts `AgentSession` with `close_on_disconnect=False` and `participant_identity="phone-user"`. Therefore, `phone-user` leaving does not automatically close the agent session. The agent and realtime provider session may remain alive until the LiveKit job shuts down, the provider times out, the process restarts, or another shutdown path happens.

If the phone rejoins soon with the same configured room and `phone-user` identity, the existing agent session may relink to that participant. This is closer to "leave the UI but keep assistant session warm" than to "end the conversation."

### Result Delivery Facts When Phone Is Offline

The result watcher consumes the result file before speaking it. It marks the task as delivered, archives task/result files, then calls `session.generate_reply(...)`.

If the phone is not in the room, the generated voice is realtime media with no recipient. LiveKit does not store that audio for later replay. The app has no unread result queue, no notification, and no "deliver on next join" persistence. From the user's perspective, delayed results can be missed if the phone leaves before they are spoken.

### Current Status UI Facts

The phone app sets `statusText` to `Connected` after `room.connect()` succeeds and `Disconnected` on `RoomDisconnectedEvent`. It does not currently model intermediate states like reconnecting, reconnected, audio interrupted, backgrounded, task pending, result ready, or result missed.

## Analysis

### Role Model

There are three separate lifecycle concepts that currently overlap but are not explicitly named in code:

| Concept | Owner | Current behavior |
| --- | --- | --- |
| LiveKit room lifecycle | LiveKit server | Room exists while participants/dispatch/job lifecycle keep it alive. It is transport state, not product intent. |
| Agent/realtime session lifecycle | LiveKit agent worker plus Gemini/Qwen | Kept alive across phone disconnect because `close_on_disconnect=False`. This preserves warmth but can create orphaned speech/results. |
| Product conversation lifecycle | Sutando product | Not explicitly represented. Manual leave, transient backgrounding, and phone-call interruption are not distinguished at the backend/session level. |

The missing product session concept is the biggest source of ambiguity. Without it, the system cannot reliably decide whether a phone disconnect means "pause and wait for rejoin" or "end the assistant session and close the realtime LLM."

### Join Walkthrough and Reliability Points

1. Credential and token fetch
   - The phone depends on the token server being reachable from the mobile network path.
   - Token creation depends on `LIVEKIT_URL`, API credentials, and a valid user record.
   - The token has a 24-hour TTL, so reconnect/rejoin can use newly fetched credentials, but the app does not currently refresh token inside `RoomController` after a disconnect.

2. Agent dispatch
   - The token server returns the JWT immediately, then dispatches the agent in a background thread.
   - This means the phone can join before the agent is confirmed.
   - The token server polls up to 15 seconds and uses a heartbeat to detect a local worker, but the phone UI does not show a distinct "agent not ready" state.

3. Room connect and mic enable
   - `RoomController` connects, enables mic, and sets status to `Connected`.
   - If mic publishing, iOS audio session, or remote subscription breaks after connection, the status can remain stale.

4. Realtime provider connect
   - The agent connects to Gemini or Qwen after accepting the room job.
   - Provider-specific issues can block greeting, transcription, VAD, or audio response even when LiveKit says connected.
   - Qwen has special compatibility and skips greeting due to observed state corruption risk.

5. Result delivery
   - Fast core results can return through `work()` directly.
   - Slow results are picked up by watcher and spoken later.
   - If the phone has left, the watcher still treats the result as delivered and speaks into an empty or agent-only room.

### Backgrounding and Phone Call Walkthrough

When the user switches away from the iPhone app:

```text
App enters background
  -> iOS may keep audio alive because UIBackgroundModes includes audio
  -> networking and WebRTC may remain healthy, reconnect, or degrade
  -> app has no didChangeAppLifecycleState handling for pause/resume
  -> app status may keep showing the previous state when resumed
```

When the user receives a system phone call:

```text
Incoming call starts
  -> iOS AVAudioSession interruption begins
  -> mic and speaker route can be paused, revoked, or moved
  -> LiveKit transport may remain connected or later reconnect
  -> realtime provider may stop receiving useful audio
  -> app has no explicit interruption state
Incoming call ends
  -> audio session may need reactivation, mic track restart, or full room reconnect
  -> app currently has no explicit recovery sequence
```

This is inherently unreliable on mobile WebRTC unless the app treats lifecycle and audio interruption as first-class states.

### Manual Leave Walkthrough

```text
User taps leave
  -> phone calls room.disconnect()
  -> phone navigates back
  -> LiveKit emits phone-user participant_disconnected
  -> agent logs the disconnect
  -> AgentSession remains open because close_on_disconnect=False
  -> Gemini/Qwen websocket may remain open
  -> result watcher continues running
  -> sutando-core is not notified
  -> any pending core result can later be consumed and spoken into a room with no phone listener
```

This behavior is coherent only if "leave" means "temporarily leave the room but keep my assistant session alive." It is surprising if the product meaning of leave is "I am done with this session."

### Rejoin Walkthrough

```text
User taps Connect again
  -> app fetches a new token
  -> token server checks whether an agent participant already exists
  -> if agent exists and is not stale, no new dispatch is created
  -> phone joins same configured room as phone-user
  -> existing agent session may link to phone-user again
```

This can feel like session continuity. However, because no explicit product session id exists, continuity depends on the existing worker/realtime session still being alive. If the worker, LiveKit room, or provider session died during the gap, the rejoin becomes a fresh session.

### Unstable, Unreliable, or Broken Areas

| Area | Risk | Why it matters |
| --- | --- | --- |
| Product session semantics | Ambiguous | Manual leave, transient disconnect, background, and phone-call interruption all collapse into similar transport events. |
| Offline result delivery | Broken for user-visible reliability | Results are consumed and archived before the system knows the phone heard them. |
| Status accuracy | Unreliable | UI lacks reconnecting/reconnected/audio-interrupted/agent-ready/result-pending states. |
| iOS background behavior | Inherently unstable | `audio` background mode helps but does not guarantee WebRTC, mic, route, or provider health. |
| Phone-call interruption recovery | Likely incomplete | No explicit AVAudioSession interruption handling or mic track recovery path is visible. |
| Agent dispatch readiness | Race-prone | Phone may be connected before agent and realtime provider are ready. |
| Realtime LLM lifetime | Potentially wasteful/orphaned | Manual phone leave can keep Gemini/Qwen open and result watcher active. |
| Rejoin continuity | Accidental | Rejoin may continue only if the same worker session survived; otherwise it silently becomes new. |
| Token refresh/reconnect | Limited in app | Native app currently fetches token before entering room; no visible in-room fresh-token reconnect path. |
| Core task handoff while phone offline | Incomplete | Core can finish tasks with no active listener; no durable acknowledgement protocol exists. |
| Observability | Partial | Logs and pipeline events exist, but there is no single lifecycle state machine that records user-visible delivery state. |

### Expected Product Behavior

For a personal assistant mobile room, a better mental model is:

- Short background or network blip: treat as temporary disconnect; auto-recover and preserve session.
- Incoming phone call: mark audio interrupted; pause/recover audio, then reconnect if needed.
- Manual leave: either explicitly end the product session, or explicitly keep it warm with pending result delivery.
- Slow task result while phone offline: store as pending and deliver on next foreground/join, or notify outside LiveKit.
- Rejoin: bind to a durable product session id when the user expects continuity.

## Possible Future Improvement

### Define Product Session State

Add an explicit product session id and lifecycle separate from LiveKit room name:

- `session_id`: created when user starts a room/conversation.
- `transport_state`: disconnected, connecting, connected, reconnecting.
- `audio_state`: unavailable, interrupted, active, muted.
- `agent_state`: dispatching, ready, failed, idle, speaking, waiting_for_core.
- `delivery_state`: no_pending_result, pending_result, delivered, missed, acknowledged.
- `ended_by_user`: true only when the user explicitly ends the session.

This lets the system distinguish "temporary phone disappeared" from "user ended the session."

### Clarify Leave Button Semantics

Choose one product meaning:

1. End session
   - Phone sends an explicit end command before or during `room.disconnect()`.
   - Agent calls `session.shutdown()` or `ctx.shutdown(...)`.
   - Realtime LLM websocket closes.
   - Pending tasks are either cancelled, completed silently, or written to another channel.

2. Leave but keep warm
   - UI labels it as "Leave room" or "Background session."
   - Agent remains alive for a bounded TTL.
   - Results are queued until the phone rejoins.
   - User sees pending results on return.

For this product, "End" and "Leave temporarily" should probably be separate actions.

### Add Durable Result Delivery

Before archiving a result as delivered, check whether `phone-user` is present and active. If not, write it to a durable pending delivery store, for example:

```text
state/mobile-sessions/{session_id}/pending-results.jsonl
```

Then on phone join/rejoin:

- App or agent marks `phone-user` active.
- Agent replays pending text summary, not raw generated audio.
- Result is acknowledged only after the phone is connected and the reply generation succeeds.
- Optional: phone UI can display pending text even if voice playback fails.

### Add Reconnect and Lifecycle State in the App

Extend the phone controller to listen to LiveKit reconnect events and app lifecycle:

- `RoomReconnectingEvent`: show "Reconnecting..."
- `RoomReconnectedEvent`: show "Connected"
- `RoomDisconnectedEvent`: decide whether to auto-reconnect or require user action.
- `AppLifecycleState.paused`: mark app backgrounded.
- `AppLifecycleState.resumed`: verify room, mic, participants, and agent readiness.
- iOS audio interruption: mark "Audio interrupted" and recover mic/audio route when ended.

The status should reflect user-actionable state, not just the last successful `room.connect()`.

### Add Agent Readiness Handshake

Expose an agent readiness signal to the phone:

- Token server or room state endpoint returns whether an agent participant exists.
- Agent sets a participant attribute like `agent_state=ready`.
- Phone displays "Agent starting..." until ready.
- User speech before readiness can be blocked, queued locally, or shown as "connecting assistant."

This removes the race where the phone is connected but the agent/realtime provider is not ready.

### Bound Warm Session Lifetime

If `close_on_disconnect=False` remains, add a timeout policy:

```text
phone-user disconnected
  -> keep session alive for N minutes
  -> if phone-user rejoins, continue
  -> if not, close realtime provider and stop watcher
  -> keep text results in pending delivery store
```

This preserves useful rejoin behavior without leaving Gemini/Qwen and result watchers alive indefinitely.

### Add Delivery Acknowledgement

Treat "result file read" and "user received result" as different events:

- `result_observed`: agent saw result file.
- `result_queued`: phone offline or audio unavailable.
- `result_spoken`: `generate_reply` completed while phone present.
- `result_acknowledged`: phone app confirmed it was online/subscribed, or user opened/read text fallback.

Only archive or mark complete after `result_spoken` or `result_acknowledged`, depending on desired strictness.

### Add Text Fallback for Missed Voice

Because voice is realtime and lossy by design, add a text-visible result surface:

- In-room transcript/result panel.
- Push/local notification if app backgrounded.
- "Recent results" list on connect screen.
- Optional result summary file under `results/proactive-*` or a mobile-specific inbox.

This avoids relying on a voice packet stream for durable task completion.

### Improve Observability

Add structured lifecycle events for:

- phone joined
- phone left
- app backgrounded/resumed
- audio interrupted/resumed
- agent dispatched
- agent ready
- realtime connected/disconnected
- core task queued
- core result ready
- result queued/spoken/acknowledged/missed
- session ended by user/timeout/error

These events should share `session_id`, `room`, `username`, `task_id`, and `phone_present` fields so regressions can be traced from one timeline.

### Recommended Near-Term Order

1. Add explicit status states in the phone app for reconnecting, disconnected, and agent starting.
2. Add phone presence check before watched results are spoken.
3. Add pending-result persistence and replay on rejoin.
4. Split "Leave" from "End session" semantics.
5. Add iOS lifecycle/audio interruption recovery.
6. Add bounded warm-session timeout for `close_on_disconnect=False`.
