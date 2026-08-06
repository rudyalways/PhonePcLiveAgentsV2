#!/bin/bash
# launchd KeepAlive for omni-exp-watch-tasks-to-tmux (Monitor fallback).
#
# TCC: LaunchAgents cannot execute or reliably scan ~/Documents. Omni drops
# task basenames into Application Support/.../omni-exp-feeder/inbox/; this job only
# reads that inbox and injects into sutando-core tmux.
set -e
LABEL="com.sutando.omni-exp-tmux-task-feeder"
OLD_LABELS=("com.sutando.tmux-task-feeder" "com.sutando.omni-tmux-task-feeder")
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$REPO/src/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
SUPPORT="$HOME/Library/Application Support/Sutando/omni-exp-feeder"
DOMAIN="gui/$(id -u)"
SERVICE="$DOMAIN/$LABEL"

__HELPER="$REPO/src/workspace_resolve.sh"
# shellcheck source=workspace_resolve.sh
source "$__HELPER"
resolve_workspace_or_die

resolve_brew_bin() {
  [ -d /opt/homebrew/bin ] && { echo /opt/homebrew/bin; return; }
  [ -d /usr/local/bin ] && { echo /usr/local/bin; return; }
  echo /usr/bin
}

bootout_if_loaded() {
  if launchctl print "$SERVICE" >/dev/null 2>&1; then
    launchctl bootout "$SERVICE" 2>/dev/null || true
    for _ in $(seq 1 10); do
      launchctl print "$SERVICE" >/dev/null 2>&1 || break
      sleep 0.3
    done
  fi
}

materialize() {
  mkdir -p "$SUPPORT/inbox" "$SUPPORT/state"
  # Generate TCC-safe feeder (no exec of ~/Documents scripts, inbox-driven).
  cat > "$SUPPORT/omni-exp-watch-tasks-to-tmux.sh" <<EOF
#!/bin/bash
set -u
REPO="$REPO"
WS="$WORKSPACE"
SUPPORT="$SUPPORT"
SOCK="\${SUTANDO_TMUX_SOCKET:-/tmp/sutando-tmux.sock}"
SESSION="\${SUTANDO_TMUX_SESSION:-sutando-core}"
TASKS="\$WS/tasks"
RESULTS="\$WS/results"
INBOX="\$SUPPORT/inbox"
LOG="\$SUPPORT/omni-exp-watch-tasks-to-tmux.log"
STATE="\$SUPPORT/state"
DONE_DIR="\$STATE/omni-exp-watch-tasks-to-tmux.done"
PID_FILE="\$STATE/omni-exp-watch-tasks-to-tmux.pid"
LOCK_DIR="\$STATE/omni-exp-watch-tasks-to-tmux.lock"
POLL_S="\${SUTANDO_TMUX_TASK_FEEDER_POLL_S:-1}"
STUCK_S="\${SUTANDO_TMUX_TASK_FEEDER_STUCK_S:-60}"
# After this many re-nudges without a done-marker, abandon and drain the next inbox
# item (prevents one orphan from head-of-line blocking Safari/etc.).
MAX_NUDGES="\${SUTANDO_TMUX_TASK_FEEDER_MAX_NUDGES:-3}"
mkdir -p "\$INBOX" "\$STATE" "\$DONE_DIR"
if ! mkdir "\$LOCK_DIR" 2>/dev/null; then
  old="\$(cat "\$PID_FILE" 2>/dev/null || true)"
  if [[ -n "\${old:-}" ]] && kill -0 "\$old" 2>/dev/null; then
    echo "\$(date -u +%Y-%m-%dT%H:%M:%SZ) already running pid=\$old" >>"\$LOG"
    exit 0
  fi
  rm -rf "\$LOCK_DIR"; mkdir "\$LOCK_DIR" 2>/dev/null || true
fi
cleanup() {
  [[ -f "\$PID_FILE" && "\$(cat "\$PID_FILE" 2>/dev/null)" == "\$\$" ]] && rm -f "\$PID_FILE"
  rm -rf "\$LOCK_DIR"
}
trap cleanup EXIT INT TERM
echo "\$\$" > "\$PID_FILE"
echo "\$(date -u +%Y-%m-%dT%H:%M:%SZ) feeder start pid=\$\$ inbox-mode" >>"\$LOG"
session_ready() { tmux -S "\$SOCK" has-session -t "\$SESSION" 2>/dev/null; }
# TCC: LaunchAgents cannot read ~/Documents — never probe \$RESULTS/\$TASKS for done.
# Omni-exp (or install seed) writes \$DONE_DIR/<basename> when a result is consumed.
is_done() { [[ -f "\$DONE_DIR/\$1" ]]; }
mark_done() { : > "\$DONE_DIR/\$1"; rm -f "\$INBOX/\$1" 2>/dev/null || true; }
inject() {
  local base="\$1" path="\$TASKS/\$base"
  session_ready || { echo "\$(date -u +%Y-%m-%dT%H:%M:%SZ) defer \$base" >>"\$LOG"; return 1; }
  echo "\$(date -u +%Y-%m-%dT%H:%M:%SZ) inject \$base" >>"\$LOG"
  local msg="TASK_FILE: \$base — Read \${path}, do the work, write \${RESULTS}/\${base}. Then idle for the next TASK_FILE."
  printf '%s' "\$msg" | tmux -S "\$SOCK" load-buffer -
  tmux -S "\$SOCK" paste-buffer -t "\$SESSION" 2>/dev/null || true
  sleep 0.08
  tmux -S "\$SOCK" send-keys -t "\$SESSION" Enter 2>/dev/null || true
}
oldest_pending() {
  while IFS= read -r f; do
    [[ -n "\$f" ]] || continue
    base=\$(basename "\$f")
    case "\$base" in task-*.txt) ;; *) continue ;; esac
    is_done "\$base" && { mark_done "\$base"; continue; }
    echo "\$base"; return 0
  done < <(ls -tr "\$INBOX"/task-*.txt 2>/dev/null)
  return 1
}
# Do not seed from \$TASKS here — LaunchAgent gets Operation not permitted on Documents.
inflight_base=""; inflight_at=0; inflight_nudges=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do session_ready && break; sleep 1; done
while true; do
  if [[ -n "\$inflight_base" ]] && is_done "\$inflight_base"; then
    mark_done "\$inflight_base"
    echo "\$(date -u +%Y-%m-%dT%H:%M:%SZ) done \$inflight_base" >>"\$LOG"
    inflight_base=""; inflight_at=0; inflight_nudges=0
  fi
  base="\$(oldest_pending || true)"
  if [[ -z "\${base:-}" ]]; then sleep "\$POLL_S"; continue; fi
  now=\$(date +%s)
  if [[ -n "\$inflight_base" ]] && ! is_done "\$inflight_base"; then
    if (( now - inflight_at < STUCK_S )); then sleep "\$POLL_S"; continue; fi
    inflight_nudges=\$((inflight_nudges + 1))
    if (( inflight_nudges > MAX_NUDGES )); then
      echo "\$(date -u +%Y-%m-%dT%H:%M:%SZ) abandon \$inflight_base after \${MAX_NUDGES} nudges (unblock inbox)" >>"\$LOG"
      mark_done "\$inflight_base"
      inflight_base=""; inflight_at=0; inflight_nudges=0
      sleep "\$POLL_S"
      continue
    fi
    echo "\$(date -u +%Y-%m-%dT%H:%M:%SZ) re-nudge \$inflight_base (\$inflight_nudges/\$MAX_NUDGES)" >>"\$LOG"
    base="\$inflight_base"
  fi
  if inject "\$base"; then
    if [[ "\$base" != "\$inflight_base" ]]; then inflight_nudges=0; fi
    inflight_base="\$base"; inflight_at=\$(date +%s)
  fi
  sleep "\$POLL_S"
