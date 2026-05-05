#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SAMPLES_DIR="$REPO_ROOT/samples"

if [[ ! -d "$SAMPLES_DIR" ]]; then
  echo "samples directory not found: $SAMPLES_DIR" >&2
  exit 1
fi

found_split_dir=0
while IFS= read -r split_dir; do
  found_split_dir=1
  assignment_dir="$(dirname "$split_dir")"
  output_dir="$assignment_dir/student-submissions"

  echo "Regenerating $(basename "$assignment_dir")"
  mkdir -p "$output_dir"

  shopt -s nullglob dotglob
  contents=("$output_dir"/*)
  if ((${#contents[@]})); then
    rm -rf -- "${contents[@]}"
  fi
  shopt -u dotglob

  uv run submission-file \
    --input "$split_dir" \
    --output "$output_dir" \
    "$@"
done < <(
  find "$SAMPLES_DIR" -mindepth 2 -maxdepth 2 -type d -name split-submissions | sort
)

if ((found_split_dir == 0)); then
  echo "No sample split-submissions directories found under $SAMPLES_DIR" >&2
  exit 1
fi
