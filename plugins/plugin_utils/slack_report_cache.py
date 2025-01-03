import io

from ansible.utils.display import Display
from ansible_collections.unity.general.plugins.plugin_utils.ramdisk_cache import (
    get_cache_path,
    lock_cache_open_file,
    unlock_cache_close_file,
)

display = Display()


def add_line(line: str, plugin_options: dict, end="\n"):
    """
    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment
    """
    slack_report_cache_path = get_cache_path("slack-report", plugin_options)
    cache_file = lock_cache_open_file(slack_report_cache_path, plugin_options)
    cache_file.seek(0, io.SEEK_END)  # seek to end of file
    cache_file.write(line)
    cache_file.write(end)
    unlock_cache_close_file(cache_file)


def get_lines(plugin_options: dict):
    """
    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment
    """
    slack_report_cache_path = get_cache_path("slack-report", plugin_options)
    cache_file = lock_cache_open_file(slack_report_cache_path, plugin_options)
    lines = cache_file.read().strip().splitlines()  # don't include trailing "\n" in each line
    unlock_cache_close_file(cache_file)
    return lines


def flush(plugin_options: dict):
    """
    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment
    """
    slack_report_cache_path = get_cache_path("slack-report", plugin_options)
    cache_file = lock_cache_open_file(slack_report_cache_path, plugin_options)
    cache_file.truncate()
    unlock_cache_close_file(cache_file)
