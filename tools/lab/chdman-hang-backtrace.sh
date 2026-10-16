#!/usr/bin/env bash
# tools/lab/chdman-hang-backtrace.sh -- every thread's stack in a hung chdman
#
#   [CHDMAN=PATH] bash tools/lab/chdman-hang-backtrace.sh OUT [RUNS] [SECONDS]
#
# The evidence a report of the child-CHD hang to MAME needs, and where a fix
# would start: chdman-delta-hang.sh counts the hang, this says where it is.
# Makes the same images, then up to RUNS times (default 400) makes the child
# CHD against its parent under gdb. A run still going after SECONDS (default
# 20) is interrupted, and gdb writes every thread's backtrace to OUT; the
# script stops there (exit 0). No hang in RUNS: exit 1, OUT not written.
# Run several at once (the hang wants a loaded box), each with its own OUT
# and one STOP file named in the environment: the first to catch a hang
# creates it, and the others stop at their next run.
#
# gdb starts chdman itself rather than attaching, so it works where ptrace
# may only reach a process's own children (kernel.yama.ptrace_scope=1).
# Symbols: the packaged chdman's come from Ubuntu's debuginfod; a source
# build (chdman-build.sh) carries none unless built with SYMBOLS=1.
#
# Tested: not yet run when written. NOT tested: whether running under gdb
# changes how often the hang comes.
printf '%s' chdman-bt > /proc/$$/comm 2>/dev/null || true
set -u
out=$1
runs=${2:-400}
limit=${3:-20}
chdman=${CHDMAN:-chdman}
export DEBUGINFOD_URLS=${DEBUGINFOD_URLS:-https://debuginfod.ubuntu.com}
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
"$chdman" createraw --input "$d/a.img" --output "$d/a.chd" --hunksize 2048 --unitsize 512 > /dev/null 2>&1 || {
    echo "chdman-bt: the parent CHD could not be made"; exit 2; }
stop=${STOP:-}
for n in $(seq 1 "$runs"); do
    if [ -n "$stop" ] && [ -e "$stop" ]; then
        echo "chdman-bt: another copy caught the hang; stopped at run $n"
        exit 1
    fi
    rm -f "$d/b.chd"
    # gdb runs chdman; SIGINT after LIMIT stops it if it is still going, and
    # the commands after `run` then print every thread's stack.
    timeout -s INT -k 30 "$limit" gdb -q -batch -nx \
        -ex 'set debuginfod enabled on' -ex 'set pagination off' -ex 'set confirm off' \
        -ex run \
        -ex 'info threads' -ex 'thread apply all bt' -ex kill \
        --args "$chdman" createraw --input "$d/b.img" --output "$d/b.chd" \
        --outputparent "$d/a.chd" > "$d/gdb.out" 2>&1
    if command grep -q "Thread .* received signal SIGINT\|Program received signal SIGINT" "$d/gdb.out"; then
        {
            echo "# chdman-hang-backtrace: run $n of $runs, still running after ${limit}s"
            echo "# $("$chdman" --help 2>&1 | head -1)"
            echo "# child at $(stat -c %s "$d/b.chd" 2>/dev/null || echo absent) bytes; $(uname -srm); nproc $(nproc)"
            cat "$d/gdb.out"
        } > "$out"
        if [ -n "$stop" ]; then touch "$stop"; fi
        echo "chdman-bt: hung at run $n; every thread's stack in $out"
        exit 0
    fi
done
echo "chdman-bt: no hang in $runs runs"
exit 1
