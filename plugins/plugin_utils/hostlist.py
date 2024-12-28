from ansible.utils.display import Display

display = Display()

try:
    from ClusterShell.NodeSet import NodeSet

    DO_NODESET = True

except ImportError:
    display.warning("unable to import clustershell. hostname lists will not be folded.")

    DO_NODESET = False


def format_hostnames(hosts) -> str:
    if DO_NODESET:
        return str(NodeSet.fromlist(sorted(list(hosts))))
    else:
        return ",".join(sorted(list(hosts)))
