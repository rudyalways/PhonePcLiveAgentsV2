#!/bin/bash
# Sync Claude Code proactive-loop skill symlink(s) with SUTANDO_PROACTIVE_LOOP_ENABLED.
#
# When disabled: unlink so /proactive-loop is not discoverable.
# When enabled: ensure symlink points at THIS repo's skills/proactive-loop
# (not a stale sibling checkout like PhonePcLiveAgents/).
#
# Always updates the workspace-scoped CCD used by sutando-core
# (<workspace>/.claude-sutando/skills). Also updates $CLAUDE_CONFIG_DIR/skills
# when set to a different path, and ~/.claude/skills as a legacy interactive
# fallback.
#
# Precedence for the toggle is in proactive-loop-enabled.py:
#   process env (SUTANDO_PROACTIVE_LOOP_ENABLED) > manifest.json config default
# Callers should export the env var first (start-cli / install.sh source .env).
# Safety net: if unset, read one line from repo .env.
#
# Triggers: src/agent/start-cli.sh (every core start/restart),
#           skills/install.sh (startup.sh + manual install).
#
# Test override: SUTANDO_PROACTIVE_LOOP_SYNC_ROOTS=colon:separated:dirs
# replaces the default skill roots (does not touch real CCD / ~/.claude).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
SKILL_SRC="$REPO/skills/proactive-loop"
GATE="$REPO/skills/proactive-loop/scripts/proactive-loop-enabled.py"
WS="$(bash "$REPO/scripts/sutando-config.sh" workspace)"

# Safety net: if caller forgot to export, read .env for this one var only.
if [ -z "${SUTANDO_PROACTIVE_LOOP_ENABLED+x}" ] && [ -f "$REPO/.env" ]; then
  # shellcheck disable=SC1091
  _line="$(grep -E '^[[:space:]]*SUTANDO_PROACTIVE_LOOP_ENABLED=' "$REPO/.env" | tail -1 || true)"
  if [ -n "$_line" ]; then
    export "${_line?}"
  fi
  unset _line
fi

state="enabled"
if [ -f "$GATE" ]; then
  state="$(python3 "$GATE" 2>/dev/null | head -1 | tr -d '[:space:]' || echo enabled)"
fi

# Collect unique skill roots to sync.
roots=()
add_root() {
  local r="$1"
  [ -n "$r" ] || return 0
  local x
  for x in "${roots[@]+"${roots[@]}"}"; do
    [ "$x" = "$r" ] && return 0
  done
  roots+=("$r")
}

if [ -n "${SUTANDO_PROACTIVE_LOOP_SYNC_ROOTS:-}" ]; then
  IFS=':' read -r -a _test_roots <<< "$SUTANDO_PROACTIVE_LOOP_SYNC_ROOTS"
  for r in "${_test_roots[@]}"; do
    add_root "$r"
  done
  unset _test_roots
else
  add_root "$WS/.claude-sutando/skills"
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    add_root "$CLAUDE_CONFIG_DIR/skills"
  fi
  add_root "$HOME/.claude/skills"
fi

sync_one() {
  local TARGET_ROOT="$1"
  local LINK="$TARGET_ROOT/proactive-loop"
  mkdir -p "$TARGET_ROOT"

  if [ "$state" = "disabled" ]; then
    if [ -L "$LINK" ]; then
      rm -f "$LINK"
      echo "proactive-loop skill unlinked (SUTANDO_PROACTIVE_LOOP_ENABLED=0): $LINK"
    elif [ -d "$LINK" ]; then
      echo "proactive-loop is a real directory at $LINK — leave it; remove manually if desired" >&2
    elif [ -e "$LINK" ]; then
      rm -f "$LINK"
      echo "proactive-loop skill removed (SUTANDO_PROACTIVE_LOOP_ENABLED=0): $LINK"
    else
      echo "proactive-loop skill already absent (disabled): $TARGET_ROOT"
    fi
    return 0
  fi

  if [ ! -f "$SKILL_SRC/SKILL.md" ]; then
    echo "proactive-loop source missing: $SKILL_SRC" >&2
    return 1
  fi
  if [ -L "$LINK" ]; then
    cur="$(readlink "$LINK" || true)"
    if [ "$cur" = "$SKILL_SRC" ] || [ "$cur" = "$SKILL_SRC/" ]; then
      echo "proactive-loop skill linked (enabled): $LINK"
      return 0
    fi
    rm -f "$LINK"
  elif [ -d "$LINK" ]; then
    echo "proactive-loop is a real directory at $LINK — skip relink" >&2
    return 0
  fi
  ln -s "$SKILL_SRC" "$LINK"
  echo "proactive-loop skill linked (enabled): $LINK -> $SKILL_SRC"
}

for root in "${roots[@]}"; do
  sync_one "$root" || true
done
