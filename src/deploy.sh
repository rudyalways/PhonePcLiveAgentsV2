#!/bin/bash
# Manage all LiveKit services (start / stop / restart).
# Usage:
#   bash src/deploy.sh                         # clean restart all services
#   bash src/deploy.sh --stop_service          # LiveKit / voice / web stack only
#   bash src/deploy.sh --stop_core_and_background  # sutando-core (tmux + agent) + task watchers only
#   bash src/deploy.sh --stop                  # --stop_service then --stop_core_and_background
#   bash src/deploy.sh --restart               # same as default: clean restart all services
# Add user: python3 src/add-user.py <username> <secret>

set -e  # Exit on error

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

VENV="$REPO/.venv-livekit"
PYTHON="$VENV/bin/python3"

ensure_node_22() {
  local major
  major="$(node -p "process.versions.node.split('.')[0]" 2>/dev/null || echo 0)"
  [ "$major" -ge 22 ] && return 0
  if [ -n "${NVM_DIR:-}" ] && [ -s "${NVM_DIR}/nvm.sh" ]; then
    # shellcheck source=/dev/null
    . "${NVM_DIR}/nvm.sh"
    nvm use 22 >/dev/null 2>&1 || true
  fi
  major="$(node -p "process.versions.node.split('.')[0]" 2>/dev/null || echo 0)"
  if [ "$major" -lt 22 ]; then
    echo "  ✗ node $(node -v 2>/dev/null || echo '?') is too old — requires Node >=22 (node:sqlite)"
    echo "    nvm install 22 && nvm use 22"
    return 1
  fi
}

# Start a background service that survives deploy.sh exiting (nohup + disown).
start_detached() {
  local log="$1"
  shift
  : > "$log"
  nohup "$@" >> "$log" 2>&1 &
  disown -h "$!" 2>/dev/null || true
}

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

stop_sutando_core() {
  local socket="/tmp/sutando-tmux.sock"
  local had_session=0

  if command -v tmux > /dev/null 2>&1 &&
     tmux -S "$socket" has-session -t sutando-core 2>/dev/null; then
    had_session=1
    echo "  stopping sutando-core"
    tmux -S "$socket" kill-session -t sutando-core 2>/dev/null || true
  fi

  stop_process_strict "sutando-core agent" "claude --name sutando-core"
  stop_process_strict "macOS GUI control MCP" "mcp-server-macos-use"

  if [ "$had_session" -eq 1 ] &&
     [ -z "$(process_pids "claude --name sutando-core")" ] &&
     [ -z "$(process_pids "mcp-server-macos-use")" ]; then
    echo "  ✓ sutando-core stopped"
  fi
}

