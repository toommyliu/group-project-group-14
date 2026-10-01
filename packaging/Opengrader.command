#!/bin/bash
# What the macOS Desktop icon opens in a terminal window. Runs the launcher
# and, once Opengrader quits, makes the window go away the way a normal app's
# would. Only the icon comes through here; `opengrader` typed in a terminal
# never does, so nobody's own terminal window is closed.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$ROOT/bootstrap.sh" "$@"
status=$?

# Terminal.app keeps a finished window open ("[Process completed]"). After a
# clean exit, close it; after a failure, leave it so the message can be read.
# The other terminals the icon uses close their window on their own.
if [[ $status -eq 0 && "${TERM_PROGRAM:-}" == "Apple_Terminal" ]] && tty_name="$(tty)"; then
  (
    # Closing the window hangs up this terminal; the helper must outlive that.
    trap '' HUP
    # Let this script finish first, or Terminal asks before closing a busy window.
    sleep 0.5
    osascript - "$tty_name" <<'APPLESCRIPT'
on run argv
  set target to item 1 of argv
  tell application "Terminal"
    repeat with w in windows
      -- Only a window holding nothing but this session.
      if (count of tabs of w) is 1 and (tty of tab 1 of w) is target then
        close w
        exit repeat
      end if
    end repeat
  end tell
end run
APPLESCRIPT
  ) </dev/null >/dev/null 2>&1 &
fi

exit $status
