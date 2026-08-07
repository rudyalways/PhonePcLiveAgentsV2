# Session Recap

Reconstruct what happened in a past core session — from a high-level summary down to verbatim owner quotes — by reading the raw session transcripts (complete, crash-proof, unbiased), not the curated relay/handoff notes.

**Usage**: `/session-recap [last | <session-uuid-prefix> | list] [detail hint]`

## Why transcripts, not notes (owner design, 2026-07-13)

1. Relay/handoff notes are short and reflect my curation — bias by construction.
2. Notes need a graceful exit; the transcript JSONL is appended by the harness live, so accidental restarts lose nothing.
3. Only the transcript has *verbatim* detail ("what did the owner say exactly").

Notes remain useful as a fast index into a long session — nothing more.

## How to run it

1. **Bound the session.** `python3 skills/session-recap/scripts/extract.py list` prints recent sessions (newest first: file uuid, start/end ISO, message counts, first user line). Cross-check with `<workspace>/state/session-starts.log` (one JSONL line per core boot; consecutive entries bound a session). "last" = the second-newest transcript (newest = the running session).
2. **Extract.** `python3 skills/session-recap/scripts/extract.py dump --session last --filter dialog --max-chars 0` → chronological `[ts] USER:/ASSISTANT:` stream. `--filter user` for owner messages only (verbatim-quote lookups: just grep this). `--filter all` adds tool-call names + system lines when the recap needs to cover actions, not just conversation. `--max-chars 0` = no cap.
3. **Summarize with a CHEAP model** (owner requirement — transcripts run to tens of MB; never burn core-model quota on this). Spawn an Agent-tool subagent with `model: haiku`, hand it the dump (or the dump file path if huge — have it Read in slices), and ask for: timeline, tasks processed + outcomes, PRs/commits, decisions, errors + fixes, **artifacts (files/notes/memories written — the dump's `Write(path)`/`Edit(path)` tool lines carry the paths; owner requirement 2026-07-13)**, loose ends. Match the detail level the owner asked for. **Drop routine operational noise** (owner rule 2026-07-13): battery-escalation ladders, idle proactive-loop passes, quota checks, watcher restarts, memory syncs, health-check green runs — none of it belongs in a recap unless it materially changed the session's course (e.g. quota exhaustion forced a pivot, a crash lost work).
4. **Deliver** to the asking channel. For a verbatim-quote request, skip the subagent entirely — grep the `--filter user` dump and quote directly.

## Required summary structure (owner requirement 2026-07-13)

Any work-summary the recap produces — boot catchup, human brief, or an on-demand "summarize what I did" — MUST cover these sections (scale depth to the request, but never drop a section that applies):

1. **Executive summary** (lead with it): the **main initiative(s)**, the **high-level goal**, and the **important decisions — especially architecture/design decisions** (the *why*, not just the *what*). One-line status at the end.
2. **Detailed body**: per-item technical detail — PRs with what each did + why + hazards hit, decisions + rationale, errors + their fixes, **artifacts written** (files/notes/memories — from the transcript's `Write`/`Edit` tool lines), loose ends. When the owner asks for "more detail," expand this — specific findings, exact fixes, reproductions, CI/tooling mechanics — not just more headlines.
3. **Roadmap relationship** (when the work maps to `roadmap/ROADMAP.md`): which track/lane it advances and how (e.g. "closes Track-14 gap (a)"), plus a pointer to the relevant plan doc.
4. **Recommended next actions**: prioritized, naming the **owner-gated blockers with exact commands** (what unblocks the biggest thing first), then the follow-on work.

Save durable work-summaries under `<workspace>/notes/work-summaries/YYYY-MM-DD.md` (owner-created folder 2026-07-13), one file per summary, each opening with a `*[workflow, summary] — author | window | requested-by*` line.

## Automatic recap on restart (owner directive 2026-07-13; fast path 2026-08-07)

**Primary consumer: the next session's agent** (owner 2026-07-13). Boot catchup must not block omni for minutes.

**Boot = fast mechanical extract (default ON).** `/schedule-crons` step 5.6 runs:

```bash
python3 skills/session-recap/scripts/boot-recap.py
```

That script `mark-ready`s first, dumps a **capped dialog** extract (default 48k chars, `SUTANDO_SESSION_RECAP_BOOT_MAX_CHARS`), and writes `<workspace>/state/last-session-recap.md` + stamp — **no LLM / no haiku subagent**. Yields (exit 2) if `tasks/task-*.txt` are pending. Set `SUTANDO_SESSION_RECAP_ON_BOOT=0` to skip boot catchup entirely.

**Deep LLM recap stays on-demand** via `/session-recap` (this skill's full procedure below) — not during boot.

**Human room post (optional):** if `recap_room` is set in `<workspace>/hosts/<hostname>/recap.json` and is private/owner-only, `/schedule-crons` may post a short pointer that boot-fast recap is on disk. Never point `recap_room` at a shared/team room.

## Notes

- Read-only over transcripts; never edit or move them.
- Transcript dir: `<workspace>/.claude-sutando/projects/<repo-slug>/*.jsonl` (the script resolves it).
- A session that spans compaction stays ONE file; a restart starts a new file — so file boundaries ARE session boundaries.
- Subagent (sidechain) transcripts can appear in the same dir as small files; the `list` table's message counts make them easy to spot and skip.