# Reads sutando-core tmux scrollback; if Claude Code shows auth / Remote Control failure, prompt the operator.
warn_if_sutando_core_needs_login() {
  local socket="/tmp/sutando-tmux.sock"
  local pane
  if ! command -v tmux > /dev/null 2>&1 || [ ! -S "$socket" ]; then
    return 0
  fi
  if ! tmux -S "$socket" has-session -t sutando-core 2>/dev/null; then
    return 0
  fi
  pane="$(tmux -S "$socket" capture-pane -t sutando-core -p -S -150 2>/dev/null || true)"
  if ! echo "$pane" | grep -qE 'Remote Control failed|Please run /login|API Error: 401|Invalid authentication credentials'; then
    return 0
  fi
  echo ""
  echo "⚠ sutando-core needs Claude Code login (or this tmux session has stale credentials)."
  echo "  Detected in tmux scrollback: Remote Control failed, /login prompt, or API 401."
  echo ""
  echo "  Next steps:"
  echo "    1) tmux -S $socket attach -t sutando-core"
  echo "    2) Run  /login   (claude.ai — same account as the Claude app for Remote Control)"
  echo "    3) Run  /proactive-loop"
  echo ""
  echo "  Or restart core after fixing auth:"
  echo "    bash src/deploy.sh --stop_core_and_background && bash src/deploy.sh"
  echo ""
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
  for _ in $(seq 1 160); do
    if pgrep -f "src/livekit-agent.py start" > /dev/null 2>&1 &&
       lsof -nP -iTCP:7082 -sTCP:LISTEN > /dev/null 2>&1; then
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
    start_detached "$log" "$@"
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

# Returns 0 if watch-tasks stream, tmux sutando-core, and related PIDs are gone.
verify_core_background_clear() {
  local socket="/tmp/sutando-tmux.sock"
  local fail=0
  echo "After —"
  if [ -n "$(process_pids "watch-tasks-stream.sh")" ]; then
    echo "  ✗ watch-tasks stream: still running"
    fail=1
  else
    echo "  ✓ watch-tasks stream: clear"
  fi
  if [ -n "$(process_pids "watch-tasks.sh")" ]; then
    echo "  ✗ watch-tasks (poll): still running"
    fail=1
  else
    echo "  ✓ watch-tasks (poll): clear"
  fi
  if command -v tmux > /dev/null 2>&1 && [ -S "$socket" ] &&
     tmux -S "$socket" has-session -t sutando-core 2>/dev/null; then
    echo "  ✗ tmux sutando-core: session still exists"
    fail=1
  else
    echo "  ✓ tmux sutando-core: no session"
  fi
  if [ -n "$(process_pids "claude --name sutando-core")" ]; then
    echo "  ✗ claude sutando-core: still running"
    fail=1
  else
    echo "  ✓ claude sutando-core: clear"
  fi
  if [ -n "$(process_pids "mcp-server-macos-use")" ]; then
    echo "  ✗ macOS GUI control MCP: still running"
    fail=1
  else
    echo "  ✓ macOS GUI control MCP: clear"
  fi
  return "$fail"
}

do_stop_service() {
  echo "Stopping LiveKit / voice / web services..."
  stop_process_strict "AI agent" "src/livekit-agent.py start"
  stop_process_strict "token server" "livekit-token-server.py"
  stop_process_strict "screen publisher server" "screen-publisher-server.py"
  stop_process_strict "mobile control server" "mobile-control-server.py"
  stop_process_strict "pipeline trace" "pipeline-trace.py"
  stop_process_strict "screen capture server" "screen-capture-server.py"
  stop_process_strict "voice agent / result watcher" "src/voice-agent.ts"
  stop_process_strict "web client" "src/web-client.ts"
  stop_process_strict "agent API" "src/agent-api.py"
  echo "LiveKit / voice / web services stopped."
}

do_stop() {
  do_stop_service
  echo ""
  do_stop_core_and_background
  echo ""
  echo "All Sutando services stopped."
}

do_stop_core_and_background() {
  local socket="/tmp/sutando-tmux.sock"
  local had_any=0
  local wt_pids cl_pids mcp_pids

  echo "sutando-core + task watchers (LiveKit stack unchanged)"
  echo ""
  echo "Before —"
  wt_pids="$(process_pids "watch-tasks-stream.sh")"
  if [ -n "$wt_pids" ]; then
    echo "  ◆ watch-tasks stream: running (pid(s): $(echo "$wt_pids" | tr '\n' ',' | sed 's/,$//'))"
    had_any=1
  else
    echo "  ○ watch-tasks stream: not running"
  fi

  local wt_poll_pids
  wt_poll_pids="$(process_pids "watch-tasks.sh")"
  if [ -n "$wt_poll_pids" ]; then
    echo "  ◆ watch-tasks (poll): running (pid(s): $(echo "$wt_poll_pids" | tr '\n' ',' | sed 's/,$//'))"
    had_any=1
  else
    echo "  ○ watch-tasks (poll): not running"
  fi

  if command -v tmux > /dev/null 2>&1 && [ -S "$socket" ] &&
     tmux -S "$socket" has-session -t sutando-core 2>/dev/null; then
    echo "  ◆ tmux sutando-core: session present ($socket)"
    had_any=1
  elif [ -S "$socket" ]; then
    echo "  ○ tmux sutando-core: no session sutando-core (socket still present)"
  else
    echo "  ○ tmux sutando-core: no server on $socket"
  fi

  cl_pids="$(process_pids "claude --name sutando-core")"
  if [ -n "$cl_pids" ]; then
    echo "  ◆ claude sutando-core: running (pid(s): $(echo "$cl_pids" | tr '\n' ',' | sed 's/,$//'))"
    had_any=1
  else
    echo "  ○ claude sutando-core: not running"
  fi

  mcp_pids="$(process_pids "mcp-server-macos-use")"
  if [ -n "$mcp_pids" ]; then
    echo "  ◆ macOS GUI control MCP: running (pid(s): $(echo "$mcp_pids" | tr '\n' ',' | sed 's/,$//')) (stopped with sutando-core)"
    had_any=1
  else
    echo "  ○ macOS GUI control MCP: not running"
  fi

  if [ "$had_any" -eq 0 ]; then
    echo ""
    echo "Hint: nothing matched — already idle (no stop needed)."
    echo ""
    if ! verify_core_background_clear; then
      echo ""
      echo "Unexpected: reported idle but verification failed — check the items above."
      exit 1
    fi
    echo ""
    echo "OK — verified idle."
    return 0
  fi

  echo ""
  echo "Stopping..."
  stop_process_strict "watch-tasks stream" "watch-tasks-stream.sh"
  stop_process_strict "watch-tasks" "watch-tasks.sh"
  stop_sutando_core
  echo ""
  if ! verify_core_background_clear; then
    echo ""
    echo "Stop incomplete — fix the items above (or run bash src/deploy.sh --stop)."
    exit 1
  fi
  echo ""
  echo "OK — sutando-core + task watchers stopped and verified."
}

if [ "$1" = "--stop" ]; then
  do_stop
  exit 0
fi

if [ "$1" = "--stop_service" ]; then
  do_stop_service
  exit 0
fi

if [ "$1" = "--stop_core_and_background" ]; then
  do_stop_core_and_background
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
  echo "Usage: bash src/deploy.sh [--restart|--stop|--stop_service|--stop_core_and_background|--logs]"
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
if ! ensure_node_22; then missing=1; fi
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
if ! lsof -i :7980 -sTCP:LISTEN > /dev/null 2>&1; then
  PORT=7980 HOST=0.0.0.0 start_detached logs/voice-agent.log npx tsx src/voice-agent.ts
  echo "  ✓ voice agent / result watcher (port 7980)"
else
  echo "  ✓ voice agent / result watcher (already running)"
fi
wait_for_port_service 7980 "voice agent / result watcher" "voice-agent.ts" "logs/voice-agent.log" || exit 1

if ! lsof -i :7080 -sTCP:LISTEN > /dev/null 2>&1; then
  CLIENT_PORT=7080 PORT=7980 start_detached logs/web-client.log npx tsx src/web-client.ts
  echo "  ✓ web client (port 7080)"
else
  echo "  ✓ web client (already running)"
fi
wait_for_port_service 7080 "web client" "web-client.ts" "logs/web-client.log" || exit 1

AGENT_API_PORT="${AGENT_API_PORT:-7950}"
if ! lsof -i :"$AGENT_API_PORT" -sTCP:LISTEN > /dev/null 2>&1; then
  start_detached logs/agent-api.log env AGENT_API_PORT="$AGENT_API_PORT" python3 src/agent-api.py
  echo "  ✓ agent API (port $AGENT_API_PORT)"
else
  echo "  ✓ agent API (already running on $AGENT_API_PORT)"
fi
wait_for_port_service "$AGENT_API_PORT" "agent API" "agent-api.py" "logs/agent-api.log" || exit 1

# Task watcher runs INSIDE sutando-core via Monitor (schedule-crons skill).
# Do NOT start watch-tasks-stream.sh here — a detached watcher writes to a
# log file the core never reads, and its PID file makes /schedule-crons skip
# the Monitor launch (skills/schedule-crons/SKILL.md step 5).

export CLIENT_PORT=7081  # HTTPS screen/mobile publisher (web UI hardcoded to 7080 above)
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
  start_detached logs/livekit-agent.log "$PYTHON" src/livekit-agent.py start
  echo "  ✓ AI agent (worker mode)"
else
  echo "  ✓ AI agent (already running)"
fi
wait_for_livekit_agent || exit 1

sleep 3
echo ""
echo "Verifying services..."
VERIFY_PORTS="7850:token-server 7081:screen-publisher 7901:mobile-control 7900:screen-capture 7902:pipeline-trace"
all_ok=1
verify_port_service 7850 "token-server" "livekit-token-server.py" "logs/livekit-token-server.log"
verify_port_service "$CLIENT_PORT" "screen-publisher" "screen-publisher-server.py" "logs/screen-publisher-server.log"
verify_port_service 7901 "mobile-control" "mobile-control-server.py" "logs/mobile-control.log"
verify_port_service 7900 "screen-capture" "screen-capture-server.py" "logs/screen-capture.log"
verify_port_service 7902 "pipeline-trace" "pipeline-trace.py" "logs/pipeline-trace.log"
verify_port_service 7980 "voice-agent / result watcher" "voice-agent.ts" "logs/voice-agent.log"
verify_port_service 7080 "web-client" "web-client.ts" "logs/web-client.log"
verify_port_service "${AGENT_API_PORT:-7950}" "agent API" "agent-api.py" "logs/agent-api.log"

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
export SUTANDO_CLAUDE_WORKING_DIR="$REPO"
export SUTANDO_ACCEPT_BYPASS_PERMISSIONS=1
if bash src/agent/claude/cli/start-cli.sh --restart; then
  echo "  ✓ sutando-core started (canonical start-cli.sh)"
  echo "    Attach with: tmux -S /tmp/sutando-tmux.sock attach -t sutando-core"
else
  echo "  ✗ sutando-core failed to start — check logs and tmux attach"
  all_ok=0
fi
sleep 6
warn_if_sutando_core_needs_login

echo ""
echo "Done. Logs in logs/. Stop with: bash src/deploy.sh --stop (--stop_service / --stop_core_and_background for partial stops)"
