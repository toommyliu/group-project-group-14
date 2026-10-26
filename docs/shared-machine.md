# Grading with other accounts on one computer

When several people grade on one machine, each logged in with their own account:

1. **Professor (once):** put the class folder where every grader can reach it (for example `/srv/cmpe195`), make a group for the graders, then share the workspace:
   ```bash
   sudo groupadd cmpe195-graders
   sudo usermod -aG cmpe195-graders alice bob     # graders (and yourself)
   opengrader tui /srv/cmpe195/quiz3              # opens it, and starts your Opengrader
   opengrader share --group cmpe195-graders /srv/cmpe195/quiz3
   ```
2. **Each grader:** `opengrader tui /srv/cmpe195/quiz3` from their own account. They join the professor's Opengrader, so only the professor's account writes the files, and the history shows who gave each grade.

Graders give grades where there are none and change their own. A change to a grade someone else gave is sent to the professor, who approves or dismisses it with **Shift+Q**. `opengrader share --instructor <login>` makes someone an instructor, `--trust <login>` lets a grader change any grade, and `--off` stops sharing. Graders can join while the professor's Opengrader runs; it keeps running in the background and stops with `opengrader stop`. On Linux, Opengrader checks with the system which account each grader really is.

Over SSH, Opengrader asks your terminal (on your own computer) whether it can show images. That works on a slow connection, but you can skip it: put `export OPENGRADER_GRAPHICS=kitty` (or `sixel`, `iterm2`, `halfcell`) in your shell profile on the server. `opengrader tui --check-graphics` shows what was detected and how long it took.

In a plain class folder (`opengrader classic`), each account writes the files itself: the folder must be group-writable (`chgrp -R`, `chmod -R g+rwX`, `umask 002`). Opengrader says when it opens a folder your account cannot write.
