#!/bin/bash
# Start all LiveKit services (token server, screen publisher, AI agent).
# Usage: bash src/start-livekit.sh
# Stop:  bash src/start-livekit.sh --stop

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

if [ "$1" = "--stop" ]; then
  pkill -f "livekit-token-server" 2>/dev/null
  pkill -f "screen-publisher-server" 2>/dev/null
  pkill -f "livekit-agent" 2>/dev/null
  echo "All LiveKit services stopped."
  exit 0
fi

set -a; [ -f .env ] && source .env; set +a

echo "Starting LiveKit services..."

if ! lsof -i :7850 > /dev/null 2>&1; then
  python3 src/livekit-token-server.py > logs/livekit-token-server.log 2>&1 &
  echo "  ✓ token server (port 7850)"
else
  echo "  ✓ token server (already running)"
fi

if ! lsof -i :8080 > /dev/null 2>&1; then
  python3 src/screen-publisher-server.py > logs/screen-publisher-server.log 2>&1 &
  echo "  ✓ screen publisher server (port 8080)"
else
  echo "  ✓ screen publisher server (already running)"
fi

sleep 1

if ! pgrep -f "livekit-agent" > /dev/null 2>&1; then
  python3 src/livekit-agent.py > logs/livekit-agent.log 2>&1 &
  echo "  ✓ AI agent"
else
  echo "  ✓ AI agent (already running)"
fi

echo "Done. Logs in logs/. Stop with: bash src/start-livekit.sh --stop"
