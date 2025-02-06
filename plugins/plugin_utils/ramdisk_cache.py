import os
import json
import time
import fcntl
import getpass
import platform

from typing import IO

from ansible.errors import AnsibleError
from ansible.utils.display import Display

display = Display()

username = getpass.getuser()

"""
this cache system can be seen as a nested dictionary. each cache file is a dictionary, and
get_cache_path provides a mapping from a simple name like "bitwarden" to a fully qualified path.
"""

OS = platform.system().lower()
if OS == "linux":
    DEFAULT_RAMDISK_PATH = "/dev/shm"
elif OS == "darwin":
    DEFAULT_RAMDISK_PATH = os.path.expanduser("~/.tmpdisk/shm")
else:
    raise AnsibleError(f"ramdisk_cache: unsupported OS: {OS}")


def get_cache_path(name: str, plugin_options: dict) -> str:
    """
    example: "bitwarden" -> "/dev/shm/.bitwarden-username"
    the "ramdisk_cache_path" plugin option can override "/dev/shm" in the above example

    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment
    """
    if plugin_options["ramdisk_cache_path"] is not None:
        ramdisk_path = plugin_options["ramdisk_cache_path"]
    else:
        ramdisk_path = DEFAULT_RAMDISK_PATH
        # special warning to install special program on macos
        if OS == "darwin" and not os.path.isdir(DEFAULT_RAMDISK_PATH):
            raise AnsibleError(
                f'"{DEFAULT_RAMDISK_PATH}" is not a directory! create it with [tmpdisk](https://github.com/imothee/tmpdisk)'
            )
    return os.path.join(ramdisk_path, f".{name}-{username}")


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

    def __init__(self, cache_name: str, plugin_options: dict, needs_write=True, name="anon"):
        self.cache_path = get_cache_path(cache_name, plugin_options)
        self.plugin_options = plugin_options
        if needs_write:
            self.flock_flag = fcntl.LOCK_EX
            self.lock_type = "write"
            self.open_mode = "r+"  # read and write but don't truncate
        else:
            self.flock_flag = fcntl.LOCK_SH
            self.lock_type = "read"
            self.open_mode = "r"
        self.name = name
        self.cache_file = None

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
                display.v(f"cache timed out, truncating...")
                open(self.cache_path, "w").close()
        else:
            open(self.cache_path, "w").close()
        os.chmod(self.cache_path, 0o600)
        os.utime(self.cache_path, times=(time.time(), time.time()))  # update atime and mtime to now
        self.cache_file = open(self.cache_path, self.open_mode)
        display.v(f"({self.name}) acquiring {self.lock_type} lock on file '{self.cache_path}'...'")
        fcntl.flock(self.cache_file, self.flock_flag)
        display.v(f"({self.name}) {self.lock_type} lock acquired on file '{self.cache_path}'.'")
        return self.cache_file

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        display.v(f"({self.name}) releasing {self.lock_type} lock on file '{self.cache_path}'... ")
        fcntl.flock(self.cache_file, fcntl.LOCK_UN)
        self.cache_file.flush()
        self.cache_file.close()
        if exc_type is not None:
            raise exc_value.with_traceback(traceback)


def cache_lambda(key, cache_name: str, lambda_func, plugin_options: dict, name=None):
    """
    run the lambda function and cache the result in memory
    if the result is cached, don't run the function

    key: unique key for the cache
    lambda_func: function that returns value for key

    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment

    both the key and the return value of the lambda_func must be JSON serializable!
    """
    if name is None:
        name = key
    if plugin_options["enable_cache"] is False:
        display.v(f"({key}) cache is disabled")
        return lambda_func()
    with RamdiskCacheContextManager(cache_name, plugin_options, name=name) as cache_file:
        try:
            cache_file.seek(0)
            cache_contents = cache_file.read()
            cache = json.loads(cache_contents)
        except json.JSONDecodeError as e:
            display.v(f"({key}) failed to parse cache. contents may be overwritten.\n{e}")
            display.v(cache_contents)
            cache = {}
        if key in cache:
            display.v(f"({key}) cache hit")
            return cache[key]
        display.v(f"({key}) cache miss")
        result = lambda_func()
        cache[key] = result
        cache_file.seek(0)
        cache_file.truncate()
        json.dump(cache, cache_file)
    return result
