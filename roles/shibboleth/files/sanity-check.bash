set -euo pipefail
trap 's=$?; echo "$0: Error on line "$LINENO": $BASH_COMMAND"; exit $s' ERR

sanity_check_out="$(/sbin/shibd -t 2>&1)"
if grep CRIT <<<"$sanity_check_out"; then
    echo "sanity check failed!"
    echo "$sanity_check_out"
    exit 1
fi
