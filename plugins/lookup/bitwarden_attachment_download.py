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
  version_added: 2.18.1
  description:
    - gets an attachment from bitwarden, copies it to ramdisk cache
    - then returns the path to that ramdisk cache file
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
    - unity.general.ramdisk_cache
"""

import subprocess
from ansible.utils.display import Display
from ansible.plugins.lookup import LookupBase
from ansible.plugins.loader import lookup_loader

from ansible_collections.unity.general.plugins.plugin_utils.ramdisk_cache import (
    get_cache_path,
    cache_lambda,
)

display = Display()


class LookupModule(LookupBase):
    def download_attachment(self, bw_item_id, bw_attachment_filename) -> str:
        download_path = get_cache_path(f"{bw_item_id}.{bw_attachment_filename}", self.get_options())
        argv = [
            "bw",
            "get",
            "attachment",
            bw_attachment_filename,
            "--itemid",
            bw_item_id,
            "--output",
            download_path,
        ]
        display.v(f"executing command: {argv}")
        subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        display.v(f"done.")
        return download_path

    def run(self, terms, variables=None, **kwargs):
        self.set_options(direct=kwargs)
        bw_item_name = self.get_option("item_name")
        bw_attachment_filename = self.get_option("attachment_filename")

        bw_item_id = lookup_loader.get("unity.general.bitwarden").run(
            [bw_item_name], variables, field="id"
        )[0]

        output = cache_lambda(
            f"{bw_item_id}.{bw_attachment_filename}",
            "bitwarden.json",
            f"bitwarden_download_attachment_{bw_item_id}.{bw_attachment_filename}",
            lambda: self.download_attachment(bw_item_id, bw_attachment_filename),
            self.get_options(),
        )

        # ansible requires that lookup returns a list
        return [output]
