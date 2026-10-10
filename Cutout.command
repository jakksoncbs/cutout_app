#!/bin/bash
# Double-click this in Finder (macOS) to start Cutout.
# It opens in Terminal; leave that window open while you work, close it to quit.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Return to close." _
  exit 1
fi
exec python3 launch.py
