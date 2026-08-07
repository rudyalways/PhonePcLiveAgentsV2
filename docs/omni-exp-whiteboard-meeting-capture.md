# Omni-exp: whiteboard / meeting-room capture

**Status: implemented** (MVP + Phase 2) — `src/omni_exp_research_capture.py`, `src/omni_exp_scene.py` (`BoardInkSensor` + face mask), wired in `src/omni-exp-agent.py`; stamps in `src/omni_exp_mode.py`.

Extends `OMNI_EXP_MODE=research` for meeting rooms: capture **discussion topics, todos, research follow-ups, and running summary** from **speech + whiteboard**, without relying on full-frame pixel MAD alone.

Related: [`omni-exp-research-mode.md`](./omni-exp-research-mode.md), sensor: `src/omni_exp_scene.py`.

## Problem

Thumb-MAD scene change fires on people walking / lighting, and **misses** ink + talk when the camera is mostly static. Whiteboard meetings need **audio-first** capture and **session memory**, with optional board text later.

## Goals

| Capture | Examples |
|---|---|
| Discussion topics | “we’re deciding on auth…” |
| Todos / action items | “I’ll send the deck”, “待办：改 API” |
| Research topics / follow-ups | “look into SOTA for…”, “研究一下竞品” |
| Running summary | short bullets of what was agreed |

Non-goals: perfect handwriting OCR, speaker diarization, always-on deep-research decks every scribble. OCR is best-effort via system `tesseract` when installed.

## Architecture

```text
mic ASR (final utterances)
        │
        ▼
 cue detector ──► append hooks ──► ResearchSessionBuffer ◄── persist state/
                                        ▲
scene_change (face-masked MAD) ─ note ──┤
board ink delta (+ optional OCR) ───────┤
meeting_scan heartbeat ─────────────────┘
                                        │
                    flush policy ───────┼──► work [research-capture-flush]
                                        └──► work [research-deep] (rare)
```

**Split of responsibility**

| Layer | Role |
|---|---|
| Omni agent | Buffer ASR + scene/ink notes; detect cues; flush to `work`; persist buffer |
| Qwen | Answers camera questions; prefers accumulate; deep only when asked |
| sutando-core stamp | `capture-flush` → notes MD + short result; `deep` → full research + Chinese deck |

## Implemented

### 1. Research session buffer

In-process buffer on the omni phone session (`OMNI_EXP_MODE=research` only):

- Ring of recent **user ASR finals** (text + ts)
- Structured hooks: `topics`, `todos`, `research`, `notes`, `summary`
- Cap size (40 ASR lines, 30 hooks)
- **Persisted** across WS reconnects: `<workspace>/state/omni-research-capture-<user>.json`

### 2. Audio cue detector

On `conversation.item.input_audio_transcription.completed`, scan for EN/ZH cues, e.g.:

- todos: `todo`, `action item`, `follow up`, `next step`, `待办`, `跟进`, `下一步`, `行动项`
- research: `research`, `look into`, `investigate`, `研究`, `查一下`, `竞品`
- summary: `summary`, `summarize`, `recap`, `总结`, `回顾`

On hit: append hook + consider **capture flush** (debounced).

### 3. Flush policy

| Kind | When | Core does |
|---|---|---|
| `capture-flush` | Cue hit (debounced), idle ≥ N s with ≥2 hooks, or ≥ M new hooks | Write/update `workspace/data/omni-research/meeting-<date>.md`; short spoken result. **No** full HTML deck. |
| `deep` | Explicit research ask / `[research-deep]` / deck request | Research pipeline (MD + Chinese auto-play deck) |

Debounce: min interval between capture flushes (default 45s) unless explicit deep ask.

### 4. Scene retune (research only)

- Lower default MAD threshold (22 when `OMNI_EXP_SCENE_THRESHOLD` unset)
- **`mask_upper_fraction`** (default 0.35) ignores upper face/walker band for scene MAD
- Scene fire still records a short scene note; cooldown backoff unchanged
- Audio remains primary; full-frame MAD is a weak backup

### 5. Prompts / stamps

- `[research-capture-flush]` → capture-only core stamp
- `[research-deep]` / `[research-mode]` → full deck stamp (human-friendly light theme)
- Omni research voice: accumulate; don’t demand a deck every utterance

### 6. Board ink + OCR (opt-in: `RESEARCH_BOARD_INK=1`)

`BoardInkSensor` watches the **lower** frame (same upper mask) with edge-enhanced MAD. On stabilized ink change → buffer note. OCR only if `RESEARCH_BOARD_OCR=1` and `tesseract` is on PATH (fail-open).

### 7. Meeting-scan heartbeat (opt-in: `RESEARCH_MEETING_SCAN_S>0`)

When enabled: re-check board ink on latest frame, note the scan if there is unflushed content, run flush policy.

## Config (env)

`OMNI_EXP_MODE=research` is the master switch (buffer + ASR cues + flush). Phase-2 extras are **separate env flags** — default off except persist:

| Flag | Default | What |
|---|---|---|
| `OMNI_EXP_RESEARCH_PERSIST` | `1` | Save/load buffer under `state/omni-research-capture-<user>.json` |
| `OMNI_EXP_RESEARCH_BOARD_INK` | `0` | Lower-frame ink delta sensor |
| `OMNI_EXP_RESEARCH_BOARD_OCR` | `0` | OCR on ink fire (needs ink=1 + `tesseract`; fail-open) |
| `OMNI_EXP_RESEARCH_MEETING_SCAN_S` | `0` | Heartbeat seconds; `0` disables |
| `OMNI_EXP_RESEARCH_MASK_UPPER` | `0.35` | Face/walker band ignored for scene MAD; `0` = full frame |

```bash
OMNI_EXP_MODE=research
OMNI_EXP_SCENE_CHANGE=1
# OMNI_EXP_RESEARCH_FLUSH_IDLE_S=90
# OMNI_EXP_RESEARCH_FLUSH_MIN_INTERVAL_S=45
# OMNI_EXP_RESEARCH_FLUSH_HOOK_COUNT=5
# OMNI_EXP_RESEARCH_SCENE_THRESHOLD=22
# OMNI_EXP_RESEARCH_MASK_UPPER=0.35
# OMNI_EXP_RESEARCH_PERSIST=1
# Opt-in Phase 2:
# OMNI_EXP_RESEARCH_BOARD_INK=1
# OMNI_EXP_RESEARCH_BOARD_OCR=1
# OMNI_EXP_RESEARCH_MEETING_SCAN_S=120
```

## Success criteria

1. Saying “待办：下周发方案” produces a capture flush into meeting notes without opening Chrome.
2. Static camera + spoken research ask still flushes (audio path).
3. Deep deck only when explicitly requested or `[research-deep]`.
4. People walking across frame do not alone trigger a deep research deck (upper mask + no deck on capture-flush).
5. Whiteboard ink change (lower frame) adds a buffer note; reconnect restores the buffer from workspace state.
