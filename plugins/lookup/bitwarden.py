DOCUMENTATION = """
  name: bitwarden
  author: Simon Leary <simon.leary42@proton.me>
  requirements:
    - bw (command line utility)
    - be logged into bitwarden
    - bitwarden vault unlocked
    - E(BW_SESSION) environment variable set
    - P(community.general.bitwarden#lookup)
  short_description: Retrieve secrets from Bitwarden
  version_added: 2.17.3
  description:
    - wrapper around P(community.general.bitwarden#lookup)
    - all options not mentioned here are passed directly to P(community.general.bitwarden#lookup)
    - the `bw` command is slow and cannot be used in parallel, but this plugin uses ramdisk cache
    - so it is fast and safe in parallel.
  options:
    _terms:
      description: item names to search for. exactly 1 item name required!
      required: true
      type: list
      elements: str
    default_collection_id:
        description: if no collection ID is given to a task, use this one by default
        ini:
          - section: bitwarden
            key: default_collection_id
        env:
          - name: BITWARDEN_DEFAULT_COLLECTION_ID
        required: false
        type: string
  notes: []
  seealso:
    - plugin: community.general.bitwarden
      plugin_type: lookup
  extends_documentation_fragment:
    - unity.general.ramdisk_cached_lookup
"""

import hashlib

from ansible.plugins.loader import lookup_loader
from ansible.errors import AnsibleError
from ansible.utils.display import Display

from ansible_collections.unity.general.plugins.plugin_utils.ramdisk_cached_lookup import (
    RamDiskCachedLookupBase,
    get_cache_path,
)

display = Display()


def make_shell_command(terms, **kwargs) -> str:
    """
    make a shell command which is equivalent to the logic of the community.general.bitwarden lookup
    purely for user debugging purposes
    """
    subcommands = ["bw sync"]
    for term in terms:
        subcommand = f"bw list items --search='{term}'"
        if "collection_id" in kwargs:
            subcommand += f" --collectionid='{kwargs['collection_id']}'"
        if "search" in kwargs:
            subcommand += f" | jq '.[] | select(.[\"{kwargs['search']}\"] == \"{term}\")'"
        if "field" in kwargs:
            subcommand += ' | jq \'.[] | {{"item[custom_fields][{x}]": .["fields"]["{x}"], "item[login][{x}]": .["login"]["{x}"], "item[{x}]": .["{x}"]}}\''.format(
                x=kwargs["field"],
            )
        subcommands.append(subcommand)
    return "; ".join(subcommands)


def do_bitwarden_lookup(terms, variables, **kwargs):
    display.v(f"running bitwarden lookup with terms: {terms} and kwargs: {kwargs}")
    results = lookup_loader.get("community.general.bitwarden").run(terms, variables, **kwargs)
    display.v("done.")
    # results is a nested list
    # the first index represents each term in terms
    # the second index represents each item that matches that term
    flat_results = []
    for result_list in results:
        flat_results += result_list
    if len(flat_results) == 0:
        raise AnsibleError(
            "\n".join(
                [
                    "",
                    "no results found!",
                    'make sure that your item is in the "Ansible" bitwarden collection, or specify a different collection ID.',
                    "also make sure you run `bw sync` to get recent changes from upstream.",
                    "feel free to double check my work by using the bitwarden CLI yourself:",
                    make_shell_command(terms, **kwargs),
                ]
            )
        )

    if len(flat_results) > 1:
        raise AnsibleError(
            "\n".join(
                [
                    "",
                    "expected single result but multiple results found!",
                    "to use multiple results, use the `community.general.bitwarden` lookup.",
                    "feel free to double check my work by using the bitwarden CLI yourself:",
                    make_shell_command(terms, **kwargs),
                ]
            )
        )
    # ansible requires that lookup returns a list
    return [flat_results[0]]


class LookupModule(RamDiskCachedLookupBase):

    def run(self, terms, variables=None, **kwargs):
        self.set_options(direct=kwargs)
        if len(terms) != 1:
            raise AnsibleError(f"exactly one posisional argument required. Given: {terms}")

        default_collection_id = self.get_option("default_collection_id")
        if "collection_id" not in kwargs and default_collection_id is not None:
            kwargs["collection_id"] = default_collection_id

        cache_key = hashlib.sha1((str(terms) + str(kwargs)).encode()).hexdigest()[:5]
        return self.cache_lambda(
            cache_key,
            get_cache_path("bitwarden"),
            lambda: do_bitwarden_lookup(terms, variables, **kwargs),
        )
