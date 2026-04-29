#!/bin/bash
# Start all LiveKit services (token server, screen publisher, AI agent worker).
# Usage: bash src/start-livekit.sh
# Stop:  bash src/start-livekit.sh --stop
# Add user: python3 src/add-user.py <username> <secret>

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

VENV="$REPO/.venv-livekit"
PYTHON="$VENV/bin/python3"

# Auto-create venv + install deps if missing
if [ ! -f "$PYTHON" ]; then
  echo "Creating LiveKit venv..."
  python3 -m venv "$VENV"
  "$PYTHON" -m pip install --upgrade pip -q
  "$PYTHON" -m pip install -r requirements-livekit.txt -q
  echo "  ✓ venv ready"
fi

if [ "$1" = "--stop" ]; then
  pkill -f "livekit-token-server" 2>/dev/null
  pkill -f "screen-publisher-server" 2>/dev/null
  pkill -f "livekit-agent" 2>/dev/null
  echo "All LiveKit services stopped."
  exit 0
fi

set -a; [ -f .env ] && source .env; set +a
mkdir -p logs

# Check users are configured
USER_COUNT=$(python3 -c "
import json, sys
try:
    d = json.load(open('src/users.json'))
    print(sum(1 for k in d if not k.startswith('_')))
except: print(0)
" 2>/dev/null)
if [ "${USER_COUNT:-0}" = "0" ]; then
  echo "  ⚠ No users configured. Add one first:"
  echo "    python3 src/add-user.py <username> <secret>"
  echo ""
fi

echo "Starting LiveKit services..."

if ! lsof -i :7850 > /dev/null 2>&1; then
  "$PYTHON" src/livekit-token-server.py > logs/livekit-token-server.log 2>&1 &
  echo "  ✓ token server (port 7850)"
else
  echo "  ✓ token server (already running)"
fi

if ! lsof -i :8080 > /dev/null 2>&1; then
  "$PYTHON" src/screen-publisher-server.py > logs/screen-publisher-server.log 2>&1 &
  echo "  ✓ screen publisher server (port 8080)"
else
  echo "  ✓ screen publisher server (already running)"
fi

sleep 1

# Agent runs in worker mode — LiveKit Cloud dispatches jobs per room.
# Credentials passed via CLI flags (values from .env loaded above).
if ! pgrep -f "livekit-agent" > /dev/null 2>&1; then
  "$PYTHON" src/livekit-agent.py start \
    > logs/livekit-agent.log 2>&1 &
  echo "  ✓ AI agent (worker mode)"
else
  echo "  ✓ AI agent (already running)"
fi

echo "Done. Logs in logs/. Stop with: bash src/start-livekit.sh --stop"
