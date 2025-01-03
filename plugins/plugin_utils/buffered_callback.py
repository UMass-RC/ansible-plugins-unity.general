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


class Display2Buffer(Display):
    def __init__(self):
        super(Display2Buffer, self).__init__()
        self.buffer = ""

    def display(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).display(*args, **kwargs)
        )

    def v(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).v(*args, **kwargs)
        )

    def vv(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).vv(*args, **kwargs)
        )

    def vvv(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).vvv(*args, **kwargs)
        )

    def vvvv(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).vvvv(*args, **kwargs)
        )

    def vvvvv(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).vvvvv(*args, **kwargs)
        )

    def vvvvvv(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).vvvvvv(*args, **kwargs)
        )

    def verbose(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).verbose(*args, **kwargs)
        )

    def debug(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).debug(*args, **kwargs)
        )

    def deprecated(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).deprecated(*args, **kwargs)
        )

    def warning(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).warning(*args, **kwargs)
        )

    def system_warning(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).system_warning(*args, **kwargs)
        )

    def banner(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).banner(*args, **kwargs)
        )

    def banner_cowsay(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).banner_cowsay(*args, **kwargs)
        )

    def error(self, *args, **kwargs) -> None:
        self.buffer += stdout_stderr_to_string(
            lambda: super(Display2Buffer, self).error(*args, **kwargs)
        )


class BufferedCallback(CallbackBase):
    """
    output is added to self._display.buffer rather than printed to stdout/stderr
    """

    def __init__(self):
        super(BufferedCallback, self).__init__()
        self._old_display = self._display
        # Display has metaclass=Singleton, Display2Buffer should not be a singleton
        self._display = Display2Buffer.__new__(Display2Buffer)
        self._display.__init__()

    def display_buffer(self):
        self._old_display.display(self._display.buffer)
