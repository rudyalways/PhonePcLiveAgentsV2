#!/bin/bash
# Bridge task-file appearances into sutando-core tmux.
#
# Canonical path is Claude Code Monitor → watch-tasks-stream.sh. When Monitor
# is unavailable (e.g. OpenRouter / non-Anthropic cores), this feeder injects
# "TASK_FILE: …" prompts into the core tmux pane so work still progresses.
#
# Enable via SUTANDO_TMUX_TASK_FEEDER=1|auto (startup.sh). Poll loop so the
# process does not exit when fswatch dies. Tracks local "done" markers because
# omni may delete results/ files after speaking.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SOCK="${SUTANDO_TMUX_SOCKET:-/tmp/sutando-tmux.sock}"
SESSION="${SUTANDO_TMUX_SESSION:-sutando-core}"
WS="$(bash "$REPO/scripts/sutando-config.sh" workspace)"
TASKS="$WS/tasks"
RESULTS="$WS/results"
LOG="$WS/logs/omni-exp-watch-tasks-to-tmux.log"
STATE="$WS/state"
DONE_DIR="$STATE/omni-exp-watch-tasks-to-tmux.done"
PID_FILE="$STATE/omni-exp-watch-tasks-to-tmux.pid"
LOCK_DIR="$STATE/omni-exp-watch-tasks-to-tmux.lock"
POLL_S="${SUTANDO_TMUX_TASK_FEEDER_POLL_S:-1}"
STUCK_S="${SUTANDO_TMUX_TASK_FEEDER_STUCK_S:-60}"

mkdir -p "$(dirname "$LOG")" "$STATE" "$RESULTS" "$DONE_DIR"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [[ -f "$PID_FILE" ]]; then
    old="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${old:-}" ]] && kill -0 "$old" 2>/dev/null \
      && ps -p "$old" -o args= 2>/dev/null | grep -q "omni-exp-watch-tasks-to-tmux"; then
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) already running pid=$old — exit" >>"$LOG"
      exit 0
    fi
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR" 2>/dev/null || true
fi

if [[ -f "$PID_FILE" ]]; then
  old="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${old:-}" ]] && kill -0 "$old" 2>/dev/null; then
    if ps -p "$old" -o args= 2>/dev/null | grep -q "omni-exp-watch-tasks-to-tmux"; then
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) already running pid=$old — exit" >>"$LOG"
      exit 0
    fi
  fi
fi

cleanup() {
  if [[ -f "$PID_FILE" ]] && [[ "$(cat "$PID_FILE" 2>/dev/null)" == "$$" ]]; then
    rm -f "$PID_FILE"
  fi
  rm -rf "$LOCK_DIR"
}
trap cleanup EXIT INT TERM

echo "$$" > "$PID_FILE"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) feeder start pid=$$ poll=${POLL_S}s stuck=${STUCK_S}s" >>"$LOG"

session_ready() {
  tmux -S "$SOCK" has-session -t "$SESSION" 2>/dev/null
}

is_done() {
  local base="$1"
  # Only real completion signals. Do NOT treat a missing task file as done —
  # core may not have claimed it yet, or another feeder raced a delete; that
  # false-done was clearing launchd inbox markers while HUD stayed on Task file.
  [[ -f "$DONE_DIR/$base" ]] && return 0
  [[ -f "$RESULTS/$base" ]] && return 0
  return 1
}

mark_done() {
  local base="$1"
  : > "$DONE_DIR/$base"
}

# Sweep: anything that already has a result is done.
for f in "$RESULTS"/task-*.txt; do
  [[ -f "$f" ]] || continue
  mark_done "$(basename "$f")"
done

inject() {
  local base="$1"
  local path="$TASKS/$base"
  [[ -f "$path" ]] || return 1
  if ! session_ready; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) defer $base (no tmux session $SESSION)" >>"$LOG"
    return 1
  fi
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) inject $base" >>"$LOG"
  # Prefer a fresh prompt; don't Escape if it would kill in-flight work —
  # only send when pane looks idle is hard, so send as queued message.
  local msg
  msg="TASK_FILE: $base — Read ${path}, do the work, write ${RESULTS}/${base}. Then idle for the next TASK_FILE."
  printf '%s' "$msg" | tmux -S "$SOCK" load-buffer -
  tmux -S "$SOCK" paste-buffer -t "$SESSION" 2>/dev/null || true
  sleep 0.08
  tmux -S "$SOCK" send-keys -t "$SESSION" Enter 2>/dev/null || true
  return 0
}

oldest_pending() {
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    base=$(basename "$f")
    if is_done "$base"; then
      mark_done "$base"
      continue
    fi
    echo "$base"
    return 0
  done < <(ls -tr "$TASKS"/task-*.txt 2>/dev/null)
  return 1
}

inflight_base=""
inflight_at=0

for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  session_ready && break
  sleep 1
done

while true; do
  # Harvest completions (result may appear and then be deleted by omni).
  if [[ -n "$inflight_base" ]]; then
    if [[ -f "$RESULTS/$inflight_base" ]] || [[ ! -f "$TASKS/$inflight_base" ]]; then
      mark_done "$inflight_base"
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) done $inflight_base" >>"$LOG"
      inflight_base=""
      inflight_at=0
    fi
  fi
  for f in "$RESULTS"/task-*.txt; do
    [[ -f "$f" ]] || continue
    mark_done "$(basename "$f")"
  done

  base="$(oldest_pending || true)"
  if [[ -z "${base:-}" ]]; then
    sleep "$POLL_S"
    continue
  fi

  now=$(date +%s)
  if [[ -n "$inflight_base" ]] && ! is_done "$inflight_base"; then
    if (( now - inflight_at < STUCK_S )); then
      sleep "$POLL_S"
      continue
    fi
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) re-nudge $inflight_base (stuck ${STUCK_S}s)" >>"$LOG"
    base="$inflight_base"
  fi

  if inject "$base"; then
    inflight_base="$base"
    inflight_at=$(date +%s)
  fi
  sleep "$POLL_S"
done
