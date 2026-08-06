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
#
# ASAP policy (2026-08-06):
#   - When the core pane looks idle, inject ALL pending tasks (burst into Claude
#     queue) instead of head-of-line blocking on one inflight.
#   - Re-nudge stuck inflight after STUCK_S (default 15s, was 60s).
#   - Abandon after MAX_NUDGES so one orphan cannot block the queue forever.
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
STUCK_S="${SUTANDO_TMUX_TASK_FEEDER_STUCK_S:-15}"
MAX_NUDGES="${SUTANDO_TMUX_TASK_FEEDER_MAX_NUDGES:-2}"
BURST_MAX="${SUTANDO_TMUX_TASK_FEEDER_BURST_MAX:-8}"

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
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) feeder start pid=$$ poll=${POLL_S}s stuck=${STUCK_S}s max_nudges=${MAX_NUDGES} burst=${BURST_MAX}" >>"$LOG"

session_ready() {
  tmux -S "$SOCK" has-session -t "$SESSION" 2>/dev/null
}

# True when the core pane looks free to accept new TASK_FILE prompts.
core_idle() {
  local pane
  pane="$(tmux -S "$SOCK" capture-pane -t "$SESSION" -p -S -12 2>/dev/null)" || return 1
  # Busy markers first (tool / thinking UI).
  if printf '%s' "$pane" | grep -qiE 'Bash\(|Reading |Running |✽|✳|✶|Infusing|Brewing|Leavening|Slithering|Musing|Worked for|ctrl\+o to expand'; then
    return 1
  fi
  # Idle / ready-for-input markers.
  if printf '%s' "$pane" | grep -qE 'Idling\.|Press up to edit queued messages'; then
    return 0
  fi
  # Bare prompt line without an active tool spinner above.
  if printf '%s' "$pane" | tail -n 6 | grep -qE '^❯[[:space:]]*$|^❯[[:space:]]+'; then
    return 0
  fi
  return 1
}

is_done() {
  local base="$1"
  # Only real completion signals. Do NOT treat a missing task file as done —
  # core may not have claimed it yet, or another feeder raced a delete.
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
  local msg
  msg="TASK_FILE: $base — Read ${path}, do the work, write ${RESULTS}/${base}. Then idle for the next TASK_FILE."
  printf '%s' "$msg" | tmux -S "$SOCK" load-buffer -
  tmux -S "$SOCK" paste-buffer -t "$SESSION" 2>/dev/null || true
  sleep 0.08
  tmux -S "$SOCK" send-keys -t "$SESSION" Enter 2>/dev/null || true
  return 0
}

list_pending() {
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    base=$(basename "$f")
    if is_done "$base"; then
      mark_done "$base"
      continue
    fi
    echo "$base"
  done < <(ls -tr "$TASKS"/task-*.txt 2>/dev/null)
}

oldest_pending() {
  list_pending | head -n 1
}

inflight_base=""
inflight_at=0
inflight_nudges=0

for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  session_ready && break
  sleep 1
done

while true; do
  # Harvest completions (result may appear and then be deleted by omni).
  if [[ -n "$inflight_base" ]]; then
    if is_done "$inflight_base" || [[ -f "$RESULTS/$inflight_base" ]]; then
      mark_done "$inflight_base"
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) done $inflight_base" >>"$LOG"
      inflight_base=""
      inflight_at=0
      inflight_nudges=0
    elif [[ ! -f "$TASKS/$inflight_base" ]]; then
      # Claimed/archived without result yet — keep waiting; do not mark done.
      :
    fi
  fi
  for f in "$RESULTS"/task-*.txt; do
    [[ -f "$f" ]] || continue
    mark_done "$(basename "$f")"
  done

  pending=()
  while IFS= read -r _p; do
    [[ -n "$_p" ]] || continue
    pending+=("$_p")
  done < <(list_pending)
  if [[ ${#pending[@]} -eq 0 ]]; then
    sleep "$POLL_S"
    continue
  fi

  now=$(date +%s)

  # Idle + backlog: burst-inject every pending task into Claude's queue.
  if core_idle; then
    n=0
    for base in "${pending[@]}"; do
      if [[ "$n" -ge "$BURST_MAX" ]]; then
        break
      fi
      if inject "$base"; then
        inflight_base="$base"
        inflight_at=$now
        inflight_nudges=0
        n=$((n + 1))
        sleep 0.15
      fi
    done
    if [[ "$n" -gt 1 ]]; then
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) idle-burst injected $n task(s)" >>"$LOG"
    fi
    sleep "$POLL_S"
    continue
  fi

  # Busy core: HOL only; re-nudge / abandon stuck head.
  base="${pending[0]}"
  if [[ -n "$inflight_base" ]] && ! is_done "$inflight_base"; then
    if [[ $((now - inflight_at)) -lt "$STUCK_S" ]]; then
      sleep "$POLL_S"
      continue
    fi
    inflight_nudges=$((inflight_nudges + 1))
    if [[ "$inflight_nudges" -gt "$MAX_NUDGES" ]]; then
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) abandon $inflight_base after ${MAX_NUDGES} nudges (unblock queue)" >>"$LOG"
      mark_done "$inflight_base"
      inflight_base=""
      inflight_at=0
      inflight_nudges=0
      sleep "$POLL_S"
      continue
    fi
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) re-nudge $inflight_base (${inflight_nudges}/${MAX_NUDGES})" >>"$LOG"
    base="$inflight_base"
  fi

  if inject "$base"; then
    if [[ "$base" != "$inflight_base" ]]; then
      inflight_nudges=0
    fi
    inflight_base="$base"
    inflight_at=$now
  fi
  sleep "$POLL_S"
done
