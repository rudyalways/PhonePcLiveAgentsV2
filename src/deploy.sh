#!/bin/bash
# Manage all LiveKit services (start / stop / restart).
# Usage:
#   bash src/deploy.sh            # clean restart all services
#   bash src/deploy.sh --stop     # stop all services
#   bash src/deploy.sh --restart  # same as default: clean restart all services
# Add user: python3 src/add-user.py <username> <secret>

set -e  # Exit on error

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

VENV="$REPO/.venv-livekit"
PYTHON="$VENV/bin/python3"

port_listener_pids() {
  lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

port_owned_by() {
  local port="$1"
  local pattern="$2"
  local pid cmd
  while IFS= read -r pid; do
    [ -z "$pid" ] && continue
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [[ "$cmd" == *"$pattern"* ]]; then
      return 0
    fi
  done < <(port_listener_pids "$port")
  return 1
}

describe_port_listener() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | sed 's/^/    /' || true
}

process_pids() {
  local pattern="$1"
  pgrep -f "$pattern" 2>/dev/null || true
}

wait_for_process_exit() {
  local pattern="$1"
  local timeout_s="${2:-10}"
  local deadline=$((SECONDS + timeout_s))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if [ -z "$(process_pids "$pattern")" ]; then
      return 0
    fi
    sleep 0.25
  done
  [ -z "$(process_pids "$pattern")" ]
}

stop_process_strict() {
  local name="$1"
  local pattern="$2"
  local pids

  pids="$(process_pids "$pattern")"
  if [ -z "$pids" ]; then
    return 0
  fi

  echo "  stopping $name"
  pkill -TERM -f "$pattern" 2>/dev/null || true
  if wait_for_process_exit "$pattern" 10; then
    echo "  ✓ $name stopped"
    return 0
  fi

  echo "  ⚠ $name still exiting; forcing stop"
  pkill -KILL -f "$pattern" 2>/dev/null || true
  if wait_for_process_exit "$pattern" 5; then
    echo "  ✓ $name stopped"
    return 0
  fi

  echo "  ✗ $name did not stop cleanly:"
  process_pids "$pattern" | while IFS= read -r pid; do
    [ -z "$pid" ] && continue
    ps -p "$pid" -o pid,command= 2>/dev/null | sed 's/^/    /' || true
  done
  exit 1
}

wait_for_port_service() {
  local port="$1"
  local name="$2"
  local pattern="$3"
  local log="$4"
  for _ in $(seq 1 40); do
    if port_owned_by "$port" "$pattern"; then
      return 0
    fi
    sleep 0.25
  done
  echo "  ✗ $name did not become ready on port $port — check $log"
  return 1
}

wait_for_livekit_agent() {
  for _ in $(seq 1 80); do
    if pgrep -f "src/livekit-agent.py start" > /dev/null 2>&1 &&
       lsof -nP -iTCP:8082 -sTCP:LISTEN > /dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  echo "  ✗ AI agent did not become ready — check logs/livekit-agent.log"
  return 1
}

start_port_service() {
  local port="$1"
  local name="$2"
  local pattern="$3"
  local log="$4"
  local conflict_hint="$5"
  shift 5

  if ! lsof -i :"$port" -sTCP:LISTEN > /dev/null 2>&1; then
    "$@" > "$log" 2>&1 &
    echo "  ✓ $name (port $port)"
  elif port_owned_by "$port" "$pattern"; then
    echo "  ✓ $name (already running on port $port)"
  else
    echo "  ✗ $name port $port is occupied by a different process:"
    describe_port_listener "$port"
    echo "    $conflict_hint"
  fi
}

verify_port_service() {
  local port="$1"
  local name="$2"
  local pattern="$3"
  local log="$4"

  if port_owned_by "$port" "$pattern"; then
    echo "  ✓ $name (port $port)"
  elif lsof -i :"$port" -sTCP:LISTEN > /dev/null 2>&1; then
    echo "  ✗ $name (port $port) is occupied by a different process:"
    describe_port_listener "$port"
    all_ok=0
  else
    echo "  ✗ $name (port $port) — check $log"
    all_ok=0
  fi
}

