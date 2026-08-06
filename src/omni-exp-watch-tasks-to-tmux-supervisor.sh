#!/bin/bash
# Keep omni-exp-watch-tasks-to-tmux.sh alive (Monitor fallback). Restarts on exit.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WS="$(bash "$REPO/scripts/sutando-config.sh" workspace)"
LOG="$WS/logs/omni-exp-watch-tasks-to-tmux.supervisor.log"
PID_FILE="$WS/state/omni-exp-watch-tasks-to-tmux-supervisor.pid"
mkdir -p "$(dirname "$LOG")" "$WS/state"

if [[ -f "$PID_FILE" ]]; then
  old="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${old:-}" ]] && kill -0 "$old" 2>/dev/null \
    && ps -p "$old" -o args= 2>/dev/null | grep -q "omni-exp-watch-tasks-to-tmux-supervisor"; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) supervisor already running pid=$old" >>"$LOG"
    exit 0
  fi
fi

cleanup() {
  if [[ -f "$PID_FILE" ]] && [[ "$(cat "$PID_FILE" 2>/dev/null)" == "$$" ]]; then
    rm -f "$PID_FILE"
  fi
}
trap cleanup EXIT INT TERM
echo "$$" > "$PID_FILE"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) supervisor start pid=$$" >>"$LOG"

while true; do
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) launching feeder" >>"$LOG"
  bash "$REPO/src/omni-exp-watch-tasks-to-tmux.sh" >>"$WS/logs/omni-exp-watch-tasks-to-tmux.log" 2>&1 || true
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) feeder exited — restart in 2s" >>"$LOG"
  rm -rf "$WS/state/omni-exp-watch-tasks-to-tmux.lock" "$WS/state/omni-exp-watch-tasks-to-tmux.pid"
  sleep 2
done
