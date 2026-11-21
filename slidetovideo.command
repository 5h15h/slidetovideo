#!/bin/sh
# macOS: double-click this. First run may ask Gatekeeper for permission —
# right-click → Open once, then double-click works.
cd "$(dirname "$0")" || exit 1
exec python3 slidetovideo.py --app "$@"
