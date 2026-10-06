#!/bin/bash
# Build a double-clickable, Dock-pinnable Cutout.app bundle.
#
# A .app is just a folder with a specific layout — no compilation needed.
# The bundle carries a copy of the app source and runs it with the system
# python3 (so the target Mac needs Python 3.11+ installed).
#
# Usage:  scripts/make_macos_app.sh [output_dir]
# Result: <output_dir>/Cutout.app   (default output_dir: ./dist)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/dist}"
APP="$OUT/Cutout.app"
CONTENTS="$APP/Contents"
RES="$CONTENTS/Resources/app"

echo "Building $APP"
rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$RES"

# --- copy the app source into the bundle ---------------------------------
for f in launch.py server.py cutout.py logo.py requirements.txt README.md; do
  cp "$ROOT/$f" "$RES/"
done

# --- Info.plist ----------------------------------------------------------
cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>Cutout</string>
  <key>CFBundleDisplayName</key>     <string>Cutout</string>
  <key>CFBundleIdentifier</key>      <string>com.cutout.app</string>
  <key>CFBundleVersion</key>         <string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleExecutable</key>      <string>Cutout</string>
  <key>CFBundleIconFile</key>        <string>Cutout.icns</string>
  <key>NSHighResolutionCapable</key> <true/>
</dict>
</plist>
PLIST

# --- optional icon -------------------------------------------------------
if [ -f "$ROOT/assets/Cutout.icns" ]; then
  cp "$ROOT/assets/Cutout.icns" "$CONTENTS/Resources/Cutout.icns"
fi

# --- launcher executable -------------------------------------------------
cat > "$CONTENTS/MacOS/Cutout" <<'LAUNCH'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  osascript -e 'display dialog "Cutout needs Python 3.11+.\n\nInstall it from python.org, then open Cutout again." buttons {"OK"} with icon caution'
  exit 1
fi
cd "$DIR"
exec "$PY" launch.py
LAUNCH
chmod +x "$CONTENTS/MacOS/Cutout"

echo "Done: $APP"
echo "Drag it to /Applications, then right-click its Dock icon -> Options -> Keep in Dock."
