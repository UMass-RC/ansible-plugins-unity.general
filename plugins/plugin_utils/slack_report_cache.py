import io
from hashlib import md5

from ansible.utils.display import Display
from ansible_collections.unity.general.plugins.plugin_utils.ramdisk_cache import (
    RamdiskCacheContextManager,
)

display = Display()


def add_line(line: str, plugin_options: dict, end="\n") -> None:
    """
    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment
    """
    name = f"slack_report_cache.add_line.{md5(line).hexdigest()[:5]}"
    with RamdiskCacheContextManager("slack-report", plugin_options, name=name) as cache_file:
        cache_file.seek(0, io.SEEK_END)  # seek to end of file
        cache_file.write(line)
        cache_file.write(end)


def get_lines(plugin_options: dict) -> list[str]:
    """
    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment
    """
    with RamdiskCacheContextManager(
        "slack-report", plugin_options, name="slack_report_cache.get_lines"
    ) as cache_file:
        lines = cache_file.read().strip().splitlines()  # don't include trailing "\n" in each line
    return lines


def flush(plugin_options: dict):
    """
    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment
    """
    with RamdiskCacheContextManager(
        "slack-report", plugin_options, name="slack_report_cache.flush"
    ) as cache_file:
        cache_file.truncate()
