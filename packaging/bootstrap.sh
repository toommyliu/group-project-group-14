#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/app/pyproject.toml" ]]; then
  APP_DIR="$SCRIPT_DIR/app"
elif [[ -f "$SCRIPT_DIR/../pyproject.toml" ]]; then
  APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  echo "error: cannot find Opengrader next to $SCRIPT_DIR" >&2
  exit 1
fi

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

LOG="${TMPDIR:-/tmp}/opengrader-bootstrap.log"
: >"$LOG"

SYNC_SOURCE=""
if [[ -f "$SCRIPT_DIR/source-path" ]] && command -v rsync >/dev/null 2>&1; then
  candidate="$(cat "$SCRIPT_DIR/source-path")"
  # An external drive can come back under another name ("Samsung_T5 1"):
  # look for the same checkout on every mounted volume and remember it.
  if [[ ! -f "$candidate/pyproject.toml" && "$candidate" == /Volumes/*/* ]]; then
    rest="${candidate#/Volumes/*/}"
    for volume in /Volumes/*; do
      if grep -qs '^name = "opengrader"$' "$volume/$rest/pyproject.toml"; then
        candidate="$volume/$rest"
        printf '%s\n' "$candidate" >"$SCRIPT_DIR/source-path"
        break
      fi
    done
  fi
  if [[ -f "$candidate/pyproject.toml" ]]; then
    SYNC_SOURCE="$candidate"
  fi
fi

STEP=0
TOTAL=4
[[ -n "$SYNC_SOURCE" ]] && TOTAL=5

say()  { printf '%s\n' "$*"; }

fail() {
  printf '\r\033[K  \033[31m✖\033[0m %s\n\n' "$1"
  say "Last lines of $LOG:"
  tail -n 15 "$LOG" | sed 's/^/    /'
  if [[ -t 0 ]]; then
    printf '\nPress Enter to close... '
    read -r
  fi
  exit 1
}

run_step() {
  local title=$1; shift
  STEP=$((STEP + 1))
  if [[ -t 1 ]]; then
    "$@" >>"$LOG" 2>&1 &
    local pid=$! frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' i=0
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r  [%d/%d] %s %s ' "$STEP" "$TOTAL" "${frames:i++%10:1}" "$title"
      sleep 0.1
    done
    local status=0
    wait "$pid" || status=$?
    if [[ $status -eq 0 ]]; then
      printf '\r\033[K  [%d/%d] \033[32m✔\033[0m %s\n' "$STEP" "$TOTAL" "$title"
    else
      fail "$title"
    fi
  else
    "$@" >>"$LOG" 2>&1 || fail "$title"
    printf '  [%d/%d] ✔ %s\n' "$STEP" "$TOTAL" "$title"
  fi
}

skip_step() {
  STEP=$((STEP + 1))
  printf '  [%d/%d] \033[32m✔\033[0m %s\n' "$STEP" "$TOTAL" "$1"
}

say ""
say "  Opengrader — checking environment"
say ""

if [[ -n "$SYNC_SOURCE" ]]; then
  run_step "Refreshing application files" \
    rsync -a --delete \
      --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
      --exclude '.pytest_cache' --exclude '.tui_cache' --exclude 'out' \
      "$SYNC_SOURCE/" "$APP_DIR/"
  if [[ -f "$APP_DIR/packaging/bootstrap.sh" ]]; then
    install -m 755 "$APP_DIR/packaging/bootstrap.sh" "$SCRIPT_DIR/.bootstrap.sh.new" &&
      mv -f "$SCRIPT_DIR/.bootstrap.sh.new" "$SCRIPT_DIR/bootstrap.sh"
  fi
fi

if command -v uv >/dev/null 2>&1; then
  skip_step "uv package manager"
else
  run_step "Installing uv package manager" \
    bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
  command -v uv >/dev/null 2>&1 || fail "uv not on PATH after install"
fi

run_step "Python 3.12 runtime" uv python install 3.12

run_step "Project dependencies" \
  uv sync --frozen --no-dev --project "$APP_DIR"

if command -v tesseract >/dev/null 2>&1; then
  skip_step "Tesseract OCR"
elif [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
  run_step "Installing Tesseract OCR (brew)" brew install tesseract
elif [[ "$(uname -s)" == "Linux" ]]; then
  STEP=$((STEP + 1))
  PKG=""
  command -v apt-get >/dev/null 2>&1 && PKG="sudo apt-get install -y tesseract-ocr"
  command -v dnf     >/dev/null 2>&1 && PKG="sudo dnf install -y tesseract"
  command -v pacman  >/dev/null 2>&1 && PKG="sudo pacman -S --noconfirm tesseract"
  if [[ -n "$PKG" && -t 0 ]]; then
    printf '  [%d/%d] Tesseract OCR is missing (needed for scan extraction).\n' "$STEP" "$TOTAL"
    printf '        Install now with: %s ? [Y/n] ' "$PKG"
    read -r reply
    if [[ -z "$reply" || "$reply" =~ ^[Yy] ]]; then
      $PKG || say "        (install failed — continuing; the TUI works without it)"
    fi
  else
    printf '  [%d/%d] \033[33m⚠\033[0m Tesseract OCR not found — install it later for scan extraction.\n' "$STEP" "$TOTAL"
  fi
else
  STEP=$((STEP + 1))
  printf '  [%d/%d] \033[33m⚠\033[0m Tesseract OCR not found — install Homebrew + tesseract for scan extraction.\n' "$STEP" "$TOTAL"
fi

if [[ "${TERM_PROGRAM:-}" == "Apple_Terminal" ]]; then
  say ""
  say "  note: Terminal.app can't show images. Grading still works — press 'o'"
  say "        to open a scan in Preview. For crisp in-app previews, install"
  say "        Ghostty, iTerm2, WezTerm, or kitty and relaunch."
fi
if ! command -v ollama >/dev/null 2>&1; then
  say "  note: Ollama not found — handwriting extraction (submission-extractor)"
  say "        will be unavailable until you install it from https://ollama.com"
fi

say ""
say "  Launching Opengrader..."
say ""

cd "$APP_DIR"
# The icon is for people who may never open a terminal: the classic app
# (Tools & Settings + Start grading, plain folders, no workspace service).
# `opengrader tui` is the workspace app for power users.
exec uv run --no-sync opengrader classic "$@"
