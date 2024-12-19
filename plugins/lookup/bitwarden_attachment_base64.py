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
import tempfile
import subprocess


from ansible.plugins.lookup import LookupBase
from ansible.plugins.loader import lookup_loader
from ansible.errors import AnsibleError

from ansible_collections.unity.general.plugins.plugin_utils.ramdisk_cached_lookup import (
    RamDiskCachedLookupBase,
    get_ramdisk_path,
)

UNAME2TMPDIR = {
    "linux": "/dev/shm",
    "darwin": "~/.tmpdisk/shm",  # https://github.com/imothee/tmpdisk
}


class LookupModule(RamDiskCachedLookupBase):
    def get_attachment_base64(self, bw_item_id, bw_attachment_filename) -> str:
        tmpdir = self.get_cache_dir_path()
        fd, tempfile_path = tempfile.mkstemp(dir=tmpdir, prefix="snap.bw.")
        os.close(fd)
        os.chmod(tempfile_path, 0o600)
        subprocess.run(
            [
                "bw",
                "get",
                "attachment",
                bw_attachment_filename,
                "--itemid",
                bw_item_id,
                "--output",
                tempfile_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        with open(tempfile_path, "rb") as fd:
            output = base64.b64encode(fd.read()).decode("utf8")
        os.remove(tempfile_path)
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
            ".unity.general.cache",
            lambda: self.get_attachment_base64(bw_item_id, bw_attachment_filename),
        )

        # ansible requires that lookup returns a list
        return [output]
