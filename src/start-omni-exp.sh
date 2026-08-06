#!/usr/bin/env bash
# Start omni-exp (experimental phone HTML + Qwen Omni Realtime).
# Separate from voice-agent's omni/webcam path.
#
# Usage:
#   bash src/start-omni-exp.sh           # foreground (dev)
#   bash src/start-omni-exp.sh --daemon  # launchd KeepAlive (preferred)
#
# Phone URL is HTTPS only: https://127.0.0.1:7090/omni-exp
#
# Root cause of past "dies after a minute" failures: agent/Cursor shells
# started omni inside their process group; shell exit → SIGTERM → silent
# death (no Python traceback). --daemon installs/uses launchd instead.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

DAEMON=0
if [[ "${1:-}" == "--daemon" ]]; then
  DAEMON=1
fi

PYTHON="${OMNI_EXP_PYTHON:-${OMNI_PYTHON:-$REPO/.venv/bin/python}}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

if ! "$PYTHON" -c "import aiohttp, PIL, dotenv" 2>/dev/null; then
  echo "Installing omni-exp deps into $PYTHON …"
  "$PYTHON" -m pip install -r "$REPO/requirements-omni-exp.txt"
fi

CERT="$REPO/state/server.crt"
KEY="$REPO/state/server.key"
if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
  mkdir -p "$REPO/state"
  echo "Generating self-signed TLS cert for phone getUserMedia…"
  openssl req -x509 -newkey rsa:2048 -keyout "$KEY" -out "$CERT" -days 365 -nodes -subj "/CN=sutando-local"
fi

if [[ -f "$REPO/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO/.env"
  set +a
fi

export OMNI_EXP_PORT="${OMNI_EXP_PORT:-${OMNI_PORT:-7090}}"
export OMNI_PORT="$OMNI_EXP_PORT"
WS="$(bash "$REPO/scripts/sutando-config.sh" workspace)"
mkdir -p "$WS/logs" "$WS/state"

echo "Omni-exp → https://127.0.0.1:${OMNI_EXP_PORT}/omni-exp  (HTTPS only)"

if [[ "$DAEMON" == "1" ]]; then
  INSTALLER="$REPO/src/install-omni-exp-launchd.sh"
  LABEL="com.sutando.omni-exp-agent"
  SERVICE="gui/$(id -u)/$LABEL"
  if [[ -f "$INSTALLER" ]]; then
    if launchctl print "$SERVICE" >/dev/null 2>&1; then
      launchctl kickstart -k "$SERVICE" 2>/dev/null || bash "$INSTALLER" install
    else
      bash "$INSTALLER" install
    fi
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
      lsof -nP -iTCP:"$OMNI_EXP_PORT" -sTCP:LISTEN >/dev/null 2>&1 && break
      sleep 0.3
    done
    if lsof -nP -iTCP:"$OMNI_EXP_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "launchd KeepAlive ✓  (bash src/install-omni-exp-launchd.sh --status)"
    else
      echo "WARN: launchd loaded but port not up — see $WS/logs/omni-exp-agent.log" >&2
      exit 1
    fi
    exit 0
  fi
  echo "ERROR: $INSTALLER missing — cannot daemonize safely" >&2
  exit 1
fi

exec "$PYTHON" -u "$REPO/src/omni-exp-agent.py"
