#!/bin/sh
# Put `opengrader` on your PATH, so it works from any directory.
#
#   sh packaging/install-command.sh
#
# This installs exactly ONE command: `opengrader`. That is the whole reason it
# exists rather than the more obvious `uv tool install .`, which works but
# installs all thirty-odd console scripts from [project.scripts] — `doctor`,
# `curve`, `claim`, `tui`, `calibrate`, `regrade` — into a directory on your
# PATH. Those names are generic enough to shadow other tools and meaningless
# out of context, and putting them there re-creates, globally, the exact
# problem the `opengrader` front door was built to solve. (uv has no way to
# filter which entry points a tool install exposes; checked on uv 0.12.)
#
# What lands on disk is packaging/opengrader, copied verbatim: it holds no
# path to this checkout, and finds the project itself on every run. So this
# installer never needs re-running when the drive is remounted under a new
# name, when the project moves, or when dependencies change.
#
# It also holds no assumption about *how* the project's environment was built.
# uv is preferred where it is installed, but a Poetry environment, a plain
# `python -m venv`, or an activated Conda environment all work — see the runner
# list at the top of packaging/opengrader.
#
# Uninstall:  rm ~/.local/bin/opengrader

set -eu

# Resolve this script's own directory so the installer works from anywhere.
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
CHECKOUT=$(dirname "$SCRIPT_DIR")
WRAPPER="$SCRIPT_DIR/opengrader"

# ~/.local/bin is the conventional per-user bin directory on macOS and Linux,
# needs no sudo, and is already where uv installs itself. Override with
# OPENGRADER_BIN if you keep your own tools somewhere else.
BIN_DIR="${OPENGRADER_BIN:-$HOME/.local/bin}"
TARGET="$BIN_DIR/opengrader"

fail() {
    printf 'error: %s\n' "$1" >&2
    exit 1
}

[ -f "$WRAPPER" ] || fail "cannot find $WRAPPER — run this from a checkout of the repo."
[ -f "$CHECKOUT/pyproject.toml" ] || fail "$CHECKOUT does not look like an Opengrader checkout."

# uv is what this project is developed with, but the launcher does not require
# it — it will use a Poetry environment, a plain virtualenv or an activated
# Conda environment just as happily. So a missing uv is worth saying out loud
# and is not a reason to refuse to install a script that may well work.
if ! command -v uv >/dev/null 2>&1; then
    printf 'note: uv is not on this PATH.\n'
    printf '      opengrader will use the project virtualenv, Poetry, or an activated\n'
    printf '      environment instead. If none of those exist yet, set one up with:\n'
    printf '        curl -LsSf https://astral.sh/uv/install.sh | sh  &&  uv sync\n'
    printf '        ...or:  poetry install\n\n'
fi

mkdir -p "$BIN_DIR" || fail "could not create $BIN_DIR"

# Refuse to clobber something that is not ours. A stray `opengrader` here is
# far more likely to be a previous `uv tool install` than a name collision,
# but either way overwriting a file this script did not write is not its call.
if [ -e "$TARGET" ] && ! grep -q 'opengrader\[wrapper\]' "$TARGET" 2>/dev/null; then
    fail "$TARGET already exists and was not installed by this script.
  If it came from \`uv tool install\`, remove that first:  uv tool uninstall opengrader
  Then run this again."
fi

cp "$WRAPPER" "$TARGET"
chmod +x "$TARGET"

# Prime the launcher's cache with this checkout, so the very first run outside
# the project does not pay for a filesystem search it can avoid.
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/opengrader"
mkdir -p "$CACHE_DIR" 2>/dev/null && printf '%s\n' "$CHECKOUT" >"$CACHE_DIR/checkout-path" 2>/dev/null || true

printf 'Installed: %s\n' "$TARGET"
printf 'Checkout:  %s\n' "$CHECKOUT"
printf '\n'

# Being on PATH is the one part this script cannot do for you: editing a
# shell profile is the user's file and their call, so say exactly what to add
# rather than adding it.
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
