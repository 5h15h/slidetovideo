#!/bin/sh
# Adds SlideToVideo to the application menu on Linux.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/512x512/apps"
mkdir -p "$APPS" "$ICONS"
cp "$ROOT/webapp/static/assets/icon-512.png" "$ICONS/slidetovideo.png"
cat > "$APPS/slidetovideo.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SlideToVideo
Comment=Turn a presentation into a narrated video
Exec=python3 $ROOT/slidetovideo.py --app
Path=$ROOT
Icon=slidetovideo
Terminal=false
Categories=AudioVideo;Video;Office;
StartupWMClass=slidetovideo
DESKTOP
update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
echo "installed $APPS/slidetovideo.desktop"
