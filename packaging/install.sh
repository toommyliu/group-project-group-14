#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$SCRIPT_DIR/.." && pwd)"
OS="$(uname -s)"

# --build-web: build the browser app now, from this checkout, so the first
# "Open in browser" a user presses doesn't have to. Optional and best effort
# — no flag, no pnpm, or a failed build all fall back to what already happens
# without this: classic offers to build it itself, once, on first use.
BUILD_WEB=""
for arg in "$@"; do
  case "$arg" in
    --build-web) BUILD_WEB=1 ;;
  esac
done

[[ -f "$SRC/pyproject.toml" ]] || { echo "error: run from a repo checkout"; exit 1; }

_find_pnpm() {
  if command -v pnpm >/dev/null 2>&1; then
    command -v pnpm
    return 0
  fi
  # Where a desktop-launched shell (or this script's own PATH) might not
  # look: Homebrew, MacPorts, per-user installs.
  for dir in /opt/homebrew/bin /usr/local/bin /opt/local/bin; do
    if [[ -x "$dir/pnpm" ]]; then
      echo "$dir/pnpm"
      return 0
    fi
  done
  return 1
}

if [[ -n "$BUILD_WEB" ]]; then
  WEB_SRC="$SRC/src/opengrader/web"
  if PNPM_BIN="$(_find_pnpm)"; then
    echo "Building the browser app (needs the internet once)…"
    # pnpm is a Node script: its own folder (MacPorts, Homebrew) holds node too.
    export PATH="$(dirname "$PNPM_BIN"):$PATH"
    if "$PNPM_BIN" --dir "$WEB_SRC" install --frozen-lockfile \
        && "$PNPM_BIN" --dir "$WEB_SRC" build; then
      echo "  ✔ Browser app built"
    else
      echo "  ⚠ Browser app build failed — continuing without it (classic will offer to build it later)"
    fi
  else
    echo "  ⚠ --build-web given but pnpm was not found — continuing without it"
  fi
fi

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
  # Keep a browser app built in the installed copy unless this checkout has one.
  KEEP_WEB_DIST=""
  [[ -f "$SRC/src/opengrader/web/dist/index.html" ]] || KEEP_WEB_DIST=1
  rsync -a --delete \
    --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
    --exclude '.pytest_cache' --exclude '.tui_cache' --exclude 'out' \
    --exclude 'node_modules' \
    ${KEEP_WEB_DIST:+--exclude=/src/opengrader/web/dist} \
    "$SRC/" "$ROOT/app/"
else
  rm -rf "$ROOT/app"; mkdir -p "$ROOT/app"
  tar -C "$SRC" --exclude .git --exclude .venv --exclude __pycache__ \
      --exclude .pytest_cache --exclude .tui_cache --exclude out \
      --exclude node_modules -cf - . \
    | tar -C "$ROOT/app" -xf -
fi

install -m 755 "$SRC/packaging/bootstrap.sh" "$ROOT/bootstrap.sh"

printf '%s\n' "$SRC" >"$ROOT/source-path"
echo "  ✔ Application files copied"

ICON_PNG="$ROOT/app/packaging/icons/opengrader.png"

if [[ "$OS" == "Darwin" ]]; then
  APP="$HOME/Applications/Opengrader.app"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

  install -m 755 "$SRC/packaging/Opengrader.command" "$ROOT/Opengrader.command"
  # A checkout unzipped from a browser download is quarantined, and install
  # copies that mark: the icon would then be stopped by Gatekeeper.
  xattr -d com.apple.quarantine "$ROOT/Opengrader.command" "$ROOT/bootstrap.sh" 2>/dev/null || true

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
  # kitty stays in the Dock after its last window closes unless told otherwise.
  kitty)    exec open -na kitty   --args -o macos_quit_when_last_window_closed=yes "\$CMD" ;;
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
