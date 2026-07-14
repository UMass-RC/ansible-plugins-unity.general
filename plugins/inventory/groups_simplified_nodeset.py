DOCUMENTATION = """
    name: groups_simplified_nodeset
    author:
      - Simon Leary <simon.leary42@proton.me>
    short_description: simplified ansible inventory with folded node set support
    requirements:
      - clustershell
    description: |
      this is for adding hosts to groups and adding child groups to parent groups, nothing more.
      the syntax is similar to a normal ansible yaml inventory, except that variables cannot be
      defined, lists (or strings) are used in place of dictionaries, and the "groups" key is used.
      folded node sets can be used to denote ranges of hosts
      https://clustershell.readthedocs.io/en/v1.8.4/api/NodeSet.html
"""

EXAMPLES = """
plugin: groups_simplified_nodeset
groups:
  groupA:
    hosts:
      - host1
    children:
      - groupB
  groupB:
    hosts: host2
"""

import yaml
from ansible.errors import AnsibleError
from ClusterShell.NodeSet import NodeSet
from ansible.plugins.inventory import BaseInventoryPlugin


class InventoryModule(BaseInventoryPlugin):
    """defines parent/child group relationships"""

    def verify_file(self, path):
        """Return the possibly of a file being consumable by this plugin."""
        return super(InventoryModule, self).verify_file(path) and path.endswith((".yaml", ".yml"))

    def validate(self, data) -> None:
        if set(data.keys()) != set(["groups", "plugin"]):
            raise AnsibleError(f'expected keys "groups" and "plugin". found keys "{data.keys()}"')
        for group_name, group_data in data["groups"].items():
            if not isinstance(group_name, str):
                raise AnsibleError(
                    f'expected string. found groups.keys()[{i}] = "{group_name}" of type {type(group_name)}'
                )
            for i, (key, val) in enumerate(group_data.items()):
                if not isinstance(key, str):
                    raise AnsibleError(
                        f'expected string. found groups["{group_name}"].keys()[{i}] = "{key}" of type {type(key)}'
                    )
                if key not in ["hosts", "children"]:
                    raise AnsibleError(
                        f'expected "hosts" or "children". found groups["{group_name}"].keys()[{i}] = "{key}" of type {type(key)}'
                    )
                if not isinstance(val, list):
                    raise AnsibleError(
                        f'expected list. found groups["{group_name}"]["{key}"] = "{val}" of type "{type(val)}"'
                    )
                if isinstance(val, list):
                    for j, element in enumerate(val):
                        if not isinstance(element, str):
                            raise AnsibleError(
                                f'expected string. found groups["{group_name}"]["{key}"][{j}] = "{element}" of type "{type(element)}"'
                            )

    def parse(self, inventory, loader, path, cache=True):
        super(InventoryModule, self).parse(inventory, loader, path, cache)
        with open(path, "r", encoding="utf8") as fp:
            data = yaml.safe_load(fp)
        self.validate(data)
        for group_name, group_data in data["groups"].items():
            self.inventory.add_group(group_name)
            for child in group_data.get("children", []):
                host_with_same_same = self.inventory.get_host(child)
                if host_with_same_same is not None:
                    raise AnsibleError(
                        f'group "{child}" cannot be added to parent group "{group_name}" because there is already a host by the same name.'
                    )
                self.inventory.add_group(child)
                self.inventory.add_child(group_name, child)
        for group_name, group_data in data["groups"].items():
            for host_folded in group_data.get("hosts", []):
                for host in NodeSet(host_folded):  # unfold ranges
                    if host in inventory.groups:
                        raise AnsibleError(
                            f'host "{host}" cannot be added to group "{group_name}" because there is already a group by the same name.'
                        )
                    self.inventory.add_host(host, group=group_name)
