#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
CHECKOUT=$(dirname "$SCRIPT_DIR")
WRAPPER="$SCRIPT_DIR/opengrader"

BIN_DIR="${OPENGRADER_BIN:-$HOME/.local/bin}"
TARGET="$BIN_DIR/opengrader"

fail() {
    printf 'error: %s\n' "$1" >&2
    exit 1
}

[ -f "$WRAPPER" ] || fail "cannot find $WRAPPER — run this from a checkout of the repo."
[ -f "$CHECKOUT/pyproject.toml" ] || fail "$CHECKOUT does not look like an Opengrader checkout."

if ! command -v uv >/dev/null 2>&1; then
    printf 'note: uv is not on this PATH.\n'
    printf '      opengrader will use the project virtualenv, Poetry, or an activated\n'
    printf '      environment instead. If none of those exist yet, set one up with:\n'
    printf '        curl -LsSf https://astral.sh/uv/install.sh | sh  &&  uv sync\n'
    printf '        ...or:  poetry install\n\n'
fi

mkdir -p "$BIN_DIR" || fail "could not create $BIN_DIR"

if [ -e "$TARGET" ] && ! grep -q 'opengrader\[wrapper\]' "$TARGET" 2>/dev/null; then
    fail "$TARGET already exists and was not installed by this script.
  If it came from \`uv tool install\`, remove that first:  uv tool uninstall opengrader
  Then run this again."
fi

cp "$WRAPPER" "$TARGET"
chmod +x "$TARGET"

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/opengrader"
mkdir -p "$CACHE_DIR" 2>/dev/null && printf '%s\n' "$CHECKOUT" >"$CACHE_DIR/checkout-path" 2>/dev/null || true

printf 'Installed: %s\n' "$TARGET"
printf 'Checkout:  %s\n' "$CHECKOUT"
printf '\n'

case ":${PATH}:" in
    *":$BIN_DIR:"*)
        printf 'Try it:\n  opengrader doctor\n'
        ;;
    *)
        printf '%s is not on your PATH yet. Add it:\n\n' "$BIN_DIR"
        case "${SHELL:-}" in
            */zsh)  profile="~/.zshrc" ;;
            */bash) profile="~/.bash_profile" ;;
            *)      profile="your shell profile" ;;
        esac
        printf "  echo 'export PATH=\"%s:\$PATH\"' >> %s\n" "$BIN_DIR" "$profile"
        printf '\nThen open a new terminal and run:\n  opengrader doctor\n'
        ;;
esac
