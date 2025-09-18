set -euo pipefail
trap 's=$?; echo "$0: Error on line "$LINENO": $BASH_COMMAND"; exit $s' ERR
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
mv "$outfile" "$outfile.bak"
mv "$tmpfile" "$outfile"
sanity_check_out="$(/sbin/shibd -t 2>&1)"
if grep CRIT <<<"$sanity_check_out"; then
    echo "sanity check failed!"
    echo "$sanity_check_out"
    mv "$outfile" "$outfile.broken"
    mv "$outfile.bak" "$outfile"
    echo "old metadata restored."
    echo "failure-causing metadata moved to '$PWD/filtered-incommon-metadata.xml.broken'."
    exit 1
fi
if systemctl status shibd >/dev/null; then
    systemctl restart shibd
else
    echo "shibd service not running, so not restarted."
fi