done
EOF
  chmod +x "$SUPPORT/omni-exp-watch-tasks-to-tmux.sh"
  cat > "$SUPPORT/run-feeder.sh" <<EOF
#!/bin/bash
set -u
cd /tmp || true
exec /bin/bash "$SUPPORT/omni-exp-watch-tasks-to-tmux.sh"
EOF
  chmod +x "$SUPPORT/run-feeder.sh"
  # Seed current backlog into inbox from this interactive shell (has Documents access).
  for f in "$WORKSPACE"/tasks/task-*.txt; do
    [ -f "$f" ] || continue
    base=$(basename "$f")
    [ -f "$WORKSPACE/results/$base" ] && continue
    : > "$SUPPORT/inbox/$base"
  done
  mkdir -p "$WORKSPACE/logs"
  ln -sfn "$SUPPORT/omni-exp-watch-tasks-to-tmux.log" "$WORKSPACE/logs/omni-exp-watch-tasks-to-tmux.launchd.log" 2>/dev/null || true
}

cmd="${1:-install}"
case "$cmd" in
  install)
    BREW_BIN="$(resolve_brew_bin)"
    mkdir -p "$HOME/Library/LaunchAgents"
    # Drop legacy labels/paths from pre-omni-exp renames.
    for _old in "${OLD_LABELS[@]}"; do
      if launchctl print "$DOMAIN/$_old" >/dev/null 2>&1; then
        launchctl bootout "$DOMAIN/$_old" 2>/dev/null || true
      fi
      rm -f "$HOME/Library/LaunchAgents/$_old.plist"
    done
    pkill -f "$SUPPORT/omni-exp-watch-tasks-to-tmux" 2>/dev/null || true
    pkill -f "Application Support/Sutando/feeder" 2>/dev/null || true
    pkill -f "Application Support/Sutando/omni-feeder" 2>/dev/null || true
    sleep 0.3
    materialize
    sed \
      -e "s|__SUPPORT__|$SUPPORT|g" \
      -e "s|__BREW_BIN__|$BREW_BIN|g" \
      -e "s|__HOME__|$HOME|g" \
      "$TEMPLATE" > "$DEST"
    bootout_if_loaded
    launchctl bootstrap "$DOMAIN" "$DEST"
    echo "Loaded $SERVICE (inbox-mode KeepAlive)"
    ;;
  --uninstall|uninstall)
    bootout_if_loaded
    for _old in "${OLD_LABELS[@]}"; do
      if launchctl print "$DOMAIN/$_old" >/dev/null 2>&1; then
        launchctl bootout "$DOMAIN/$_old" 2>/dev/null || true
      fi
      rm -f "$HOME/Library/LaunchAgents/$_old.plist"
    done
    rm -f "$DEST"
    echo Uninstalled
    ;;
  --status|status)
    if launchctl print "$SERVICE" >/dev/null 2>&1; then
      launchctl print "$SERVICE" | grep -E '^\s+(state|pid|last exit code|runs)' || true
    else echo "(not loaded)"; fi
    echo "inbox: $(ls "$SUPPORT/inbox"/task-*.txt 2>/dev/null | wc -l | tr -d ' ') pending notifies"
    ;;
  --restart|restart)
    if ! launchctl print "$SERVICE" >/dev/null 2>&1; then exec bash "$0" install; fi
    materialize
    launchctl kickstart -k "$SERVICE"
    bash "$0" --status
    ;;
  *) echo "Usage: $0 [install|--uninstall|--status|--restart]" >&2; exit 2 ;;
esac
