#!/bin/bash
# Session guardian — auto-restarts sutando-core when it dies or stalls.
# Started by startup.sh (nohup background) before exec-ing the Claude session.
#
# Restart triggers:
#   1. Process dead  — no `claude --name sutando-core` process
#   2. Session stuck — core-status.json > 10 min old AND tasks/*.txt > 5 min old

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$REPO/logs/session-guardian.log"
SOCKET="/tmp/sutando-tmux.sock"

start_session() {
    echo "$(date -Iseconds) [guardian] Starting sutando-core..." >> "$LOG"
    if command -v tmux > /dev/null 2>&1; then
        tmux -S "$SOCKET" new-session -d -s sutando-core \
            claude --name sutando-core --remote-control "Sutando" \
            --dangerously-skip-permissions --add-dir "$HOME" \
            -- "/proactive-loop" >> "$LOG" 2>&1 || true
    else
        nohup claude --name sutando-core --remote-control "Sutando" \
            --dangerously-skip-permissions --add-dir "$HOME" \
            -- "/proactive-loop" >> "$REPO/logs/sutando-core.log" 2>&1 &
        disown
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
