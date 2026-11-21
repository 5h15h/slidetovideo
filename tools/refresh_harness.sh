#!/bin/sh
# Copy the live UI into the screenshot harness and re-apply its two patches.
set -e
SRC="$(dirname "$0")/../webapp/static"
DST=/tmp/shot
mkdir -p "$DST/assets"
cp "$SRC"/index.html "$SRC"/app.css "$SRC"/app.js "$DST"/
cp "$SRC"/assets/* "$DST/assets/" 2>/dev/null || true
python3 - "$DST/index.html" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); t = p.read_text()
if 'stub.js' not in t:
    t = t.replace('<script src="app.js"></script>',
                  '<script src="stub.js"></script>\n<script src="app.js"></script>')
if 'notrans' not in t:
    t = t.replace('</head>',
        '<style id="notrans">*,*::before,*::after{transition:none!important;animation:none!important}</style>\n</head>')
p.write_text(t)
print("harness refreshed")
PY
