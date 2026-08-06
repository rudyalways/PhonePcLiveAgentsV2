#!/bin/bash
# Wrapper for launchd-managed omni-agent (com.sutando.omni-exp-agent).
# Do NOT use `set -e` — pkill/lsof non-zero statuses must not abort KeepAlive.
set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

WS="$(bash "$REPO_ROOT/scripts/sutando-config.sh" workspace 2>/dev/null || true)"
[[ -n "${WS:-}" ]] || WS="$REPO_ROOT/workspace"
mkdir -p "$WS/logs" "$REPO_ROOT/state"
DBG="$WS/logs/omni-exp-launchd-wrapper.log"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >>"$DBG"; }

log "start wrapper pid=$$ repo=$REPO_ROOT"

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  # shellcheck disable=SC1090
  source "$REPO_ROOT/.env" || log "WARN: sourcing .env failed"
  set +a
fi

PORT="${OMNI_EXP_PORT:-${OMNI_EXP_PORT:-${OMNI_PORT:-7090}}}"
PYTHON="${OMNI_EXP_PYTHON:-${OMNI_PYTHON:-$REPO_ROOT/.venv/bin/python}}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 || true)"
fi
if [[ -z "${PYTHON:-}" || ! -x "$PYTHON" ]]; then
  log "ERROR: no python interpreter"
  exit 1
fi
log "python=$PYTHON port=$PORT"

CERT="$REPO_ROOT/state/server.crt"
KEY="$REPO_ROOT/state/server.key"
if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
  /usr/bin/openssl req -x509 -newkey rsa:2048 -keyout "$KEY" -out "$CERT" \
    -days 365 -nodes -subj "/CN=sutando-local" >>"$DBG" 2>&1 || true
fi

# Evict stale listeners (manual/agent starts). Ignore failures.
if command -v lsof >/dev/null 2>&1; then
  stale="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -n "${stale:-}" ]]; then
    log "evicting stale pids: $stale"
    # shellcheck disable=SC2086
    kill $stale 2>/dev/null || true
    sleep 0.5
    # shellcheck disable=SC2086
    kill -9 $stale 2>/dev/null || true
    sleep 0.2
  fi
fi
pkill -f "$REPO_ROOT/src/omni-exp-supervisor.sh" 2>/dev/null || true

log "exec omni-exp-agent.py"
exec "$PYTHON" -u "$REPO_ROOT/src/omni-exp-agent.py"
