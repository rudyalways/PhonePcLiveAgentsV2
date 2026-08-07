#!/bin/bash
# Inbox-mode tmux task feeder (launchd / Application Support).
#
# Omni writes task basenames into $SUPPORT/inbox/; this process injects into
# sutando-core tmux. Never scans ~/Documents (TCC). Done markers live under
# $SUPPORT/state/ — omni-exp calls mark_feeder_done when a result is consumed.
#
# Installed by src/install-omni-exp-tmux-task-feeder-launchd.sh (materialize).
# Env required: SUTANDO_FEEDER_WS, SUTANDO_FEEDER_SUPPORT (set by run-feeder.sh).
set -u

WS="${SUTANDO_FEEDER_WS:?}"
SUPPORT="${SUTANDO_FEEDER_SUPPORT:?}"
SOCK="${SUTANDO_TMUX_SOCKET:-/tmp/sutando-tmux.sock}"
SESSION="${SUTANDO_TMUX_SESSION:-sutando-core}"
TASKS="$WS/tasks"
RESULTS="$WS/results"
INBOX="$SUPPORT/inbox"
LOG="$SUPPORT/omni-exp-watch-tasks-to-tmux.log"
STATE="$SUPPORT/state"
DONE_DIR="$STATE/omni-exp-watch-tasks-to-tmux.done"
PID_FILE="$STATE/omni-exp-watch-tasks-to-tmux.pid"
LOCK_DIR="$STATE/omni-exp-watch-tasks-to-tmux.lock"
POLL_S="${SUTANDO_TMUX_TASK_FEEDER_POLL_S:-1}"
STUCK_S="${SUTANDO_TMUX_TASK_FEEDER_STUCK_S:-15}"
MAX_NUDGES="${SUTANDO_TMUX_TASK_FEEDER_MAX_NUDGES:-2}"
BURST_MAX="${SUTANDO_TMUX_TASK_FEEDER_BURST_MAX:-8}"

mkdir -p "$INBOX" "$STATE" "$DONE_DIR"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  old="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${old:-}" ]] && kill -0 "$old" 2>/dev/null; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) already running pid=$old" >>"$LOG"
    exit 0
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR" 2>/dev/null || true
fi

cleanup() {
  if [[ -f "$PID_FILE" ]] && [[ "$(cat "$PID_FILE" 2>/dev/null)" == "$$" ]]; then
    rm -f "$PID_FILE"
  fi
  rm -rf "$LOCK_DIR"
}
trap cleanup EXIT INT TERM

echo "$$" > "$PID_FILE"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) feeder start pid=$$ inbox-mode stuck=${STUCK_S}s burst=${BURST_MAX}" >>"$LOG"

session_ready() { tmux -S "$SOCK" has-session -t "$SESSION" 2>/dev/null; }

# Match src/core_readiness.py BOOTING_STALE_S (15 min). While booting, hold —
# never abandon: /startup Step 1 processes tasks/ from disk.
core_booting() {
  local f="$WS/state/core-booting.json"
  [[ -f "$f" ]] || return 1
  local age
  age=$(( $(date +%s) - $(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0) ))
  [[ "$age" -lt 900 ]]
}

core_idle() {
  local pane
  pane="$(tmux -S "$SOCK" capture-pane -t "$SESSION" -p -S -12 2>/dev/null)" || return 1
  if printf '%s' "$pane" | grep -qiE 'Bash\(|Reading |Running |✽|✳|✶|Infusing|Brewing|Leavening|Slithering|Musing|Worked for|ctrl\+o to expand'; then
    return 1
  fi
  if printf '%s' "$pane" | grep -qE 'Idling\.|Press up to edit queued messages'; then
    return 0
  fi
  if printf '%s' "$pane" | tail -n 6 | grep -qE '^❯[[:space:]]*$|^❯[[:space:]]+'; then
    return 0
  fi
  return 1
}

is_done() { [[ -f "$DONE_DIR/$1" ]]; }
mark_done() { : > "$DONE_DIR/$1"; rm -f "$INBOX/$1" 2>/dev/null || true; }

inject() {
  local base="$1" path="$TASKS/$base"
  session_ready || { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) defer $base" >>"$LOG"; return 1; }
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) inject $base" >>"$LOG"
  local msg="TASK_FILE: $base — Read ${path}, do the work, write ${RESULTS}/${base}. Then idle for the next TASK_FILE."
  printf '%s' "$msg" | tmux -S "$SOCK" load-buffer -
  tmux -S "$SOCK" paste-buffer -t "$SESSION" 2>/dev/null || true
  sleep 0.08
  tmux -S "$SOCK" send-keys -t "$SESSION" Enter 2>/dev/null || true
}

list_pending() {
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    base=$(basename "$f")
    case "$base" in task-*.txt) ;; *) continue ;; esac
    is_done "$base" && { mark_done "$base"; continue; }
    echo "$base"
  done < <(ls -tr "$INBOX"/task-*.txt 2>/dev/null)
}

inflight_base=""
inflight_at=0
inflight_nudges=0

for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  session_ready && break
  sleep 1
done

while true; do
  if [[ -n "$inflight_base" ]] && is_done "$inflight_base"; then
    mark_done "$inflight_base"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) done $inflight_base" >>"$LOG"
    inflight_base=""
    inflight_at=0
    inflight_nudges=0
  fi

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

  # Booting: leave tasks queued. /startup reads tasks/ directly; do not abandon.
  if core_booting; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) hold ${#pending[@]} pending — core booting (/startup owns tasks/)" >>"$LOG"
    # Reset nudge clock so a long /startup cannot burn MAX_NUDGES.
    if [[ -n "$inflight_base" ]]; then
      inflight_at=$now
      inflight_nudges=0
    fi
    sleep "$POLL_S"
    continue
  fi

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

  base="${pending[0]}"
  if [[ -n "$inflight_base" ]] && ! is_done "$inflight_base"; then
    if [[ $((now - inflight_at)) -lt "$STUCK_S" ]]; then
      sleep "$POLL_S"
      continue
    fi
    inflight_nudges=$((inflight_nudges + 1))
    if [[ "$inflight_nudges" -gt "$MAX_NUDGES" ]]; then
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) abandon $inflight_base after ${MAX_NUDGES} nudges (unblock inbox)" >>"$LOG"
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