# TODO: auto-detect macOS vs Linux and choose the native supervisor
# (launchd/systemd) when deployment targets expand beyond this Mac runtime.

# ── Stop ──────────────────────────────────────────────────────────────────────

do_stop() {
  stop_process_strict "AI agent" "src/livekit-agent.py start"
  stop_process_strict "token server" "livekit-token-server.py"
  stop_process_strict "screen publisher server" "screen-publisher-server.py"
  stop_process_strict "mobile control server" "mobile-control-server.py"
  stop_process_strict "pipeline trace" "pipeline-trace.py"
  stop_process_strict "screen capture server" "screen-capture-server.py"
  stop_process_strict "voice agent / result watcher" "src/voice-agent.ts"
  stop_process_strict "web client" "src/web-client.ts"
  stop_process_strict "watch-tasks stream" "watch-tasks-stream.sh"
  stop_process_strict "watch-tasks" "watch-tasks.sh"
  echo "All Sutando services stopped."
}

if [ "$1" = "--stop" ]; then
  do_stop
  exit 0
fi

if [ "$1" = "--logs" ]; then
  mkdir -p "$REPO/logs"
  LOGS=(
    "$REPO/logs/livekit-agent.log"
    "$REPO/logs/livekit-token-server.log"
    "$REPO/logs/screen-publisher-server.log"
    "$REPO/logs/mobile-control.log"
    "$REPO/logs/screen-capture.log"
    "$REPO/logs/pipeline-trace.log"
  )
  # Create files if they don't exist yet so tail doesn't error
  for f in "${LOGS[@]}"; do touch "$f"; done
  echo "Following logs (Ctrl-C to exit):"
  for f in "${LOGS[@]}"; do echo "  ${f##*/}"; done
  echo ""
  tail -f "${LOGS[@]}"
  exit 0
fi

if [ "$1" = "--restart" ]; then
  echo "Restarting LiveKit services..."
elif [ "${1:-}" = "" ]; then
  echo "Clean restarting LiveKit services..."
else
  echo "Unknown option: $1"
  echo "Usage: bash src/deploy.sh [--restart|--stop|--logs]"
  exit 1
fi
echo ""
echo "Stopping services..."
do_stop
echo ""
echo "Starting services..."
echo ""

# ── Start ─────────────────────────────────────────────────────────────────────

# Check prerequisites
echo "Checking prerequisites..."
missing=0
if ! command -v python3 > /dev/null 2>&1; then echo "  ✗ python3 not found"; missing=1; fi
if ! command -v lsof > /dev/null 2>&1; then echo "  ✗ lsof not found"; missing=1; fi
if ! command -v node > /dev/null 2>&1; then echo "  ✗ node not found — brew install node"; missing=1; fi
if ! command -v npx > /dev/null 2>&1; then echo "  ✗ npx not found — comes with node"; missing=1; fi
if ! command -v claude > /dev/null 2>&1; then
  echo "  ✗ claude not found — see https://docs.anthropic.com/en/docs/claude-code/getting-started"
  missing=1
fi
if ! command -v fswatch > /dev/null 2>&1; then
  if command -v brew > /dev/null 2>&1; then
    echo "  ⚠ fswatch not found — installing via Homebrew..."
    brew install fswatch
    if command -v fswatch > /dev/null 2>&1; then
      echo "  ✓ fswatch installed"
    else
      echo "  ✗ fswatch installation failed"; missing=1
    fi
  else
    echo "  ✗ fswatch not found — brew install fswatch"; missing=1
  fi
fi
if [ $missing -eq 1 ]; then echo ""; echo "Fix the above and try again."; exit 1; fi
echo "  ✓ All prerequisites found"
echo ""

# Auto-create venv if missing
if [ ! -f "$PYTHON" ]; then
  echo "Creating LiveKit venv..."
  python3 -m venv "$VENV"
  "$PYTHON" -m pip install --upgrade pip -q
fi

# Check and install dependencies every time
echo "Checking Python dependencies..."
"$PYTHON" -m pip install -r requirements-livekit.txt -q
echo "  ✓ Dependencies ready"

