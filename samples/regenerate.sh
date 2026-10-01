#!/bin/sh
set -eu

samples_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(dirname -- "$samples_dir")

if [ "$#" -gt 0 ]; then
    cat <<'USAGE'
Usage: samples/regenerate.sh

Delete generated sample submissions, crops, transcriptions, grades, and
Opengrader metadata, then split, extract, and auto-grade each submissions.pdf.
Source scans, templates, answer keys, and ground-truth labels are preserved.
Samples without submissions.pdf are skipped. Uses the saved AI settings,
with response transcription enabled. Requires uv and the extraction dependencies.
USAGE
    case "$1" in
        -h|--help) exit 0 ;;
        *) exit 2 ;;
    esac
fi

command -v uv >/dev/null 2>&1 || { echo "uv is required." >&2; exit 1; }
cd -- "$repo_root"

for scan in "$samples_dir"/*/submissions.pdf; do
    [ -f "$scan" ] || continue
    assignment_dir=$(dirname -- "$scan")
    [ ! -L "$assignment_dir" ] || { echo "Refusing sample symlink: $assignment_dir" >&2; exit 1; }
    echo "Clearing generated artifacts: $(basename -- "$assignment_dir")"
    rm -rf -- "$assignment_dir/split-submissions" "$assignment_dir/student-submissions" \
        "$assignment_dir/split" "$assignment_dir/submissions" "$assignment_dir/.opengrader"
done

for scan in "$samples_dir"/*/submissions.pdf; do
    [ -f "$scan" ] || continue
    assignment_dir=$(dirname -- "$scan")
    echo "Regenerating $(basename -- "$assignment_dir")"

    uv run submission-splitter --input "$scan" \
        --output "$assignment_dir/split-submissions" --fix-rotation --fix-order

    uv run submission-extractor --input "$assignment_dir/split-submissions" \
        --output "$assignment_dir/student-submissions"

    if [ -f "$assignment_dir/qa.md" ]; then
        uv run automated-grading --solutions "$assignment_dir/qa.md" \
            --input "$assignment_dir/student-submissions"
    else
        echo "Skipping auto-grading: no qa.md in $(basename -- "$assignment_dir")"
    fi
done
