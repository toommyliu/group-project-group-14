#!/bin/bash
set -e
cd "$(dirname "$0")/.."
uv run sample-pdf --output tests/sample_output --versions A B --write-spec --title "
Quiz 1: Shell/Linux Commands
CMPE195A: Senior Design Project I
San Jose State University
Friday, March 22, 2026
" --instructions "Closed book, closed notes. 6 questions, 10 minutes."
