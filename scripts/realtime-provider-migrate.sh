#!/usr/bin/env bash
# Realtime provider migration — phase tracking, verify, rollback.
# Durable state: <workspace>/state/realtime-provider-migration.json
#
# Usage:
#   bash scripts/realtime-provider-migrate.sh status
#   bash scripts/realtime-provider-migrate.sh init
#   bash scripts/realtime-provider-migrate.sh mark <phase> complete|in_progress|rolled_back [notes]
#   bash scripts/realtime-provider-migrate.sh verify <phase>
#   bash scripts/realtime-provider-migrate.sh rollback
#
# Rollback (instant, no script required):
#   REALTIME_PROVIDER=gemini REALTIME_USE_FACTORY=1 REALTIME_VISION_ADAPTER=0
#   Legacy inline transport: REALTIME_USE_FACTORY=0

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE="$(bash "$REPO/scripts/sutando-config.sh" workspace)"
STATE_FILE="$WORKSPACE/state/realtime-provider-migration.json"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

init_state() {
  mkdir -p "$WORKSPACE/state"
  if [[ -f "$STATE_FILE" ]]; then
    echo "Migration state already exists: $STATE_FILE"
    return 0
  fi
  cat > "$STATE_FILE" << EOF
{
  "schema_version": 1,
  "updated_at": "$(ts)",
  "current_phase": 1,
  "phases": {
    "0": { "status": "complete", "completed_at": "$(ts)", "notes": "Vendor spikes" },
    "1": { "status": "pending" },
    "2": { "status": "pending" },
    "3": { "status": "pending" },
    "4": { "status": "pending" }
  },
  "rollback": {
    "provider": "gemini",
    "use_factory": true,
    "vision_adapter": false
  }
}
EOF
  echo "Initialized $STATE_FILE"
}

cmd="${1:-status}"

case "$cmd" in
  status)
    echo "=== Realtime provider migration ==="
    echo "Workspace: $WORKSPACE"
    echo "State file: $STATE_FILE"
    if [[ -f "$STATE_FILE" ]]; then
      python3 -m json.tool "$STATE_FILE" 2>/dev/null || cat "$STATE_FILE"
    else
      echo "(no state file — run: bash scripts/realtime-provider-migrate.sh init)"
    fi
    echo ""
    echo "=== Current .env (if set) ==="
    grep -E '^REALTIME_|^DASHSCOPE_|^QWEN_' "$REPO/.env" 2>/dev/null || echo "(no REALTIME_* vars in .env)"
    ;;

  init)
    init_state
    ;;

  mark)
    phase="${2:?phase number required}"
    status="${3:?status required (complete|in_progress|rolled_back|pending|skipped)}"
    notes="${4:-}"
    init_state
    python3 << PY
import json
from pathlib import Path
from datetime import datetime, timezone

path = Path("$STATE_FILE")
state = json.loads(path.read_text())
now = datetime.now(timezone.utc).isoformat()
rec = state.setdefault("phases", {}).setdefault("$phase", {"status": "pending"})
rec["status"] = "$status"
if "$notes":
    rec["notes"] = "$notes"
if "$status" == "in_progress" and "started_at" not in rec:
    rec["started_at"] = now
if "$status" in ("complete", "rolled_back"):
    rec["completed_at"] = now
if "$status" == "complete":
    p = int("$phase")
    if p >= state.get("current_phase", 1):
        state["current_phase"] = min(p + 1, 4)
state["updated_at"] = now
path.write_text(json.dumps(state, indent=2) + "\n")
print(f"Marked phase $phase -> $status")
PY
    ;;

  verify)
    phase="${2:?phase number required}"
    echo "Verifying phase $phase..."
    case "$phase" in
      0)
        if [[ -x "$REPO/.venv-livekit/bin/python" ]]; then
          PY="$REPO/.venv-livekit/bin/python"
        else
          PY=python3
        fi
        echo "=== Phase 0: tools ==="
        "$PY" "$REPO/scripts/test-qwen-realtime-tools.py" --timeout-s 45
        echo "=== Phase 0: audio ==="
        "$PY" "$REPO/scripts/test-qwen-realtime-audio.py" --timeout-s 45
        echo "=== Phase 0: vision ==="
        "$PY" "$REPO/scripts/test-qwen-realtime-vision.py" --timeout-s 45
        ;;
      1)
        npm run typecheck --prefix "$REPO" 2>/dev/null || (cd "$REPO" && npx tsc --noEmit)
        cd "$REPO" && npx tsx --test tests/realtime-provider-config.test.ts tests/realtime-provider-vision-adapter.test.ts tests/realtime-provider-errors.test.ts
        python3 "$REPO/tests/realtime-provider-factory.test.py"
        ;;
      2)
        cd "$REPO" && npx tsx --test tests/realtime-provider-vision-adapter.test.ts tests/realtime-provider-errors.test.ts
        if [[ -x "$REPO/.venv-livekit/bin/python" ]]; then PY="$REPO/.venv-livekit/bin/python"; else PY=python3; fi
        "$PY" "$REPO/scripts/test-qwen-realtime-vision.py" --timeout-s 45
        ;;
      3)
        bash "$REPO/scripts/test-realtime-provider-e2e.sh"
        ;;
      *)
        echo "No automated verify for phase $phase"
        exit 0
        ;;
    esac
    bash "$0" mark "$phase" complete "verified $(ts)"
    ;;

  rollback)
    echo "=== Rollback to safe defaults ==="
    echo "Add or set in .env:"
    echo "  REALTIME_PROVIDER=gemini"
    echo "  REALTIME_USE_FACTORY=1"
    echo "  REALTIME_VISION_ADAPTER=0"
    echo ""
    echo "Legacy inline Qwen (pre-factory): REALTIME_USE_FACTORY=0 REALTIME_PROVIDER=qwen"
    echo "Then restart voice-agent / livekit-agent."
    bash "$0" mark 1 rolled_back "operator rollback $(ts)" 2>/dev/null || true
    ;;

  *)
    echo "Unknown command: $cmd"
    echo "Commands: status | init | mark | verify | rollback"
    exit 1
    ;;
esac
