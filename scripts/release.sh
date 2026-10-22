#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo "release: $1" >&2; exit 1; }

[[ $# -eq 1 ]] || fail "usage: scripts/release.sh {testpypi|pypi}"
case "$1" in
  testpypi|pypi) ;;
  *) fail "unknown registry '$1'; expected testpypi or pypi" ;;
esac
[[ -n "${UV_PUBLISH_TOKEN:-}" ]] || fail "set UV_PUBLISH_TOKEN in the environment before releasing"

npm --prefix src/opengrader/web ci
npm --prefix src/opengrader/web run build

rm -rf dist
uv build

wheel=$(ls dist/*.whl)
sdist=$(ls dist/*.tar.gz)

wheel_files=$(unzip -Z1 "$wheel")
sdist_files=$(tar tzf "$sdist")
grep -qx 'opengrader/web/dist/index.html' <<<"$wheel_files" || fail "wheel is missing the web app"
grep -q '^opengrader/web/src/' <<<"$wheel_files" && fail "wheel contains the web app sources"
sdist_mb=$(( $(wc -c <"$sdist") / 1024 / 1024 ))
(( sdist_mb < 50 )) || fail "sdist is ${sdist_mb} MB"

venv="$(mktemp -d)/venv"
uv venv -q -p 3.12 "$venv"
VIRTUAL_ENV="$venv" uv pip install -q "$wheel"
"$venv/bin/opengrader" --version
"$venv/bin/python" -I -c '
from opengrader.core.browser_app import web_dist_state
from opengrader.core.pdf.templates.fonts import CMU_BOLD_FILE, CMU_REGULAR_FILE
assert web_dist_state() == "ready", web_dist_state()
assert CMU_REGULAR_FILE.is_file() and CMU_BOLD_FILE.is_file(), CMU_REGULAR_FILE
'

ls -lh dist
uv publish --index "$1" "$wheel" "$sdist"
