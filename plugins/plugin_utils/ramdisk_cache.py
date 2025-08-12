import os
import json
import time
import fcntl
import getpass
import platform
import threading

from typing import IO

from ansible.errors import AnsibleError
from ansible.utils.display import Display

display = Display()

"""
this cache system can be seen as a nested dictionary. each cache file is a dictionary, and
get_cache_path provides a mapping from a simple name like "bitwarden" to a fully qualified path.
"""


def display_verbose(x: str, id):
    display.v(f"[{os.getpid()}.{threading.get_ident()}] ({id}) {x}")


def get_cache_path(basename: str, plugin_options: dict) -> str:
    """
    example: basename="bitwarden.json" -> "$XDG_RUNTIME_DIR/bitwarden.json"
    the "ramdisk_cache_path" plugin option can overide the dirname in the above example

    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment
    """
    if plugin_options["ramdisk_cache_path"] is not None:
        ramdisk_path = plugin_options["ramdisk_cache_path"]
    else:
        if "XDG_RUNTIME_DIR" in os.environ:
            ramdisk_path = os.environ["XDG_RUNTIME_DIR"]
        else:
            _os = platform.system().lower()
            if _os == "linux":
                ramdisk_path = f"/dev/shm/{getpass.getuser()}"
                if not os.path.isdir(ramdisk_path):
                    os.mkdir(ramdisk_path)
                    os.chmod(ramdisk_path, 0o700)
            elif _os == "darwin":
                ramdisk_path = os.path.expanduser("~/.tmpdisk/shm")
                if not os.path.isdir(ramdisk_path):
                    raise AnsibleError(
                        f'"{ramdisk_path}" is not a directory! create it with [tmpdisk](https://github.com/imothee/tmpdisk)'
                    )
            else:
                raise AnsibleError(f"ramdisk_cache: unsupported OS: {OS}")
    return os.path.join(ramdisk_path, basename)


class RamdiskCacheContextManager:
    """
    enter:
        assert plugin_options["enable_cache"] == True
        create file if it doesn't exist
        assert file owner is current user
        chmod 600
        truncate the file if its mtime is older than the plugin_options["cache_timeout_seconds"]
        open the file in r+ mode
        lock the file
        return the file

    exit:
        unlock the file
        flush the file
        close the file
        return None

    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment
    """

    def __init__(self, basename: str, id: str, plugin_options: dict, needs_write=True):
        self.cache_path = get_cache_path(basename, plugin_options)
        self.plugin_options = plugin_options
        if needs_write:
            self.flock_flag = fcntl.LOCK_EX
            self.lock_type = "write"
            self.open_mode = "r+"  # read and write but don't truncate
        else:
            self.flock_flag = fcntl.LOCK_SH
            self.lock_type = "read"
            self.open_mode = "r"
        self.id = id
        self.cache_file = None

    def display_verbose(self, x):
        display_verbose(x, self.id)

    def __enter__(self) -> IO:
        if self.plugin_options["enable_cache"] is False:
            raise AnsibleError("cannot lock/open cache because caching is disabled!")
        cache_timeout_seconds = self.plugin_options["cache_timeout_seconds"]
        if os.path.exists(self.cache_path):
            cache_path_owner_uid = os.stat(self.cache_path).st_uid
            if os.getuid() != cache_path_owner_uid:
                raise AnsibleError(
                    f'another user (uid={cache_path_owner_uid}) already owns the file "{self.cache_path}"!'
                )
            if (cache_timeout_seconds > 0) and (
                (time.time() - os.path.getmtime(self.cache_path)) > cache_timeout_seconds
            ):
                self.display_verbose(f"cache timed out, truncating...")
                open(self.cache_path, "w").close()
        else:
            open(self.cache_path, "w").close()
        os.chmod(self.cache_path, 0o600)
        os.utime(self.cache_path, times=(time.time(), time.time()))  # update atime and mtime to now
        self.cache_file = open(self.cache_path, self.open_mode)
        self.display_verbose(f"acquiring {self.lock_type} lock on file '{self.cache_path}'...'")
        fcntl.flock(self.cache_file, self.flock_flag)
        self.display_verbose(f"{self.lock_type} lock acquired on file '{self.cache_path}'.'")
        return self.cache_file

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.display_verbose(f"releasing {self.lock_type} lock on file '{self.cache_path}'... ")
        fcntl.flock(self.cache_file, fcntl.LOCK_UN)
        self.cache_file.flush()
        self.cache_file.close()
        if exc_type is not None:
            raise exc_value.with_traceback(traceback)


def cache_lambda(key, basename: str, id: str, lambda_func, plugin_options: dict):
    """
    run the lambda function and cache the result in memory
    if the result is cached, don't run the function

    key: unique key for the cache
    lambda_func: function that returns value for key

    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment

    both the key and the return value of the lambda_func must be JSON serializable!
    """
    if plugin_options["enable_cache"] is False:
        display_verbose(f"cache is disabled", key)
        return lambda_func()
    # One might be tempted to use a read-only lock for checking cache hit or cache miss
    # and then a write lock for populating the cache.
    # That would be wrong because in the time between the 1st and 2nd locks, another thread
    # may detect a cache miss and also decide it also wants to poulate the cache.
    # It's important that once a cache miss is detected, the cache is populated by the same thread
    # before any other cache misses can occur for any other threads
    with RamdiskCacheContextManager(basename, id, plugin_options, needs_write=True) as cache_file:
        try:
            cache_file.seek(0)
            cache_contents = cache_file.read()
            cache = json.loads(cache_contents)
        except json.JSONDecodeError as e:
            display_verbose(f"failed to parse '{cache_contents}', will be overwritten.\n{e}", key)
            cache = {}
        if key in cache:
            display_verbose(f"cache hit", key)
            return cache[key]
        display_verbose(f"cache miss", key)
        result = lambda_func()
        cache[key] = result
        cache_file.seek(0)
        cache_file.truncate()
        json.dump(cache, cache_file)
    return result
