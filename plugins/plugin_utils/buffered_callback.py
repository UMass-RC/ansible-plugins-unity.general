import os
import sys
import stat
from ansible.utils.display import Display
from ansible.plugins.callback import CallbackBase
from contextlib import redirect_stdout, redirect_stderr


def capture(func, *args, **kwargs):
    "redirect stdout and stderr to a named pipe and return the contents of that pipe as a string"
    if stat.S_ISFIFO(os.stat(sys.stdout.fileno()).st_mode):
        # if it's already a pipe, don't redirect
        func(*args, **kwargs)
        return ""
    read_fd, write_fd = os.pipe()
    with os.fdopen(write_fd, "w") as write_pipe:
        with redirect_stdout(write_pipe):
            with redirect_stderr(write_pipe):
                func(*args, **kwargs)
    with os.fdopen(read_fd, "r") as read_pipe:
        return read_pipe.read()


class Display2Buffer:
    """
    overloads the Display class to capture what would be stdout/stderr output into a buffer
    this can't be done with normal inheritance because Display is a Singleton and this can't
    be a Singleton
    """

    def __init__(self):
        self._display = Display()
        self.buffer = ""
        functions_to_capture = [
            "display",
            "v",
            "vv",
            "vvv",
            "vvvv",
            "vvvvv",
            "vvvvvv",
            "verbose",
            "debug",
            "deprecated",
            "warning",
            "system_warning",
            "banner",
            "banner_cowsay",
            "error",
        ]
        for attr_name in dir(self._display):
            if attr_name.startswith("__"):
                continue
            if callable(getattr(self._display, attr_name)):
                if attr_name in functions_to_capture:
                    self._make_captured_wrapper_function(attr_name)
                else:
                    self._make_wrapper_function(attr_name)
            else:
                self._make_property(attr_name)

    def _make_captured_wrapper_function(self, attr_name):
        def _wrapper_function(*args, **kwargs):
            self.buffer += capture(getattr(self._display, attr_name), *args, **kwargs)

        setattr(self, attr_name, _wrapper_function)

    def _make_wrapper_function(self, attr_name):
        def _wrapper_function(*args, **kwargs):
            return getattr(self._display, attr_name)(*args, **kwargs)

        setattr(self, attr_name, _wrapper_function)

    def _make_property(self, attr_name):
        setattr(
            self.__class__, attr_name, property(lambda _self: getattr(_self._display, attr_name))
        )


class BufferedCallback(CallbackBase):
    """
    output is added to an internal buffer rather than printed to stdout/stderr
    be sure to call self.display_buffer()
    """

    def __init__(self):
        super(BufferedCallback, self).__init__()
        self._real_display = self._display
        self._display = Display2Buffer()

    def display_buffer(self):
        self._real_display.display(self._display.buffer)
        self._display.buffer = ""
