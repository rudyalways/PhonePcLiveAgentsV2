#!/bin/bash
# Watch for new task files in tasks/ directory.
# Usage: bash src/watch-tasks.sh (run_in_background: true)
#
# Uses fswatch -1 to detect one new file, then exits.
# The caller processes tasks and restarts.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
TASKS_DIR="${1:-$REPO/tasks}"

_pipeline_emit() {
  python3 "$REPO/src/pipeline_emit.py" "$@" 2>/dev/null || true
}

# Ensure required directories exist
mkdir -p "$TASKS_DIR"
mkdir -p "$REPO/results/calls"

if ! command -v fswatch >/dev/null 2>&1; then
  _pipeline_emit watcher_fswatch_missing "" --fail --detail "fswatch not installed" --component watch-tasks
fi

# Wait for a new .txt file — loop until one actually appears
while true; do
  # Check for existing files BEFORE waiting (catches files written during restarts)
  if ls "$TASKS_DIR"/*.txt >/dev/null 2>&1; then
    echo "TASK_DETECTED: Process ALL .txt files in tasks/."
    for f in "$TASKS_DIR"/*.txt; do
      tid="$(basename "$f" .txt)"
      echo "--- $(basename "$f") ---"; cat "$f"
      _pipeline_emit watcher_pickup "$tid" --ok --detail "TASK_DETECTED(existing)" --component watch-tasks
      _pipeline_emit watcher_to_core_queue "$tid" --ok --detail "Listed for Claude sutando-core" --component watch-tasks
    done
    break
  fi
  # Wait for next filesystem event
  CHANGED=$(fswatch -1 -l 1 "$TASKS_DIR" 2>/dev/null)
  echo "fswatch triggered: $CHANGED"
  # Check again after event
  if ls "$TASKS_DIR"/*.txt >/dev/null 2>&1; then
    echo "TASK_DETECTED: Process ALL .txt files in tasks/."
    for f in "$TASKS_DIR"/*.txt; do
      tid="$(basename "$f" .txt)"
      echo "--- $(basename "$f") ---"; cat "$f"
      _pipeline_emit watcher_pickup "$tid" --ok --detail "TASK_DETECTED(fswatch:$CHANGED)" --component watch-tasks
      _pipeline_emit watcher_to_core_queue "$tid" --ok --detail "Listed for Claude sutando-core" --component watch-tasks
    done
    break
  fi
  # False trigger — brief pause before re-watching
  sleep 2
done
