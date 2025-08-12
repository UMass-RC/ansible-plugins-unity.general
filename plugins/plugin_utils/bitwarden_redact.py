import json
import datetime

from ansible.utils.display import Display
from ansible_collections.unity.general.plugins.plugin_utils.ramdisk_cache import (
    RamdiskCacheContextManager,
)

display = Display()


def _get_bitwarden_secrets(plugin_options: dict):
    with RamdiskCacheContextManager(
        "bitwarden", "bitwarden_redact", plugin_options, needs_write=False
    ) as cache_file:
        try:
            bitwarden_cache = json.load(cache_file)
        except json.JSONDecodeError as e:
            display.debug(f"assuming bitwarden cache is empty due to json decode error: {str(e)}")
            return []
        secrets = []
        for value in bitwarden_cache.values():
            if isinstance(value, list):
                secrets += value
            else:
                secrets.append(value)
    return [x.strip() for x in secrets]


def bitwarden_redact(x: object, plugin_options: dict) -> str:
    """
    any secrets currently in bitwarden cache will be removed from object x
    x must be JSON serializable

    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.ramdisk_cache documentation fragment
    """
    num_secrets_redacted = 0
    start_time = datetime.datetime.now()
    x_json_str = json.dumps(x)
    for secret in _get_bitwarden_secrets(plugin_options):
        if secret in x_json_str:
            x_json_str = x_json_str.replace(secret, "REDACTED")
            num_secrets_redacted += 1
    seconds_elapsed = (datetime.datetime.now() - start_time).total_seconds()
    display.v(
        f"it took {seconds_elapsed:.1f} seconds to remove {num_secrets_redacted} bitwarden secrets from a string of length {len(x)}."
    )
    return json.loads(x_json_str)