# Validate .env and required environment variables
REQUESTED_CLIENT_PORT="${CLIENT_PORT:-}"

if [ ! -f .env ]; then
  echo "  ✗ .env not found — cp .env.example .env and add your keys"
  exit 1
fi

set -a; [ -f .env ] && source .env; set +a
if [ -n "$REQUESTED_CLIENT_PORT" ]; then CLIENT_PORT="$REQUESTED_CLIENT_PORT"; fi

missing=0
if [ -z "$LIVEKIT_URL" ]; then echo "  ✗ LIVEKIT_URL not set in .env"; missing=1; fi
if [ -z "$LIVEKIT_API_KEY" ]; then echo "  ✗ LIVEKIT_API_KEY not set in .env"; missing=1; fi
if [ -z "$LIVEKIT_API_SECRET" ]; then echo "  ✗ LIVEKIT_API_SECRET not set in .env"; missing=1; fi
if [ -z "$GEMINI_API_KEY" ]; then echo "  ✗ GEMINI_API_KEY not set in .env"; missing=1; fi
if [ $missing -eq 1 ]; then echo ""; echo "Add missing keys to .env and try again."; exit 1; fi
echo "  ✓ Environment variables validated"
echo ""

# Create all necessary directories
mkdir -p logs state tasks results data
echo "  ✓ Directories created"
echo ""

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

# Check macOS permissions
echo "Checking macOS permissions..."
if ! screencapture -x /tmp/livekit-permcheck.png 2>/dev/null; then
  echo "  ⚠ Screen Recording not granted (needed for screen-publisher-server)"
  echo "    → System Settings → Privacy & Security → Screen & System Audio Recording"
  echo "    → Add 'python3'"
else
  rm -f /tmp/livekit-permcheck.png
  echo "  ✓ Screen Recording"
fi
echo ""

# Prevent display sleep (important for always-on operation)
if ! pgrep -q caffeinate; then
  caffeinate -d -i -s &
  echo "  ✓ caffeinate started (prevents display sleep)"
else
  echo "  ✓ caffeinate already running"
fi
echo ""

# Install Claude Code skills (needed by sutando-core)
echo "Installing skills..."
bash "$REPO/skills/install.sh" 2>/dev/null || true
echo ""

# Archive stale results (>24h) to prevent backlog
python3 "$REPO/src/archive-stale-results.py" 2>/dev/null || true

echo "Starting voice services..."
if ! lsof -i :9900 -sTCP:LISTEN > /dev/null 2>&1; then
  PORT=9900 HOST=0.0.0.0 npx tsx src/voice-agent.ts > logs/voice-agent.log 2>&1 &
  echo "  ✓ voice agent / result watcher (port 9900)"
else
  echo "  ✓ voice agent / result watcher (already running)"
fi
wait_for_port_service 9900 "voice agent / result watcher" "voice-agent.ts" "logs/voice-agent.log" || exit 1

if ! lsof -i :8080 -sTCP:LISTEN > /dev/null 2>&1; then
  CLIENT_PORT=8080 PORT=9900 npx tsx src/web-client.ts > logs/web-client.log 2>&1 &
  echo "  ✓ web client (port 8080)"
else
  echo "  ✓ web client (already running)"
fi
wait_for_port_service 8080 "web client" "web-client.ts" "logs/web-client.log" || exit 1

export CLIENT_PORT="${CLIENT_PORT:-8081}"  # Avoid conflict with startup.sh's web-client (port 8080)
echo ""

echo "Starting LiveKit services..."

start_port_service 7850 "token server" "livekit-token-server.py" "logs/livekit-token-server.log" \
  "Stop the conflicting process, then rerun deploy." \
  "$PYTHON" src/livekit-token-server.py
wait_for_port_service 7850 "token server" "livekit-token-server.py" "logs/livekit-token-server.log" || exit 1

start_port_service "$CLIENT_PORT" "screen publisher server" "screen-publisher-server.py" "logs/screen-publisher-server.log" \
  "Stop the conflicting process, or rerun with CLIENT_PORT=<free-port> bash src/deploy.sh." \
  "$PYTHON" src/screen-publisher-server.py
