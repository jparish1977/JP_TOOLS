#!/usr/bin/env bash
# tools/lab/chdman-delta-hang.sh -- how often chdman hangs making a child CHD
#
#   bash tools/lab/chdman-delta-hang.sh [RUNS] [SECONDS]
#
# Makes the images tests/test_chd_chdman.py's delta test makes (64 sectors of
# 2048 bytes, 7 bytes changed at 5000), then RUNS times (default 50): the
# parent with createraw as chd.py runs it, then the child against it with
# --outputparent, each under `timeout SECONDS` (default 20). Prints one line
# per run that timed out, with the child's size, then the totals.
#
# Why: on a 32-thread box chdman 0.264 once spun one thread at 100% while its
# 17 workers waited on a futex, writing nothing past the 124-byte header,
# for as long as it was left (killed at 8 minutes). This counts how often.
#
# Tested: 100 runs at 20 s on a quiet 32-thread box (chdman 0.264): 0 hung,
# 0 failed. Positive control: 3 runs at 0.01 s: 3 hung, each named.
# NOT tested: under load. The one hang seen came while a proof ran 8 suites
# at once beside it; this runs one chdman at a time on an idle box, so a 0
# here does not say the hang cannot happen.
printf '%s' chdman-hang > /proc/$$/comm 2>/dev/null || true
set -u
runs=${1:-50}
limit=${2:-20}
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
python3 - "$d" <<'EOF'
import sys
from pathlib import Path
d = Path(sys.argv[1])
base = bytes((i * 5 + i // 7) % 251 for i in range(64 * 2048))
changed = bytearray(base)
changed[5000:5007] = b"changed"
(d / "a.img").write_bytes(base)
(d / "b.img").write_bytes(changed)
EOF
hung=0
failed=0
for n in $(seq 1 "$runs"); do
    rm -f "$d/a.chd" "$d/b.chd"
    timeout "$limit" chdman createraw --input "$d/a.img" --output "$d/a.chd" \
        --hunksize 2048 --unitsize 512 > /dev/null 2>&1
    rc=$?
    if [ "$rc" -eq 0 ]; then
        timeout "$limit" chdman createraw --input "$d/b.img" --output "$d/b.chd" \
            --outputparent "$d/a.chd" > /dev/null 2>&1
        rc=$?
        what=child
    else
        what=parent
    fi
    if [ "$rc" -eq 124 ]; then
        hung=$((hung + 1))
        echo "run $n: $what hung past ${limit}s, child $(stat -c %s "$d/b.chd" 2>/dev/null || echo absent) bytes"
    elif [ "$rc" -ne 0 ]; then
        failed=$((failed + 1))
        echo "run $n: $what failed rc=$rc"
    fi
done
echo "chdman $(chdman --help 2>&1 | head -1 | cut -d' ' -f10): $runs runs, $hung hung, $failed failed, nproc $(nproc)"
