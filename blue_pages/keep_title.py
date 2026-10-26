#!/usr/bin/env python3
"""Run a command keeping the terminal window title.

Usage: keep_title.py TITLE COMMAND [ARGS...]

Sets the title, then runs the command in a pseudo-terminal and drops the escape
sequences it prints to change the title (OSC 0, 1 and 2), such as the ones a
remote shell prints from its prompt. Everything else passes through untouched.
Standalone (no imports from the package): it runs inside the terminal emulator.
"""
import fcntl
import os
import pty
import select
import signal
import sys
import termios
import tty

TITLE_OSCS = (b"0", b"1", b"2")
# An unterminated OSC longer than this is passed through instead of buffered.
MAX_OSC = 4096


class TitleFilter:
    """Removes title-setting OSC sequences from a byte stream, even split across reads."""

    def __init__(self):
        self.pending = b""

    def feed(self, data):
        data = self.pending + data
        self.pending = b""
        out = bytearray()
        i = 0
        while True:
            start = data.find(b"\x1b]", i)
            if start < 0:
                # A trailing ESC may be the start of an OSC that continues in the next read.
                if data.endswith(b"\x1b") and i < len(data):
                    out += data[i:-1]
                    self.pending = b"\x1b"
                else:
                    out += data[i:]
                return bytes(out)
            out += data[i:start]
            # An OSC ends with BEL or with ST (ESC \).
            ends = [(pos, n) for pos, n in ((data.find(b"\x07", start + 2), 1),
                                            (data.find(b"\x1b\\", start + 2), 2)) if pos >= 0]
            if not ends:
                if len(data) - start > MAX_OSC:
                    out += data[start:]
                else:
                    self.pending = data[start:]
                return bytes(out)
            end, n = min(ends)
            if data[start + 2:end].split(b";", 1)[0] not in TITLE_OSCS:
                out += data[start:end + n]
            i = end + n


def write_all(fd, data):
    while data:
        data = data[os.write(fd, data):]


def main():
    title, argv = sys.argv[1], sys.argv[2:]
    write_all(sys.stdout.fileno(), b"\x1b]0;" + title.encode() + b"\x07")
    if not os.isatty(0):
        os.execvp(argv[0], argv)

    pid, master = pty.fork()
    if pid == 0:
        try:
            os.execvp(argv[0], argv)
        except OSError as e:
            print(f"{argv[0]}: {e.strerror}", file=sys.stderr)
        os._exit(127)

    def resize(*_):
        size = fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(master, termios.TIOCSWINSZ, size)

    resize()
    signal.signal(signal.SIGWINCH, resize)
    saved = termios.tcgetattr(0)
    tty.setraw(0)
    title_filter = TitleFilter()
    inputs = [0, master]
    try:
        while True:
            ready, _, _ = select.select(inputs, [], [])
            if master in ready:
                try:
                    data = os.read(master, 65536)
                except OSError:  # EIO: the command exited
                    break
                if not data:
                    break
                write_all(1, title_filter.feed(data))
            if 0 in ready:
                data = os.read(0, 65536)
                if data:
                    write_all(master, data)
                else:
                    inputs.remove(0)
    finally:
        termios.tcsetattr(0, termios.TCSAFLUSH, saved)

    _, status = os.waitpid(pid, 0)
    code = os.waitstatus_to_exitcode(status)
    sys.exit(128 - code if code < 0 else code)


if __name__ == "__main__":
    main()
