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
SRC_FEEDER="$REPO/src/omni-exp-watch-tasks-to-tmux-inbox.sh"
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
  # Legacy load path may still hold the job when bootstrap API is broken.
  launchctl unload "$DEST" 2>/dev/null || true
}

# Prefer bootstrap; on macOS "Bootstrap failed: 5" fall back to load -w
# (observed 2026-08-06 — bootstrap I/O error while load -w succeeds).
load_agent() {
  local err
  if err="$(launchctl bootstrap "$DOMAIN" "$DEST" 2>&1)"; then
    echo "Loaded $SERVICE via bootstrap (inbox-mode KeepAlive)"
    return 0
  fi
  echo "  bootstrap failed ($err) — trying launchctl load -w" >&2
  if launchctl load -w "$DEST" 2>/dev/null; then
    echo "Loaded $SERVICE via load -w (inbox-mode KeepAlive)"
    return 0
  fi
  echo "ERROR: could not load $SERVICE (bootstrap + load -w both failed)" >&2
  return 1
}

materialize() {
  mkdir -p "$SUPPORT/inbox" "$SUPPORT/state"
  if [[ ! -f "$SRC_FEEDER" ]]; then
    echo "ERROR: missing $SRC_FEEDER" >&2
    exit 1
  fi
  cp "$SRC_FEEDER" "$SUPPORT/omni-exp-watch-tasks-to-tmux.sh"
  chmod +x "$SUPPORT/omni-exp-watch-tasks-to-tmux.sh"
  cat > "$SUPPORT/run-feeder.sh" <<EOF
#!/bin/bash
set -u
cd /tmp || true
export SUTANDO_FEEDER_WS='$WORKSPACE'
export SUTANDO_FEEDER_SUPPORT='$SUPPORT'
exec /bin/bash "\$SUTANDO_FEEDER_SUPPORT/omni-exp-watch-tasks-to-tmux.sh"
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
    # Prefer launchd over Documents scanner (avoids double TASK_FILE paste).
    pkill -f "$REPO/src/omni-exp-watch-tasks-to-tmux.sh" 2>/dev/null || true
    pkill -f "omni-exp-watch-tasks-to-tmux-supervisor" 2>/dev/null || true
    sleep 0.3
    materialize
    # Strip XML comments — some launchctl paths choke on them (Bootstrap 5).
    python3 - "$TEMPLATE" "$DEST" "$SUPPORT" "$BREW_BIN" "$HOME" <<'PY'
import re, sys
src, dest, support, brew, home = sys.argv[1:6]
text = open(src, encoding="utf-8").read()
text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
text = (
    text.replace("__SUPPORT__", support)
    .replace("__BREW_BIN__", brew)
    .replace("__HOME__", home)
)
open(dest, "w", encoding="utf-8").write(text)
PY
    plutil -lint "$DEST" >/dev/null
    bootout_if_loaded
    load_agent
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
    if launchctl print "$SERVICE" >/dev/null 2>&1 || launchctl list 2>/dev/null | grep -q "$LABEL"; then
      launchctl print "$SERVICE" 2>/dev/null | grep -E '^\s+(state|pid|last exit code|runs)' || \
        launchctl list | grep "$LABEL" || true
    else echo "(not loaded)"; fi
    echo "inbox: $(ls "$SUPPORT/inbox"/task-*.txt 2>/dev/null | wc -l | tr -d ' ') pending notifies"
    ;;
  --restart|restart)
    if ! launchctl print "$SERVICE" >/dev/null 2>&1 && ! launchctl list 2>/dev/null | grep -q "$LABEL"; then
      exec bash "$0" install
    fi
    materialize
    launchctl kickstart -k "$SERVICE" 2>/dev/null || launchctl load -w "$DEST" 2>/dev/null || true
    bash "$0" --status
    ;;
  *) echo "Usage: $0 [install|--uninstall|--status|--restart]" >&2; exit 2 ;;
esac
