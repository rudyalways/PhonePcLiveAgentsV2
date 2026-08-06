#!/bin/bash
# Keep omni-exp-agent.py alive. Restarts on exit.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WS="$(bash "$REPO/scripts/sutando-config.sh" workspace)"
LOG="$WS/logs/omni-exp-supervisor.log"
PID_FILE="$WS/state/omni-exp-supervisor.pid"
mkdir -p "$(dirname "$LOG")" "$WS/state"

if [[ -f "$PID_FILE" ]]; then
  old="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${old:-}" ]] && kill -0 "$old" 2>/dev/null \
    && ps -p "$old" -o args= 2>/dev/null | grep -q "omni-exp-supervisor"; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) already running pid=$old" >>"$LOG"
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

PYTHON="${OMNI_PYTHON:-$REPO/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"

while true; do
  if [[ -f "$REPO/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO/.env"
    set +a
  fi
  export OMNI_PORT="${OMNI_EXP_PORT:-${OMNI_EXP_PORT:-${OMNI_PORT:-7090}}}"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) launching omni-exp-agent port=$OMNI_PORT" >>"$LOG"
  # setsid: survive caller shell exit (Cursor/agent shells otherwise reap children)
  if command -v setsid >/dev/null 2>&1; then
    setsid "$PYTHON" -u "$REPO/src/omni-exp-agent.py" >>"$WS/logs/omni-exp-agent.log" 2>&1 &
  else
    # macOS: no setsid by default — use nohup + disown in a subshell
    ( cd "$REPO" && nohup "$PYTHON" -u "$REPO/src/omni-exp-agent.py" >>"$WS/logs/omni-exp-agent.log" 2>&1 & )
  fi
  child=$!
  echo "$child" > "$WS/state/omni-exp-agent.pid"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) omni pid=$child" >>"$LOG"
  wait "$child" || true
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) omni exited — restart in 2s" >>"$LOG"
  rm -f "$WS/state/omni-exp-agent.pid"
  sleep 2
done
