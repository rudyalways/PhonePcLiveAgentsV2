---
name: startup
description: "Single entry point for fresh-session bootstrap. Runs optional task-orphan recovery, cron registration, and watcher start in a fixed order. Replaces the current `claude -- '/schedule-crons'` invocation pattern as the canonical CLI startup target."
user-invocable: true
---

# Startup

The canonical entry point for a fresh Sutando session. Bundles every action that must happen once at session start, in the correct order.

**Usage**: `/startup`

ARGUMENTS: $ARGUMENTS (currently unused — reserved for future per-instance overrides)

## What this replaces

Previously: `claude -- "/schedule-crons"` was the de-facto startup invocation, and `skills/schedule-crons/SKILL.md` accumulated startup ceremony (cron-fallback, watcher) on top of its actual job (registering crons from `crons.json`).

Now: `claude -- "/startup"` is the canonical startup target. `/startup` orchestrates the sequence; `/schedule-crons` shrinks back to its narrow job.

Migration: update `~/Library/LaunchAgents/*.plist` and any CLI invocation scripts to call `/startup` instead of `/schedule-crons`. `/schedule-crons` still works standalone (for manual cron re-registration) — both paths are idempotent.

## Why one bundled skill

Per Chi 2026-05-23 Discord: "we can make a new skill and include everything we need at start." Five rationales:

