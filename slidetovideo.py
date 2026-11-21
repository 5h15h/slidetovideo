#!/usr/bin/env python3
"""Start SlideToVideo and open it.

    python3 slidetovideo.py            # start the renderer, open a browser
    python3 slidetovideo.py --app      # open a chrome-less app window instead
    python3 slidetovideo.py --no-open  # just run the renderer

The work happens on this machine, so something has to be running. This starts
it, waits until it answers, and opens the app.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "webapp" / "server.py"

APP_BROWSERS = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ],
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ],
    "linux": ["google-chrome", "chromium", "chromium-browser",
              "microsoft-edge", "brave-browser"],
}


def port_open(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.35)
        return s.connect_ex((host, port)) == 0


def free_port(host: str, start: int, tries: int = 20) -> int:
    for p in range(start, start + tries):
        if not port_open(host, p):
            return p
    return start


def healthy(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def find_app_browser() -> str | None:
    for cand in APP_BROWSERS.get(sys.platform, APP_BROWSERS["linux"]):
        path = cand if os.path.isabs(cand) else shutil.which(cand)
        if path and os.path.exists(path):
            return path
    return None


def open_app_window(url: str) -> bool:
    exe = find_app_browser()
    if not exe:
        return False
    profile = Path.home() / ".slidetovideo" / "browser"
    profile.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(
            [exe, f"--app={url}", f"--user-data-dir={profile}",
             "--new-window", "--no-first-run", "--no-default-browser-check"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Run SlideToVideo locally")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8808)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--app", action="store_true",
                    help="open a chrome-less app window instead of a tab")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser")
    a = ap.parse_args()

    if not SERVER.exists():
        print(f"error: {SERVER} not found", file=sys.stderr)
        return 1

    url = f"http://{a.host}:{a.port}"
    proc = None

    if healthy(url):
        print(f"  already running at {url}")
    else:
        if port_open(a.host, a.port):
            a.port = free_port(a.host, a.port + 1)
            url = f"http://{a.host}:{a.port}"
            print(f"  port in use, moved to {a.port}")
        env = {**os.environ, "PORT": str(a.port), "HOST": a.host}
        print(f"  starting the renderer on {url}")
        proc = subprocess.Popen([sys.executable, str(SERVER)], cwd=str(ROOT), env=env)

        for _ in range(120):                       # up to ~60s for a cold start
            if healthy(url):
                break
            if proc.poll() is not None:
                print("error: the renderer exited during start-up",
                      file=sys.stderr)
                return proc.returncode or 1
            time.sleep(0.5)
        else:
            print("error: the renderer did not become healthy", file=sys.stderr)
            proc.terminate()
            return 1

    print(f"  ready: {url}")
    if not a.no_open:
        if not (a.app and open_app_window(url)):
            webbrowser.open(url)
        print("  tip: use the Install app button to add it to your dock")

    if proc is None:
        return 0
    print("  Ctrl+C to stop")
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n  stopping")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
