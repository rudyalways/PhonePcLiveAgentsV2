#!/usr/bin/env bash
# Phase 3 — factory + vendor spikes for Qwen opt-in path (no live voice-agent required).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

if [[ -x .venv-livekit/bin/python ]]; then PY=.venv-livekit/bin/python; else PY=python3; fi

echo "=== Phase 3 E2E: factory contract (gemini default) ==="
npx tsx --test tests/realtime-provider-config.test.ts tests/realtime-provider-vision-adapter.test.ts tests/realtime-provider-errors.test.ts
python3 tests/realtime-provider-factory.test.py

if grep -q DASHSCOPE_API_KEY .env 2>/dev/null; then
  echo "=== Phase 3 E2E: Qwen config + vendor path ==="
  npx tsx --test tests/realtime-provider-e2e.test.ts
  "$PY" scripts/test-qwen-realtime-tools.py --timeout-s 45
  "$PY" scripts/test-qwen-realtime-audio.py --timeout-s 45
else
  echo "SKIP: DASHSCOPE_API_KEY not in .env — Qwen vendor E2E skipped"
fi

echo "OK: Phase 3 E2E checks passed"
