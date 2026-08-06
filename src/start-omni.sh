#!/usr/bin/env bash
# Start the omni-agent (phone HTML + Qwen Omni Realtime).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

PYTHON="${OMNI_PYTHON:-$REPO/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

if ! "$PYTHON" -c "import aiohttp, PIL, dotenv" 2>/dev/null; then
  echo "Installing omni deps into $PYTHON …"
  "$PYTHON" -m pip install -r "$REPO/requirements-omni.txt"
fi

CERT="$REPO/state/server.crt"
KEY="$REPO/state/server.key"
if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
  mkdir -p "$REPO/state"
  echo "Generating self-signed TLS cert for phone getUserMedia…"
  openssl req -x509 -newkey rsa:2048 -keyout "$KEY" -out "$CERT" -days 365 -nodes -subj "/CN=sutando-local"
fi

export OMNI_PORT="${OMNI_PORT:-7090}"
echo "Omni agent → https://<this-host>:${OMNI_PORT}/omni"
exec "$PYTHON" "$REPO/src/omni-agent.py"
