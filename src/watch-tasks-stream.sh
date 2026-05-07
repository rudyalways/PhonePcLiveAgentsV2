#!/bin/bash
# Streaming task watcher — companion to watch-tasks.sh.
#
# Runs fswatch indefinitely and emits ONE line per new task file appearance.
# Designed for persistent monitor-style invocation without process-restart cycles.
#
# Output format per event:
#   TASK_FILE: <basename>
# Plus an initial sweep at startup for any pre-existing files.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
TASKS_DIR="${1:-$REPO/tasks}"

_pipeline_emit() {
  python3 "$REPO/src/pipeline_emit.py" "$@" 2>/dev/null || true
}

_emit_task() {
  local path="$1"
  local detail="$2"
  local tid
  tid="$(basename "$path" .txt)"
  echo "TASK_FILE: $(basename "$path")"
  _pipeline_emit watcher_pickup "$tid" --ok --detail "$detail" --component watch-tasks-stream
  _pipeline_emit watcher_to_core_queue "$tid" --ok --detail "Listed for Claude sutando-core" --component watch-tasks-stream
}

mkdir -p "$TASKS_DIR"
mkdir -p "$REPO/results/calls"

if ! command -v fswatch >/dev/null 2>&1; then
  _pipeline_emit watcher_fswatch_missing "" --fail --detail "fswatch not installed" --component watch-tasks-stream
  echo "ERROR: fswatch not installed" >&2
  exit 1
fi

# Canonicalize watched dir. macOS fswatch may emit physical paths
# (for example /private/tmp instead of /tmp).
TASKS_DIR_ABS="$(cd "$TASKS_DIR" && pwd -P)"

# Initial sweep catches files written during a restart gap.
shopt -s nullglob
for f in "$TASKS_DIR"/*.txt; do
  _emit_task "$f" "TASK_FILE(initial_scan)"
done
shopt -u nullglob

# Stream subsequent task file appearances. FSEvents can be recursive, so only
# accept direct children of TASKS_DIR that still exist after the event.
fswatch \
  -l 0.5 \
  --event Created \
  --event Renamed \
  "$TASKS_DIR" 2>/dev/null \
| while IFS= read -r path; do
  case "$path" in
    *.txt)
      parent="$(dirname "$path")"
      if [ "$parent" = "$TASKS_DIR_ABS" ] && [ -f "$path" ]; then
        _emit_task "$path" "TASK_FILE(fswatch)"
      fi
      ;;
  esac
done
