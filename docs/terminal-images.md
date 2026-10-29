# Which terminal shows images best

Opengrader shows each student's scanned answer *inside* the terminal and picks the best image protocol automatically.

| Image quality | Terminals |
| --- | --- |
| Full resolution (Kitty protocol) | [Ghostty](https://ghostty.org/), [kitty](https://sw.kovidgoyal.net/kitty/), Contour |
| Full resolution (iTerm2 protocol) | [iTerm2](https://iterm2.com/), [WezTerm](https://wezfurlong.org/wezterm/), VS Code* |
| Full resolution (Sixel) | Konsole, foot, mlterm, Windows Terminal 1.22+, Rio, XTerm |
| Coarse preview | macOS Terminal.app, GNOME Terminal, PuTTY, tmux |

In a coarse-preview terminal, press **`o`** while grading to open the full-resolution image in your normal image viewer.

\* VS Code needs the `terminal.integrated.enableImages` setting ([details](https://code.visualstudio.com/docs/terminal/advanced#_image-support)).

Check what was detected (and include this in any bug report):

```bash
opengrader tui --check-graphics
```
