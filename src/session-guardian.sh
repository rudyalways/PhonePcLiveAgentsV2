#!/bin/bash
# Session guardian — auto-restarts sutando-core when it dies or stalls.
# Started by startup.sh (nohup background) before exec-ing the Claude session.
#
# Restart triggers:
#   1. Process dead  — no `claude --name sutando-core` process
#   2. Session stuck — core-status.json > 10 min old AND tasks/*.txt > 5 min old
#
# Always restarts via src/agent/start-cli.sh so .env policy (including
# SUTANDO_PROACTIVE_LOOP_ENABLED) and skill symlink sync are applied.
# Never hardcode -- "/proactive-loop" here — that bypasses the whole-loop
# toggle and can burn budget when the skill is intentionally disabled.

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$REPO/logs/session-guardian.log"
SOCKET="${SUTANDO_TMUX_SOCKET:-/tmp/sutando-tmux.sock}"

# Load repo .env so the gate + sync see the same value as start-cli / install.
# Preserve an explicit ambient override (skill-config: env > .env).
if [ -f "$REPO/.env" ]; then
  _proactive_was_set=0
  if [ "${SUTANDO_PROACTIVE_LOOP_ENABLED+x}" = x ]; then
    _proactive_was_set=1
    _proactive_ambient="$SUTANDO_PROACTIVE_LOOP_ENABLED"
  fi
  set -a
  # shellcheck disable=SC1091
  source "$REPO/.env"
  set +a
  if [ "$_proactive_was_set" = 1 ]; then
    export SUTANDO_PROACTIVE_LOOP_ENABLED="$_proactive_ambient"
  fi
  unset _proactive_was_set _proactive_ambient
fi

start_session() {
    echo "$(date -Iseconds) [guardian] Starting sutando-core via start-cli.sh (proactive=$(python3 "$REPO/skills/proactive-loop/scripts/proactive-loop-enabled.py" 2>/dev/null || echo unknown))..." >> "$LOG"
    # --restart is idempotent when the session is already gone; it also runs
    # sync-skill-link.sh so a disabled toggle keeps the skill unlinked.
    if ! bash "$REPO/src/agent/start-cli.sh" --restart >> "$LOG" 2>&1; then
        echo "$(date -Iseconds) [guardian] start-cli.sh --restart failed" >> "$LOG"
    fi
}

kill_session() {
    echo "$(date -Iseconds) [guardian] Killing stuck session..." >> "$LOG"
    pkill -f "claude.*--name.*sutando-core" 2>/dev/null || true
    tmux -S "$SOCKET" kill-session -t sutando-core 2>/dev/null || true
    sleep 3
}

is_stuck() {
    local status_file="$REPO/core-status.json"
    [ -f "$status_file" ] || return 1
    local now ts age
    now=$(date +%s)
    ts=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(int(d.get('ts',0)))" \
        "$status_file" 2>/dev/null) || return 1
    age=$((now - ts))
    # Status must be stale > 10 min
    [ "$age" -gt 600 ] || return 1
    # AND there must be tasks waiting > 5 min (otherwise idle is fine)
    find "$REPO/tasks" -name "*.txt" -mmin +5 -print -quit 2>/dev/null | grep -q .
}

echo "$(date -Iseconds) [guardian] Started (PID $$)" >> "$LOG"

while true; do
    if ! pgrep -qf "claude.*--name.*sutando-core" 2>/dev/null; then
        echo "$(date -Iseconds) [guardian] sutando-core not running — restarting" >> "$LOG"
        start_session
    elif is_stuck; then
        echo "$(date -Iseconds) [guardian] stuck (stale status + pending tasks) — restarting" >> "$LOG"
        kill_session
        start_session
    fi
    sleep 30
done
