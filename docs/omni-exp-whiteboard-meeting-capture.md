# Omni-exp: whiteboard / meeting-room capture

**Status: MVP implemented** (`src/omni_exp_research_capture.py`, wired in `src/omni-exp-agent.py`; stamps in `src/omni_exp_mode.py`).

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

Non-goals (v1): perfect handwriting OCR, speaker diarization, always-on deep-research decks every scribble.

## Architecture

```text
mic ASR (final utterances)
        │
        ▼
 cue detector ──► append hooks ──► ResearchSessionBuffer
                                        ▲
scene_change (retuned) ── vision note ──┘
board OCR / ink (phase 2) ─────────────┘
                                        │
                    flush policy ───────┼──► work [research-capture-flush]
                                        └──► work [research-deep] (rare)
```

**Split of responsibility**

| Layer | Role |
|---|---|
| Omni agent | Buffer ASR + scene notes; detect cues; flush to `work` |
| Qwen | Still answers camera questions; research prompt encourages capture phrasing |
| sutando-core stamp | `capture-flush` → notes MD + short result; `deep` → full research + Chinese deck |

## MVP (implement now)

### 1. Research session buffer

In-process buffer on the omni phone session (`OMNI_EXP_MODE=research` only):

- Ring of recent **user ASR finals** (text + ts)
- Structured hooks: `topics`, `todos`, `research`, `notes`, `summary_bullets`
- Cap size (e.g. 40 ASR lines, 30 hooks)

### 2. Audio cue detector

On `conversation.item.input_audio_transcription.completed`, scan for EN/ZH cues, e.g.:

- todos: `todo`, `action item`, `follow up`, `next step`, `待办`, `跟进`, `下一步`, `行动项`
- research: `research`, `look into`, `investigate`, `研究`, `查一下`, `竞品`
- summary: `summary`, `summarize`, `recap`, `总结`, `回顾`

On hit: append hook + consider **capture flush** (debounced).

### 3. Flush policy

| Kind | When | Core does |
|---|---|---|
| `capture-flush` | Cue hit (debounced), or buffer idle ≥ N s with new content, or ≥ M new hooks | Write/update `workspace/data/omni-research/meeting-<date>.md` with topics/todos/research/follow-ups/summary; short spoken result. **No** full HTML deck. |
| `deep` | Explicit research ask, or task tagged `[research-deep]`, or user/Qwen requests deck | Existing research pipeline (MD + Chinese auto-play deck) |

Debounce: min interval between capture flushes (e.g. 45s) unless explicit deep ask.

### 4. Scene retune (research only)

When mode is `research`:

- Slightly **lower** `enter_threshold` (more sensitive to board pans) — env override kept
- Prefer not treating every NO_SPEAK as failure; keep cooldown backoff
- Scene prompt already asks for research `work`; buffer also records a short “scene note” when scene fires (for flush context)

Full-frame MAD remains a **weak** backup; audio is primary.

### 5. Prompts / stamps

- Task tag `[research-capture-flush]` → capture-only core stamp
- Task tag `[research-deep]` / normal research capture from Qwen → full deck stamp
- Omni research voice: prefer accumulating; don’t demand a deck every utterance

## Phase 2 (not MVP)

- Board-crop OCR / ink delta (fire on text change)
- Ignore upper face region for MAD
- Periodic “meeting scan” heartbeat
- Persist buffer across WS reconnects under workspace state

## Config (env)

```bash
OMNI_EXP_MODE=research
OMNI_EXP_SCENE_CHANGE=1
# Optional MVP knobs:
# OMNI_EXP_RESEARCH_FLUSH_IDLE_S=90
# OMNI_EXP_RESEARCH_FLUSH_MIN_INTERVAL_S=45
# OMNI_EXP_RESEARCH_SCENE_THRESHOLD=22   # lower than default 28 when set
```

## Success criteria

1. Saying “待办：下周发方案” produces a capture flush into meeting notes without opening Chrome.
2. Static camera + spoken research ask still flushes (audio path).
3. Deep deck only when explicitly requested or `[research-deep]`.
4. People walking across frame do not alone trigger a deep research deck.
