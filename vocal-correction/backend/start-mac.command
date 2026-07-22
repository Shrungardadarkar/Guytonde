#!/bin/bash
# Double-click this file in Finder to start the Vocal Tuning Correction engine.
# Leave the window that opens running in the background while you use the
# webpage. Press Ctrl+C in this window (or just close it) to stop.

set -e
cd "$(dirname "$0")"

echo "Vocal Tuning Correction -- starting local engine..."
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 isn't installed on this Mac yet."
  echo "Install it from https://www.python.org/downloads/macos/ (get the latest"
  echo "3.x installer), then double-click this file again."
  read -p "Press Enter to close this window..."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "First-time setup -- this takes a few minutes, only happens once."
  python3 -m venv .venv
fi

source .venv/bin/activate

if [ ! -f ".venv/.deps_installed" ]; then
  echo "Installing the audio processing libraries (first run only)..."
  pip install --upgrade pip
  if ! pip install -r requirements.txt; then
    echo ""
    echo "Install failed. On a Mac this is usually because the command-line"
    echo "developer tools aren't installed yet. Try running this in Terminal:"
    echo "    xcode-select --install"
    echo "then double-click this file again once that finishes."
    read -p "Press Enter to close this window..."
    exit 1
  fi
  touch ".venv/.deps_installed"
fi

echo ""
echo "Starting the engine. Keep this window open while you use the webpage."
echo "When you see 'Uvicorn running on http://127.0.0.1:8000' below, go to:"
echo "  https://shrungardadarkar.github.io/Guytonde/"
echo ""

uvicorn main:app --port 8000
