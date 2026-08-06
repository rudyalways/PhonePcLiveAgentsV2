# Omni-exp mode: `research`

Env: `OMNI_EXP_MODE=research`  
Aliases: `research_mode`, `scene_research`.

Meeting / whiteboard note capture (audio-first, no deck every utterance):
[`omni-exp-whiteboard-meeting-capture.md`](./omni-exp-whiteboard-meeting-capture.md).

**Implemented in code** (`src/omni_exp_mode.py` + `src/omni-exp-agent.py` + `src/omni_exp_research_capture.py`):

| Surface | What |
|---|---|
| Omni / Qwen `INSTRUCTIONS` | Research capture middle + scene override |
| Omni `SCENE_PROMPT` | Research-specific PromptTrigger (call `work` on researchable scenes) |
| Omni `work` tool description | Capture → MD → auto-play HTML deck |
| Task file `omni_exp_mode` + `[research-mode]` tag | Marks the job |
| Task stamp `===SUTANDO SYSTEM INSTRUCTIONS===` | Full sutando-core pipeline (source of truth) |
| Scene change | Forced **on** when mode is `research` |

## Intent

Turn phone camera + mic into a **research capture loop**: notice topics from the scene and speech, expand them into multi-angle research, and deliver a **PPT-like HTML deck** (one file; **one major topic per slide**, typically 3–8) with **auto-play** and **~10–20s auto-explanation** per slide. Draft the research narrative in Markdown first, then render HTML.

## Responsibility split (same as other modes)

| Layer | Role |
|---|---|
| Qwen system prompt | Thin: watch scene/audio, extract hooks, call `work` with a clear research brief; speak short status |
| `work` tool description | One-liner: research + MD + HTML deck |
| Task stamp → sutando-core | **Source of truth** for capture taxonomy, research directions, MD→HTML pipeline |
| `normal_with_gui` | No-op stamp (unchanged) |

## Capture (from scene + audio)

Monitor ongoing vision/audio and extract durable hooks into the `work` task:

- **Search topics** — names, products, papers, companies, jargon on screen or spoken
- **Research questions** — “what is…”, “who owns…”, “is this SOTA…”
- **Todos / follow-ups** — action items implied by speech or slides
- **Notes** — facts worth keeping
- **Meeting notes / summary** — if the scene looks like a meeting, talk, or whiteboard

Prefer one consolidated `work` call per coherent burst (not one task per word). Re-call when a **new** distinct topic appears.

Recommended companion env (not forced by mode code):

```bash
OMNI_EXP_SCENE_CHANGE=1
```

## Deep research directions (core)

For each primary topic, cover **multiple directions** (skip a lane only if truly N/A):

1. **SOTA research papers** — arXiv / Semantic Scholar / Google Scholar style lookup (CLI/fetch; no browser-search GUI unless mode is `normal_with_gui`)
2. **Hot / frontier startups** — recent companies, funding, product angle
3. **Big tech** — related blogs, product pages, engineering posts
4. **Community** — X/Twitter, Reddit, Hacker News, YC (Show HN / Launch YC) discussions
5. **Investor perspectives** — thesis, market size, competitive framing (public sources)

Use non-GUI fetch/CLI when `OMNI_EXP_MODE` is `research` (same search discipline as `no_gui_html_output` unless the operator switches to `normal_with_gui`).

## Deliverable pipeline (core)

Canonical prompt stamped on every research task: `RESEARCH_TASK_SYSTEM` in `src/omni_exp_mode.py` (switchable with `OMNI_EXP_MODE=research`).

1. **Markdown first** — `workspace/data/omni-research/research-<slug>-YYYYMMDD.md`
2. **HTML deck** — `workspace/data/omni-research/<slug>-deck.html` (spec below)
3. **Open** — `open <absolute-path-to-deck.html>`
4. **Result file** — short phone summary + paths

### HTML deck design (sutando-core must follow)

#### Language

**Simplified Chinese (简体中文) required** for all user-facing deck copy: titles, thesis, bullets, lane labels, follow-ups, narration, control labels (`播放/暂停`, `静音`). `html lang="zh-CN"`. Proper nouns may stay original. Markdown briefings should also be Chinese.

#### Slide topics (topic-paged; do not crush into 2)

| Slide | When | Content |
|---|---|---|
| **1 — 全景 / 论点** | Always | 中文标题（≤16字）, 一句论点, 3–5 要点, optional 来源标签 |
| **2…N-1 — one hook each** | Each major MD topic/chapter | Title + thesis + 3–5 bullets. Do not merge unrelated chapters. |
| **Last — 资源 / 行动** | Always if links/todos exist | Links + 2–4 待办; optional direction lanes |

Counter chrome: `主题 i/N` (or `主题 i/N · 页 a/b` when a topic spans two pages). Bare `2/2` is only valid when there are really two topics. Cap at 10 slides.

Narration: **10–20 seconds** spoken Chinese per slide (~45–90 汉字).

#### Format (PPT-like)

- Full-viewport slides (`100vw` × `100vh`); only the active slide visible
- Dark graphite `#12141a`, text `#f2f2f0`, accent `#3d9cf0`; PingFang SC / Microsoft YaHei stack
- Bottom bar: 讲解 + 播放/暂停 + 静音 + model status + slide counter + progress dots
- Deck CSS/UI inline; **allowed network use**: first-time download of in-browser Chinese TTS runtime + model weights (then cache locally)

#### Auto-explain — download model → run locally in the page

1. Chinese transcript per slide (`data-narration` + `#explain`).
2. **Primary:** HTML loads a browser TTS engine (e.g. onnxruntime-web / transformers.js / sherpa-onnx wasm) and a **Chinese** voice model. Show「正在下载语音模型…」, then cache in **Cache API / IndexedDB** so later opens reuse the local copy.
3. Synthesize + play narration **on-device** after `就绪`. Core should **not** need gemini-tts / openai-tts / `say` for the happy path.
4. **Fallback:** `speechSynthesis` `zh-CN` if model download/init fails.
5. Mute: transcript only; timer advance.

#### Auto-play — adapts to explanation time

| Situation | When to advance |
|---|---|
| Local model TTS / AudioBuffer | `onended` (progress = position/duration) |
| `speechSynthesis` fallback | `utterance.onend` |
| Mute / TTS unavailable | Timer ≈ `max(8s, 0.35s × chars/2)`, cap 20s |

Do **not** start advancing while the model is still downloading. No fixed 10s when TTS is speaking.

## Voice behavior (thin)

- Proactively surface: “I heard/saw X — researching that.”
- Do not claim the deck is done until `TASK_RESULT`.
- Keep spoken replies short; rich content lives in the HTML deck on the Mac.

## Out of scope (v1)

- Multi-hour continuous archival of every frame
- Guaranteed live X API access (use available CLI/web fetch; degrade gracefully)
- Stacking `OMNI_EXP_MODE` values (only one mode at a time; `research` is the default and already includes no-GUI search + HTML)
