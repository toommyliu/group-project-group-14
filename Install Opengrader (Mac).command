#!/bin/bash
# Double-click installer for macOS: runs packaging/install.sh from wherever
# this folder was unzipped, so nobody has to open a terminal and cd into it.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$HERE/packaging/install.sh" ]]; then
  bash "$HERE/packaging/install.sh" "$@"
  status=$?
else
  echo ""
  echo "Opengrader's files were not found next to this installer."
  echo "Keep it inside the Opengrader folder it came in, then open it again."
  status=1
fi

if [[ $status -ne 0 ]]; then
  echo ""
  echo "Opengrader was not installed. The message above says why."
fi

# Terminal keeps a finished window open; the other terminals close theirs,
# so wait for a key either way.
if [[ -t 0 ]]; then
  printf 'Press Enter to close this window... '
  read -r
fi

exit $status
