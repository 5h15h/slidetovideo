#!/bin/sh
# Build the static bundle Vercel serves, pointed at your renderer.
#   ./deploy/build-frontend.sh https://slidetovideo.fly.dev
set -e
API="${1:?usage: build-frontend.sh https://your-renderer-host}"
API="${API%/}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/deploy/vercel/public"

rm -rf "$OUT" && mkdir -p "$OUT/assets"
cp "$ROOT/webapp/static/index.html" "$ROOT/webapp/static/app.css" \
   "$ROOT/webapp/static/app.js" "$OUT/"
cp "$ROOT"/webapp/static/assets/* "$OUT/assets/"

printf 'window.SLIDETOVIDEO_API_BASE = "%s";\n' "$API" > "$OUT/config.js"

python3 - "$OUT/index.html" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); t = p.read_text()
if "config.js" not in t:
    t = t.replace('<script src="app.js"></script>',
                  '<script src="config.js"></script>\n<script src="app.js"></script>')
p.write_text(t)
PY

echo "built $OUT  ->  API base $API"
