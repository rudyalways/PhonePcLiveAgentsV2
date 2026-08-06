#!/bin/bash
# Install Sutando skills into Claude Code ($CLAUDE_CONFIG_DIR/skills/).
# Creates symlinks so updates to the repo are picked up automatically.
# Resolves the target via the M0 claude-home-path helper so claude-sutando
# users get their workspace-scoped CCD honored.

set -e

SKILLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$(bash "$(cd "$SKILLS_DIR/.." && pwd)/scripts/sutando-config.sh" claude-home-path skills)"

mkdir -p "$TARGET"

for skill_dir in "$SKILLS_DIR"/*/; do
  skill_name=$(basename "$skill_dir")
  [ "$skill_name" = "install.sh" ] && continue
  [ ! -f "$skill_dir/SKILL.md" ] && continue
  # proactive-loop link follows SUTANDO_PROACTIVE_LOOP_ENABLED (see sync-skill-link.sh).
  [ "$skill_name" = "proactive-loop" ] && continue

  if [ -L "$TARGET/$skill_name" ] && [ ! -e "$TARGET/$skill_name" ]; then
    rm "$TARGET/$skill_name"
    ln -s "$skill_dir" "$TARGET/$skill_name"
    echo "  ✓ $skill_name (relinked — old symlink was broken)"
  elif [ -L "$TARGET/$skill_name" ]; then
    # Relink if pointing at a different checkout (stale sibling path).
    cur="$(readlink "$TARGET/$skill_name" || true)"
    want="${skill_dir%/}"
    if [ "$cur" != "$want" ] && [ "$cur" != "$want/" ]; then
      rm "$TARGET/$skill_name"
      ln -s "$skill_dir" "$TARGET/$skill_name"
      echo "  ✓ $skill_name (relinked — was $cur)"
    else
      echo "  ↻ $skill_name (symlink exists)"
    fi
  elif [ -d "$TARGET/$skill_name" ]; then
    echo "  ⚠ $skill_name (directory exists, skipping — remove manually to reinstall)"
  else
    ln -s "$skill_dir" "$TARGET/$skill_name"
    echo "  ✓ $skill_name"
  fi
done

# Honor whole-loop toggle: unlink when disabled, link to THIS repo when enabled.
# Load repo .env first when the var isn't already set (start-cli sources it;
# bare `bash skills/install.sh` / startup.sh otherwise fall through to the
# manifest default of enabled=1 and would re-link the skill).
REPO_ROOT="$(cd "$SKILLS_DIR/.." && pwd)"
if [ -z "${SUTANDO_PROACTIVE_LOOP_ENABLED+x}" ] && [ -f "$REPO_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi
if [ -x "$SKILLS_DIR/proactive-loop/scripts/sync-skill-link.sh" ]; then
  SUTANDO_SUPPRESS_CCD_FALLBACK_BANNER=1 \
    bash "$SKILLS_DIR/proactive-loop/scripts/sync-skill-link.sh" || true
fi

echo ""
echo "Installed. Skills available in any Claude Code session."
