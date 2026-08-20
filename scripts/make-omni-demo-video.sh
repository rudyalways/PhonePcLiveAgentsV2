#!/usr/bin/env bash
# Generate the omni-exp demo video: a series of spoken, actionable tasks over
# on-screen text. The omni agent hears the audio, transcribes it, and calls
# work() for each task — so the demo shows the full pipeline (task -> CC ->
# result -> spoken) without anyone talking into the mic.
#
# macOS only: uses /usr/bin/say for TTS and ffmpeg for muxing. Each task is
# rendered as a still frame (Pillow) and paired with its TTS audio; segments
# are concatenated into one MP4 with 16 kHz mono PCM16 audio — the exact format
# the omni-exp client sends from the mic.
#
# Usage:
#   bash scripts/make-omni-demo-video.sh [output.mp4]
#
# Default output: state/omni-demo/omni-tasks-demo.mp4 (served by the agent at
# /omni-demo/omni-tasks-demo.mp4). If state/omni-demo is not writable, falls
# back to the repo root.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$REPO/state/omni-demo/omni-tasks-demo.mp4}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

VOICE="${OMNI_DEMO_VOICE:-Samantha}"   # en_US; try Alex, Daniel, Karen, Samantha
WIDTH="${OMNI_DEMO_WIDTH:-1280}"
HEIGHT="${OMNI_DEMO_HEIGHT:-720}"
FONT="${OMNI_DEMO_FONT:-/System/Library/Fonts/Helvetica.ttc}"

# Each task is phrased distinctly so the agent's work-dedupe
# (normalize_work_task, 180s window) treats them as separate tasks.
TASKS=(
  "Follow up with Sarah about the Q3 planning document."
  "Set up a meeting for Thursday afternoon with the design team."
  "Send an email to John thanking him for the introduction."
  "Summarize the last few days of news about AI regulation."
  "Research the best standing desks under five hundred dollars and give me three options."
  "Remind me to review the contract before Friday."
  "Book a table for two at a sushi restaurant near the office for Friday night."
  "Draft a short message to the landlord about the broken dishwasher."
  "What are the key takeaways from the latest OpenAI announcement?"
)

say_command() {
  command -v say >/dev/null 2>&1 || { echo "say not found — macOS only" >&2; exit 1; }
  command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg not found" >&2; exit 1; }
  "$REPO/.venv/bin/python3" -c 'import PIL' 2>/dev/null || { echo "Pillow not installed in .venv" >&2; exit 1; }
}

render_frame() {  # render_frame <text> <n> <total> <out.png>
  local text="$1" n="$2" total="$3" out="$4"
  "$REPO/.venv/bin/python3" - "$text" "$n" "$total" "$out" "$WIDTH" "$HEIGHT" "$FONT" <<'PY'
import sys, textwrap
from PIL import Image, ImageDraw, ImageFont
text, n, total, out, W, H, fontpath = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], int(sys.argv[5]), int(sys.argv[6]), sys.argv[7]
img = Image.new("RGB", (W, H), "#0f3460")
d = ImageDraw.Draw(img)
# header bar
d.rectangle([0, 0, W, 110], fill="#1a1a2e")
try:
    f_head = ImageFont.truetype(fontpath, 40)
    f_body = ImageFont.truetype(fontpath, 64)
except Exception:
    f_head = ImageFont.load_default()
    f_body = ImageFont.load_default()
d.text((40, 30), "SUTANDO OMNI — TASK DEMO", font=f_head, fill="#c4b5fd")
d.text((W - 200, 30), f"{n} / {total}", font=f_head, fill="#86efac")
# body text, wrapped
lines = textwrap.wrap(text, width=34)
y = 200
for ln in lines:
    d.text((80, y), ln, font=f_body, fill="#ffffff")
    y += 90
img.save(out)
PY
}

say_command
mkdir -p "$WORK" "$(dirname "$OUT")"

declare -a SEGMENTS=()
total=${#TASKS[@]}
for i in "${!TASKS[@]}"; do
  n=$((i + 1))
  safe=$(printf "%02d" "$n")
  aiff="$WORK/task-$safe.aiff"
  png="$WORK/task-$safe.png"
  seg="$WORK/task-$safe.mp4"
  echo "[$n/$total] ${TASKS[$i]}"
  say -v "$VOICE" -o "$aiff" "${TASKS[$i]}"
  render_frame "${TASKS[$i]}" "$n" "$total" "$png"
  # Still frame + TTS audio -> one segment. Resample audio to 16 kHz mono
  # PCM16 (matches the client's mic path) and add 1.2s of silence at the end
  # so tasks don't run together in the transcript.
  ffmpeg -hide_banner -loglevel error -y \
    -loop 1 -i "$png" -i "$aiff" \
    -filter_complex "[1:a]aresample=16000,aformat=sample_fmts=s16:channel_layouts=mono,apad=pad_dur=1.2[a]" \
    -map 0:v -map "[a]" -c:v libx264 -tune stillimage -pix_fmt yuv420p \
    -c:a aac -b:a 96k -shortest "$seg"
  SEGMENTS+=("$seg")
done

# concat list
LIST="$WORK/list.txt"
for seg in "${SEGMENTS[@]}"; do echo "file '$seg'" >> "$LIST"; done

echo "Concatenating ${#SEGMENTS[@]} segments → $OUT"
ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$LIST" -c copy "$OUT"

echo "Done: $OUT"
ls -lh "$OUT"