1. **Single entry point** — no more "which skill does the CLI invoke?" The launchd plist points at `/startup` and only at `/startup`.
2. **Ordering encoded in one place** — the sequence (recover state → register schedules → start watcher) lives in this skill's `On Activation` section, not scattered across schedule-crons's step list.
3. **Easy to extend** — future startup work (new lifecycle checks, telemetry pings, dependency probes) appends to this skill's sequence; no debate about where it belongs.
4. **Each sub-step stays callable standalone** — `/task-orphan-check`, `/schedule-crons`, etc. continue to work for manual invocation. `/startup` is a wrapper, not a replacement.
5. **Idempotent re-invocation** — calling `/startup` twice in the same session is safe; each sub-skill is idempotent (registering an already-scheduled cron is a no-op, an already-running watcher isn't restarted, etc.).

## On Activation

The sequence below MUST run in this order. Each step is naturally idempotent, so re-invocation is safe.

**Hard rule — owner/omni work beats boot ceremony.** If `<workspace>/tasks/task-*.txt` exists at any point during `/startup` (including while `/schedule-crons` is mid-run), **stop cron registration**, process those tasks (oldest first: do the work, write matching `results/<basename>`), then resume. Never say "I'll finish schedule-crons first" while owner tasks wait — that is the 2026-08-06 omni stall class (core `.alive` but work stuck for minutes). Queued `TASK_FILE:` injections from the tmux feeder count as the same obligation.

**Policy — task arrives while still “not ready”:**

1. The task file on disk is the source of truth. Do **not** drop it; do **not** wait for the feeder.
2. `/startup` Step 1 (or this hard rule mid-ceremony) **must** process it from `tasks/`.
3. **Before** starting that owner work, run `mark-ready` so omni HUD / feeder stop showing BOOTING/BLOCKED while you are already executing the task. Ceremony (orphan-check / schedule-crons) continues **after** owner tasks drain.
4. If there are **no** owner tasks yet, keep `core-booting.json` through cron ceremony and only `mark-ready` at Step 4 — that still prevents the feeder from spamming injects during schedule-crons.

### Step 0 — Boot readiness sentinel

`start-cli.sh` already wrote `<workspace>/state/core-booting.json`.

- **Owner tasks present (Step 1 / hard rule):** call `mark-ready` immediately, then process tasks.
- **No owner tasks:** leave booting until Step 4 (after ceremony).

### Step 1 — Pending owner tasks (before orphan-check / crons)

```bash
WS="$(bash scripts/sutando-config.sh workspace)"
ls -1tr "$WS/tasks"/task-*.txt 2>/dev/null
```

If any files are listed:

```bash
python3 src/core_readiness.py mark-ready --source startup-owner-tasks
```

Then process **all** of them now (oldest first). Only after `tasks/` has no remaining `task-*.txt` continue to Step 2. If none, continue immediately (stay booting until Step 4).

### Step 2 — Task orphan check (optional)

Invoke `/task-orphan-check` IF the skill is installed (i.e. `$CLAUDE_CONFIG_DIR/skills/task-orphan-check/` exists). This is the recovery half of the post-#1049 redesign: scan `<workspace>/tasks/` for orphan tasks left over from a crash mid-execution, cross-reference per-side-effect markers (e.g. PR #1048's `.sending` files), archive completed tasks, write recovery sentinels for stuck ones. See the skill itself for the full procedure.

If the skill is not installed, skip silently. `/startup` works without it — every other step is independent.

Note: this step runs BEFORE schedule registration so the watcher (started early inside `/schedule-crons`) doesn't pick up an orphan task before recovery has classified it. If orphan-check leaves fresh `task-*.txt` for the watcher, process those before Step 3's slow cron work if `/schedule-crons` has not yet started the watcher.

### Step 3 — Register schedules + start watcher

Invoke `/schedule-crons`. That skill **starts the streaming watcher before CronCreate** and itself yields to pending `task-*.txt`. It also handles:
- Reading host `crons.json`
- Calling `CronCreate` for each entry that isn't already scheduled
- Ensuring a fallback `/proactive-loop` cron exists at `*/10 * * * *` if `crons.json` doesn't include one (post-#954 belt-and-suspenders) — **skipped entirely** when `python3 skills/proactive-loop/scripts/proactive-loop-enabled.py` prints `disabled` (`SUTANDO_PROACTIVE_LOOP_ENABLED=0`)
- **Boot session-recap** — **skipped by default** (`SUTANDO_SESSION_RECAP_ON_BOOT=0`). When enabled, schedule-crons `mark-ready`s **before** the transcript dump so omni is not stuck WAITING. See schedule-crons step 5.6.

### Step 4 — Mark ready + confirm

```bash
python3 src/core_readiness.py mark-ready --source startup
```

Emit a one-line summary so the operator (or main session's first turn) sees what fired:

```
/startup complete: orphan-check (N tasks recovered, M archived), schedules (K crons + watcher), core-ready.
```

The orphan-check fields say `skipped (skill not installed)` if step 2 was skipped.

## Sequence diagram

```
session start
    │
    ▼
/startup
    │
    ├─► step 1:  pending task-*.txt?
    │              yes ──► mark-ready ──► process owner/omni work FIRST
    │              no  ──► stay booting
    │
    ├─► step 2:  /task-orphan-check (optional) ──► classifies + archives orphan tasks
    │
    ├─► step 3:  /schedule-crons ──┬─► yield + start watcher FIRST
    │                               ├─► register crons.json + proactive fallback
    │                               └─► stamp + confirm
    │
    └─► step 4: mark-ready (idempotent) + emit summary
```

## Re-invoking in an already-running session

If `/startup` is invoked mid-session, the sub-skills skip their already-done work (an already-scheduled cron isn't re-created, an already-running watcher isn't restarted), so the result is effectively a re-confirm of state. Safe.

## What lives elsewhere

This skill is intentionally a thin orchestrator. Logic lives in the sub-skills:

- **Orphan recovery**: `skills/task-orphan-check/` (separate PR, optional)
- **Cron registration + watcher start**: `skills/schedule-crons/`

If you find yourself wanting to put logic IN `/startup`, ask whether it belongs in one of the sub-skills (or a new sub-skill) first. `/startup` is the order, not the work.

## Iteration log

- v0.1.0 — 2026-05-23 — initial draft. Per Chi 2026-05-23 Discord exchange about #1049 redesign ("make a new skill and include everything we need at start"). `/startup` becomes the canonical CLI entry; `/schedule-crons` remains callable for manual cron re-registration. Migration: launchd plists + CLI scripts switch to `/startup`.
- v0.2.0 — 2026-06-21 — removed the fresh-session briefing step and its session sentinel (that sub-skill was deleted). `/startup` now runs orphan-check → schedules + watcher → confirm; sub-skill idempotency replaces the former sentinel guard.
- v0.3.0 — 2026-08-06 — yield to pending `tasks/task-*.txt` before cron ceremony; mark `core-ready` via `src/core_readiness.py` so omni HUD can distinguish alive vs ready (fixes boot-stall where feeder injected while schedule-crons ran for minutes).
- v0.4.0 — 2026-08-07 — if owner tasks exist at Step 1 / mid-ceremony, `mark-ready` **before** processing them so HUD/feeder don't stay BOOTING for the whole research job; no-task boots still mark-ready only at Step 4.
- v0.4.1 — 2026-08-07 — boot `session-recap` default OFF (`SUTANDO_SESSION_RECAP_ON_BOOT`); when on, mark-ready before dump (omni stall class).
