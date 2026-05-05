#!/bin/bash
# Restart all LiveKit services
# Usage: bash src/restart-livekit.sh

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

echo "Restarting LiveKit services..."
echo ""

# Stop all services
echo "Stopping services..."
bash src/start-livekit.sh --stop

# Wait for processes to fully terminate
sleep 2

# Start all services
echo ""
echo "Starting services..."
bash src/start-livekit.sh

echo ""
echo "✓ LiveKit services restarted"
