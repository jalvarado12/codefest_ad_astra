"""Windows stub for POSIX termios, so google-colab-cli imports on Windows.

colab_cli.console imports termios/tty at module scope but only calls into them
when stdin is a real TTY (interactive `colab console`). Non-interactive commands
never touch these, so empty shims are enough.
"""
TCSANOW = 0
TCSADRAIN = 1
TCSAFLUSH = 2


class error(Exception):
    pass


def tcgetattr(fd):
    raise error("termios is not available on Windows")


def tcsetattr(fd, when, attributes):
    raise error("termios is not available on Windows")
