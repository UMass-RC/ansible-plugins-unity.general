from collections.abc import Sequence

from ansible.inventory.host import Host
from ansible.utils.display import Display

display = Display()

try:
    from ClusterShell.NodeSet import NodeSet

    DO_NODESET = True

except ImportError:
    display.warning("unable to import clustershell. hostname lists will not be folded.")

    DO_NODESET = False


def format_hostnames(hosts: Sequence[str | Host]) -> str:
    names = sorted(str(x) for x in hosts)
    if DO_NODESET:
        return str(NodeSet.fromlist(names))
    else:
        return ",".join(names)
