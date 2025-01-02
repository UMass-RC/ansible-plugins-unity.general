import os
import json
import time
import fcntl
import getpass
import subprocess

from ansible.errors import AnsibleError
from ansible.utils.display import Display

display = Display()

username = getpass.getuser()

"""
this cache system can be seen as a nested dictionary. each cache file is a dictionary, and
get_cache_path provides a mapping from a simple name like "bitwarden" to a fully qualified path.

the API here is a bit strange. originally this inherited from LookupBase, but when I implemented
slack-callback/bitwarden-lookup integration, this needed to be usable by more than just one
type of plugin. Taking plugin_options as an argument allows for other plugins to use this utility
whithout worrying about fetching the value of every plugin option this utility supports.
In an effort to avoid magic hard coding of the bitwarden cache path into
the slack plugin, I exposed `get_cache_path`, and forced the bitwarden lookup to use it. this way
the only magic value in the slack plugin was the string "bitwarden".
"""

UNAME2RAMDISK_PATH = {
    "linux": "/dev/shm",
    "darwin": "~/.tmpdisk/shm",  # https://github.com/imothee/tmpdisk
}


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
        try:
            uname = subprocess.check_output("uname", text=True).strip().lower()
        except FileNotFoundError as e:
            raise AnsibleError("unsupported operating system: `uname` command not found.") from e
        try:
            ramdisk_path = os.path.expanduser(UNAME2RAMDISK_PATH[uname])
        except KeyError as e:
            raise AnsibleError(
                f'unsupported OS: "{uname}". supported: {UNAME2RAMDISK_PATH.keys()}'
            ) from e
        if not os.path.isdir(ramdisk_path):
            if uname == "darwin":
                raise AnsibleError(
                    f'"{ramdisk_path}" is not a directory! create it with [tmpdisk](https://github.com/imothee/tmpdisk)'
                )
            else:
                raise AnsibleError(f'"{ramdisk_path}" is not a directory!')
    return os.path.join(ramdisk_path, f".{name}-{username}")


def lock_cache_open_file(cache_path: str, plugin_options: dict):
    """
    assert plugin_options["enable_cache"] == True
    create file if it doesn't exist
    assert file owner is current user
    chmod 600
    truncate the file if its mtime is older than the plugin_options["cache_timeout_seconds"]
    open the file in r+ mode
    lock the file
    return the file

    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment
    """
    if plugin_options["enable_cache"] is False:
        raise RuntimeError("cannot lock/open cache because caching is disabled!")
    cache_timeout_seconds = plugin_options["cache_timeout_seconds"]
    if os.path.exists(cache_path):
        cache_path_owner_uid = os.stat(cache_path).st_uid
        if os.getuid() != cache_path_owner_uid:
            raise AnsibleError(
                f'another user (uid={cache_path_owner_uid}) already owns the file "{cache_path}"!'
            )
        if (cache_timeout_seconds > 0) and (
            (time.time() - os.path.getmtime(cache_path)) > cache_timeout_seconds
        ):
            display.v(f"cache timed out, truncating...")
            open(cache_path, "w").close()
    else:
        open(cache_path, "w").close()
    os.chmod(cache_path, 0o600)
    cache_file = open(cache_path, "r+")  # read and write but don't truncate
    fcntl.flock(cache_file, fcntl.LOCK_EX)
    return cache_file


def unlock_cache_close_file(cache_file) -> None:
    fcntl.flock(cache_file, fcntl.LOCK_UN)
    cache_file.flush()
    cache_file.close()


def cache_lambda(key, cache_path: str, lambda_func, plugin_options: dict):
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
        display.v(f"({key}) cache is disabled")
        return lambda_func()
    display.v(f"({key}) acquiring lock on file '{cache_path}'...'")
    cache_file = lock_cache_open_file(cache_path, plugin_options)
    display.v(f"({key}) lock acquired on file '{cache_path}'.'")
    try:
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
        cache_file.flush()
    finally:
        display.v(f"({key}) releasing lock on file '{cache_path}'... ")
        unlock_cache_close_file(cache_file)
    return result
