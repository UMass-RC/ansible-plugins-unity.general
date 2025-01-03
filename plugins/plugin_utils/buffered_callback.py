import os
import sys
import stat
from ansible.utils.display import Display
from ansible.plugins.callback import CallbackBase
from contextlib import redirect_stdout, redirect_stderr


def stdout_stderr_to_string(lambda_func):
    if stat.S_ISFIFO(os.stat(sys.stdout.fileno()).st_mode):
        # if it's already a pipe, don't redirect
        lambda_func()
        return ""
    read_fd, write_fd = os.pipe()
    with os.fdopen(write_fd, "w") as write_pipe:
        with redirect_stdout(write_pipe):
            with redirect_stderr(write_pipe):
                lambda_func()
    with os.fdopen(read_fd, "r") as read_pipe:
        return read_pipe.read()


class Display2Buffer:
    def __init__(self):
        self._display = Display()
        self.buffer = ""

    def display(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.display(*args, **kwargs))

    def v(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.v(*args, **kwargs))

    def vv(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.vv(*args, **kwargs))

    def vvv(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.vvv(*args, **kwargs))

    def vvvv(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.vvvv(*args, **kwargs))

    def vvvvv(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.vvvvv(*args, **kwargs))

    def vvvvvv(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.vvvvvv(*args, **kwargs))

    def verbose(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.verbose(*args, **kwargs))

    def debug(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.debug(*args, **kwargs))

    def deprecated(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.deprecated(*args, **kwargs))

    def warning(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.warning(*args, **kwargs))

    def system_warning(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: self._display.system_warning(*args, **kwargs)
        )

    def banner(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.banner(*args, **kwargs))

    def banner_cowsay(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.banner_cowsay(*args, **kwargs))

    def error(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(lambda: self._display.error(*args, **kwargs))


class BufferedCallback(CallbackBase):
    """
    when using self._display2 rather than self._display,
    output is added to an internal buffer rather than printed to stdout/stderr
    """

    def __init__(self):
        super(BufferedCallback, self).__init__()
        self._display2 = Display2Buffer()

    def display_buffer(self):
        super(Display2Buffer, self._display).display(self._display2.buffer)
        self._display2.buffer = ""


class NonBufferedCallback(CallbackBase):
    """
    this class does nothing, but it makes it easy to switch to BufferedCallback once you're already
    using self._display2 rather than self._display
    if you inherit both Buffered and NonBuffered, Buffered takes precedence
    """

    def __init__(self):
        super(NonBufferedCallback, self).__init__()
        if isinstance(self, BufferedCallback):
            return
        self._display2 = self._display
