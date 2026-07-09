set -euo pipefail
trap 's=$?; echo "$0: Error on line "$LINENO": $BASH_COMMAND"; exit $s' ERR

function help() {
    echo "Usage: $0 [--restart]" >&2
}

restart=0
while (($# > 0)); do
    case "$1" in
        --help)
            help
            exit 0
            ;;
        --restart)
            restart=1
            ;;
        *)
            echo "Unknown argument: $1" >&2
            help
            exit 2
            ;;
    esac
    shift
done

tmpfile=/etc/shibboleth/filtered-incommon-metadata.xml.new
truncate --size 0 "$tmpfile"
chmod 644 "$tmpfile"
/bin/python3 /etc/shibboleth/filter-incommon-metadata.py > "$tmpfile"
outfile=/etc/shibboleth/filtered-incommon-metadata.xml
if cmp -s "$outfile" "$tmpfile"; then
    echo "new metadata is identical to old, nothing doing..."
    rm "$tmpfile"
    exit 0
fi
(set -x;  mv "$outfile" "$outfile.bak" && mv "$tmpfile" "$outfile")
if ! bash /etc/shibboleth/sanity-check.bash; then
    (set -x; mv "$outfile" "$outfile.broken" && mv "$outfile.bak" "$outfile")
    echo "sanity check failed! old metadata restored, and shibd not restarted."
    exit 1
fi
if systemctl status shibd >/dev/null; then
    if [ "$restart" == 1 ]; then
        (set -x; systemctl restart shibd)
    else
        echo "shibd should be restarted! (you can also call this script with --restart)"
    fi
else
    echo "shibd service not running, so not restarted."
fi
