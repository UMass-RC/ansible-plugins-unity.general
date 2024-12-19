DOCUMENTATION = """
  name: bitwarden
  author: Simon Leary <simon.leary42@proton.me>
  requirements:
    - bw (command line utility)
    - be logged into bitwarden
    - bitwarden vault unlocked
    - E(BW_SESSION) environment variable set
    - P(community.general.bitwarden#lookup)
  short_description: retrieves binary secrets from bitwarden
  version_added: 2.17.3
  description:
    - gets an attachment from bitwarden, copies it to ramdisk cache
    - then returns the content of that file in base64
    - the `bw` command is slow and cannot be used in parallel, but this plugin uses ramdisk cache
    - so it is fast and safe in parallel.
  options:
    item_name:
      desctiption: bitwarden item name
      type: str
      required: true
    attachment_filename:
      description: filename of the desired attachment
      type: str
      required: true
  notes: []
  seealso:
    - plugin: community.general.bitwarden
      plugin_type: lookup
    - plugin: unity.general.bitwarden
      plugin_type: lookup
  extends_documentation_fragment:
    - unity.general.ramdisk_cached_lookup
"""

import os
import base64
import subprocess

from ansible.plugins.loader import lookup_loader
from ansible.utils.display import Display
from ansible.module_utils.common.text.converters import to_text

from ansible_collections.unity.general.plugins.plugin_utils.ramdisk_cached_lookup import (
    RamDiskCachedLookupBase,
    get_cache_path,
)

display = Display()


class LookupModule(RamDiskCachedLookupBase):
    def get_attachment_base64(self, bw_item_id, bw_attachment_filename) -> str:
        tempfile_path = get_cache_path(f"{bw_item_id}.{bw_attachment_filename}")
        open(tempfile_path, "w").close()
        os.chmod(tempfile_path, 0o600)
        display.v(f"got tempfile for attachment download: '{tempfile_path}'.")
        argv = [
            "bw",
            "get",
            "attachment",
            bw_attachment_filename,
            "--itemid",
            bw_item_id,
            "--output",
            tempfile_path,
        ]
        display.v(f"executing command: {argv}")
        subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        display.v(f"done.")
        with open(tempfile_path, "rb") as fd:
            output = base64.b64encode(fd.read()).decode("utf8")
        return output

    def run(self, terms, variables=None, **kwargs):
        self.set_options(direct=kwargs)
        bw_item_name = self.get_option("item_name")
        bw_attachment_filename = self.get_option("attachment_filename")

        bw_item_id = lookup_loader.get("unity.general.bitwarden").run(
            [bw_item_name], variables, field="id"
        )[0]

        output = self.cache_lambda(
            f"{bw_item_id}.{bw_attachment_filename}",
            get_cache_path("bitwarden"),
            lambda: self.get_attachment_base64(bw_item_id, bw_attachment_filename),
        )

        # add decoded string to cache so that slack callback plugin can redact it
        # if not text then it should not be included in the task result
        is_text = False
        decoded_bytes = base64.b64decode(output)
        try:
            decoded_str = to_text(decoded_bytes)
            is_text = True
        except:
            pass
        if is_text:
            self.cache_lambda(
                f"{bw_item_id}.{bw_attachment_filename}-decoded",
                get_cache_path("bitwarden"),
                lambda: decoded_str,
            )

        # ansible requires that lookup returns a list
        return [output]
