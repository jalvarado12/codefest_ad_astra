"""Windows stub for POSIX tty (see termios.py in this directory)."""
import termios


def setraw(fd, when=termios.TCSAFLUSH):
    raise termios.error("tty is not available on Windows")


def setcbreak(fd, when=termios.TCSAFLUSH):
    raise termios.error("tty is not available on Windows")
