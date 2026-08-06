#!/bin/bash
# Smoke-test ASAP knobs in the Documents + inbox feeder scripts.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DOC="$REPO/src/omni-exp-watch-tasks-to-tmux.sh"
INBOX="$REPO/src/omni-exp-watch-tasks-to-tmux-inbox.sh"

grep -q 'STUCK_S=.*15' "$DOC"
grep -q 'core_idle' "$DOC"
grep -q 'idle-burst' "$DOC"
grep -q 'BURST_MAX' "$DOC"
grep -q 'MAX_NUDGES' "$DOC"

grep -q 'STUCK_S=.*15' "$INBOX"
grep -q 'core_idle' "$INBOX"
grep -q 'idle-burst' "$INBOX"
grep -q 'SUTANDO_FEEDER_WS' "$INBOX"

# Installer must copy inbox script + load -w fallback.
INST="$REPO/src/install-omni-exp-tmux-task-feeder-launchd.sh"
grep -q 'omni-exp-watch-tasks-to-tmux-inbox.sh' "$INST"
grep -q 'load -w' "$INST"

# bash -n syntax check (macOS bash 3.2)
bash -n "$DOC"
bash -n "$INBOX"
bash -n "$INST"

echo "omni-exp-tmux-feeder-asap.test.sh OK"
