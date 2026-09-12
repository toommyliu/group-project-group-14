#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$SCRIPT_DIR/.." && pwd)"
OS="$(uname -s)"

[[ -f "$SRC/pyproject.toml" ]] || { echo "error: run from a repo checkout"; exit 1; }

if [[ "$OS" == "Darwin" ]]; then
  ROOT="$HOME/Library/Application Support/Opengrader"
else
  ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/opengrader"
fi

echo ""
echo "Installing Opengrader to: $ROOT"

mkdir -p "$ROOT/app" 2>/dev/null || { echo "error: cannot create $ROOT (permissions?)"; exit 1; }
[[ -w "$ROOT" ]] || { echo "error: $ROOT is not writable"; exit 1; }

if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
    --exclude '.pytest_cache' --exclude '.tui_cache' --exclude 'out' \
    "$SRC/" "$ROOT/app/"
else
  rm -rf "$ROOT/app"; mkdir -p "$ROOT/app"
  tar -C "$SRC" --exclude .git --exclude .venv --exclude __pycache__ \
      --exclude .pytest_cache --exclude .tui_cache --exclude out -cf - . \
    | tar -C "$ROOT/app" -xf -
fi

install -m 755 "$SRC/packaging/bootstrap.sh" "$ROOT/bootstrap.sh"

printf '%s\n' "$SRC" >"$ROOT/source-path"
echo "  ✔ Application files copied"

ICON_PNG="$ROOT/app/packaging/icons/opengrader.png"

if [[ "$OS" == "Darwin" ]]; then
  APP="$HOME/Applications/Opengrader.app"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

  cat >"$ROOT/Opengrader.command" <<EOF
#!/bin/bash
exec "$ROOT/bootstrap.sh"
EOF
  chmod 755 "$ROOT/Opengrader.command"

  cat >"$APP/Contents/MacOS/opengrader" <<EOF
#!/bin/bash
CMD="$ROOT/Opengrader.command"
pick=""
for t in Ghostty iTerm WezTerm kitty; do
  if open -Ra "\$t" 2>/dev/null; then pick="\$t"; break; fi
done
case "\$pick" in
  Ghostty)  exec open -na Ghostty --args -e "\$CMD" ;;
  WezTerm)  exec open -na WezTerm --args start -- "\$CMD" ;;
  kitty)    exec open -na kitty   --args "\$CMD" ;;
  iTerm)    exec open -a iTerm "\$CMD" ;;
  *)        exec open -a Terminal "\$CMD" ;;
esac
EOF
  chmod 755 "$APP/Contents/MacOS/opengrader"

  cat >"$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>Opengrader</string>
  <key>CFBundleDisplayName</key>       <string>Opengrader</string>
  <key>CFBundleIdentifier</key>        <string>edu.sjsu.cmpe195.opengrader</string>
  <key>CFBundleVersion</key>           <string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>CFBundleExecutable</key>        <string>opengrader</string>
  <key>CFBundleIconFile</key>          <string>opengrader</string>
  <key>NSHighResolutionCapable</key>   <true/>
</dict>
</plist>
EOF

  if [[ -f "$ICON_PNG" ]]; then
    ICONSET="$(mktemp -d)/opengrader.iconset"
    mkdir -p "$ICONSET"
    for sz in 16 32 128 256 512; do
      sips -z "$sz" "$sz" "$ICON_PNG" --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null
      sips -z "$((sz*2))" "$((sz*2))" "$ICON_PNG" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
    done
    iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/opengrader.icns"
    rm -rf "$(dirname "$ICONSET")"
  fi

  touch "$APP"
  /System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister \
    -f "$APP" >/dev/null 2>&1 || true

  if [[ -d "$HOME/Desktop" ]]; then
    ln -sfn "$APP" "$HOME/Desktop/Opengrader"
  fi
  echo "  ✔ App bundle: $APP  (link on Desktop)"

else
  DESKTOP_FILE="$ROOT/opengrader.desktop"
  cat >"$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Opengrader
Comment=Local, offline-first exam grading
Exec=$ROOT/bootstrap.sh
Icon=$ICON_PNG
Terminal=true
Categories=Education;Utility;
EOF
  chmod 755 "$DESKTOP_FILE"

  APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  mkdir -p "$APPS_DIR"
  cp "$DESKTOP_FILE" "$APPS_DIR/opengrader.desktop"

  DESKTOP_DIR="$(command -v xdg-user-dir >/dev/null 2>&1 && xdg-user-dir DESKTOP || echo "$HOME/Desktop")"
  if [[ -d "$DESKTOP_DIR" ]]; then
    cp "$DESKTOP_FILE" "$DESKTOP_DIR/opengrader.desktop"
    chmod 755 "$DESKTOP_DIR/opengrader.desktop"
    gio set "$DESKTOP_DIR/opengrader.desktop" metadata::trusted true 2>/dev/null || true
  fi
  update-desktop-database "$APPS_DIR" 2>/dev/null || true
  echo "  ✔ Launcher: apps menu + $DESKTOP_DIR/opengrader.desktop"
fi

echo ""
echo "Done. Double-click the Opengrader icon on your Desktop to launch."
echo "First launch installs Python + dependencies (one-time, ~1–2 min)."
echo ""
