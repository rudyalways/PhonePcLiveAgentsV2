#!/bin/bash
# Compatibility stub — experimental phone agent was renamed to omni-exp
# (voice-agent has its own omni/webcam path).
echo "note: start-omni.sh → start-omni-exp.sh (experimental phone agent)" >&2
exec bash "$(cd "$(dirname "$0")" && pwd)/start-omni-exp.sh" "$@"
