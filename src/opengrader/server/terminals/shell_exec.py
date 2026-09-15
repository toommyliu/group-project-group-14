"""Acquire the PTY in a fresh process, then replace it with the shell."""

import fcntl
import os
import sys
import termios

if __name__ == "__main__":
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.execv(sys.argv[1], sys.argv[1:])
