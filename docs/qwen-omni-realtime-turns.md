# Qwen-Omni-Realtime: how turns work

Source: [Alibaba Cloud Model Studio — Qwen-Omni-Realtime](https://www.alibabacloud.com/help/en/model-studio/realtime)

Agent design / decision log: [omni-exp-agent-design.md](./omni-exp-agent-design.md)

## Mental model

Omni Realtime is **turn-based conversation** with **streaming media transport** — not “send a batch every N seconds.”

```text
mic/camera ──append──► server buffer ──[turn trigger]──► model generate ──delta──► client
                         (accumulates)     VAD or commit      one response
```

| Layer | Streams continuously | Discrete unit |
| --- | --- | --- |
| Transport | audio chunks, image frames | — |
| Conversation | response text/audio deltas | one user turn → one assistant response |

`append` only fills a buffer. The model does **not** answer on every chunk. A **turn boundary** means: “this utterance (plus recent frames) is complete — generate now.”

## What streams in?

| Input | Required? | How |
| --- | --- | --- |
| **Audio** | Yes | PCM 16 kHz via `input_audio_buffer.append` (WebSocket) or RTP (WebRTC) |
| **Images / video frames** | Optional | `input_image_buffer.append` or WebRTC video track; ~**1 fps** recommended |
| **Text as live input** | No | Not the primary path. Text is mainly **output** (+ optional ASR of user audio). Session `instructions` are config, not a per-frame text stream. |

## Two turn modes

### VAD mode (`server_vad` / `semantic_vad`)

For voice-call scenarios. WebSocket and WebRTC both support this (WebRTC is VAD-only).

1. Client keeps appending audio (and optional frames).
2. Server: `speech_started` → … → `speech_stopped` (after `silence_duration_ms`, e.g. 800ms).
3. Server auto-commits and starts a response.
4. Client receives streaming `response.*.delta` until `response.done`.

Prefer `semantic_vad` on qwen3.5-omni-realtime (better at ignoring backchannels / noise).

#### Is `silence_duration_ms: 800` a time-based trigger?

**Yes, but only as end-of-utterance after speech — not a periodic timer.**

```text
[silence…]  — no speech_started → nothing (800ms does nothing by itself)
[user talking…]
[silence ≥ silence_duration_ms] → speech_stopped → commit → model responds
```

| What people might think | What it actually is |
| --- | --- |
| “Every 800ms, run the model” | **No** |
| “If the line is quiet for 800ms anytime, respond” | **No** — needs speech activity first |
| “After the user was speaking, 800ms of silence means they’re done; respond” | **Yes** |

So it is **time-based silence trailing a detected utterance**, gated by VAD. `threshold` controls how easily speech is detected; `semantic_vad` additionally tries not to treat backchannels/noise as real turns.

### Manual mode (`turn_detection: null`)

Push-to-talk / voice-message style. WebSocket only.

1. Append audio/images while “talking.”
2. Client: `input_audio_buffer.commit` then `response.create`.
3. Model streams one response.

## Q: VAD, but silent all the time?

**Nothing happens.** No speech → no `speech_started` → no commit → no model response.

Frames may keep appending into the buffer, but **images alone do not start a turn**. Generation waits for end-of-speech (VAD) or an explicit manual commit.

## Q: Turn based on one stream, some streams, or all streams?

**Only audio owns the turn boundary.**

| Stream | Starts a turn? | Role |
| --- | --- | --- |
| **Audio** | Yes (VAD end-of-speech, or manual `commit`) | Turn clock |
| **Images / video frames** | No | Extra context attached to the next audio turn |
| **Text** | No | Mostly output / ASR |

Not “all streams must finish.” Not “any stream can trigger.” Other modalities **ride along** when the audio turn closes.

Manual-mode note from docs: send at least one `input_audio_buffer.append` before any `input_image_buffer.append`.

## Related edge cases

**Timer every 1s?** Wrong unit. Don’t poll Omni like a batch vision API — you get overlapping turns, cut-off speech, wasted tokens. Use VAD (calls) or manual commit (PTT).

**User speaks while response still streaming?** Barge-in: on `speech_started` while responding, client typically `response.cancel` + stop local playback, then the new utterance becomes the next turn.

**Outputs:** streaming text and/or audio (`modalities: ["text"]` or `["text","audio"]`), via WebSocket deltas or WebRTC RTP + DataChannel events.