wait_for_port_service "$CLIENT_PORT" "screen publisher server" "screen-publisher-server.py" "logs/screen-publisher-server.log" || exit 1

start_port_service 7901 "mobile control server" "mobile-control-server.py" "logs/mobile-control.log" \
  "Stop the conflicting process, then rerun deploy." \
  "$PYTHON" src/mobile-control-server.py
wait_for_port_service 7901 "mobile control server" "mobile-control-server.py" "logs/mobile-control.log" || exit 1

start_port_service 7900 "screen capture server" "screen-capture-server.py" "logs/screen-capture.log" \
  "Stop the conflicting process, then rerun deploy." \
  "$PYTHON" src/screen-capture-server.py
wait_for_port_service 7900 "screen capture server" "screen-capture-server.py" "logs/screen-capture.log" || exit 1

start_port_service 7902 "pipeline trace" "pipeline-trace.py" "logs/pipeline-trace.log" \
  "Stop the conflicting process, then rerun deploy." \
  python3 skills/pipeline-trace/scripts/pipeline-trace.py
wait_for_port_service 7902 "pipeline trace" "pipeline-trace.py" "logs/pipeline-trace.log" || exit 1

sleep 1

# Agent runs in worker mode — LiveKit Cloud dispatches jobs per room.
if ! pgrep -f "src/livekit-agent.py start" > /dev/null 2>&1; then
  "$PYTHON" src/livekit-agent.py start \
    > logs/livekit-agent.log 2>&1 &
  echo "  ✓ AI agent (worker mode)"
else
  echo "  ✓ AI agent (already running)"
fi
wait_for_livekit_agent || exit 1

sleep 3
echo ""
echo "Verifying services..."
VERIFY_PORTS="7850:token-server 8081:screen-publisher 7901:mobile-control 7900:screen-capture 7902:pipeline-trace"
all_ok=1
verify_port_service 7850 "token-server" "livekit-token-server.py" "logs/livekit-token-server.log"
verify_port_service "$CLIENT_PORT" "screen-publisher" "screen-publisher-server.py" "logs/screen-publisher-server.log"
verify_port_service 7901 "mobile-control" "mobile-control-server.py" "logs/mobile-control.log"
verify_port_service 7900 "screen-capture" "screen-capture-server.py" "logs/screen-capture.log"
verify_port_service 7902 "pipeline-trace" "pipeline-trace.py" "logs/pipeline-trace.log"
verify_port_service 9900 "voice-agent / result watcher" "voice-agent.ts" "logs/voice-agent.log"
verify_port_service 8080 "web-client" "web-client.ts" "logs/web-client.log"

# Check agent process (doesn't bind a port)
if pgrep -f "src/livekit-agent.py start" > /dev/null 2>&1; then
  echo "  ✓ AI agent (worker mode)"
else
  echo "  ✗ AI agent — check logs/livekit-agent.log"
  all_ok=0
fi

echo ""
if [ $all_ok -eq 1 ]; then
  echo "✓ All services running"
  echo "Open screen publisher: https://localhost:$CLIENT_PORT/"
else
  echo "⚠ Some services failed to start — check logs/"
fi

echo ""
echo "Checking sutando-core..."
if tmux -S /tmp/sutando-tmux.sock has-session -t sutando-core 2>/dev/null; then
  echo "  ✓ sutando-core is running"
else
  echo "  Starting sutando-core..."
  if command -v tmux > /dev/null 2>&1; then
    tmux -S /tmp/sutando-tmux.sock new-session -d -s sutando-core \
      "cd '$REPO' && claude --name sutando-core --remote-control 'Sutando' --dangerously-skip-permissions --add-dir '$HOME' -- '/proactive-loop'"
    echo "  ✓ sutando-core started in background"
    echo "    Attach with: tmux -S /tmp/sutando-tmux.sock attach -t sutando-core"
  else
    echo "  ✗ tmux not found — install with: brew install tmux"
    exit 1
  fi
fi

echo ""
echo "Done. Logs in logs/. Stop with: bash src/deploy.sh --stop"
