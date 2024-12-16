try:
    from ClusterShell.NodeSet import NodeSet

    DO_NODESET = True

except ImportError:
    print("unable to import clustershell. hostname lists will not be folded.", file=sys.stderr)

    DO_NODESET = False


def format_hostnames(hosts) -> str:
    if DO_NODESET:
        return str(NodeSet.fromlist(sorted(list(hosts))))
    else:
        return ",".join(sorted(list(hosts)))
