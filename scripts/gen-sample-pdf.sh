#!/bin/bash
set -e
cd "$(dirname "$0")/.."
uv run sample-pdf --output tests/sample_output --versions A B --write-spec
