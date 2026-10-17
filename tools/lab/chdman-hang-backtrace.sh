#!/usr/bin/env bash
# tools/lab/chdman-hang-backtrace.sh -- every thread's stack in a hung chdman
#
#   [CHDMAN=PATH] [DEBUGINFOD=on|off] [STOP=FILE] \
#       bash tools/lab/chdman-hang-backtrace.sh OUT [RUNS] [SECONDS]
#
# DEBUGINFOD=off for a chdman built with its own symbols
# (SYMBOLS=1 chdman-build.sh): nothing is downloaded.
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
# Symbols are switched on with -iex, before gdb loads the program: gdb asks
# whether to use debuginfod as it loads it, and with no terminal that answer
# is no, whatever a later -ex says. One warm-up run with a long limit then
# fetches every symbol file into gdb's cache, so no timed run spends its
# limit downloading (the first version of this script did: every run sat in
# "Downloading separate debug info for system-supplied DSO" until its 20 s
# interrupt, and none ever ran chdman). Each run is counted as finished
# ("exited normally"), hung (chdman interrupted while running), or not
# started, and the totals say which: a "no hang" with no finished runs
# tested nothing.
#
# Tested: the packaged chdman 0.264 on Ubuntu 24.04 with debuginfod on: the
# warm-up never finished in 600 s, every download "Timer expired" (chdman's,
# the vDSO's, SDL2's), so Ubuntu's server is no route from that box; build
# with SYMBOLS=1 instead. NOT tested: whether running under gdb changes how
# often the hang comes.
printf '%s' chdman-bt > /proc/$$/comm 2>/dev/null || true
set -u
out=$1
runs=${2:-400}
limit=${3:-20}
chdman=${CHDMAN:-chdman}
debuginfod=${DEBUGINFOD:-on}
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
# gdb runs chdman making the child; after `run`, every thread's stack.
gdb_child=(gdb -q -batch -nx -iex "set debuginfod enabled $debuginfod" -iex 'set pagination off'
    -iex 'set confirm off' -ex run -ex 'info threads' -ex 'thread apply all bt' -ex kill
    --args "$chdman" createraw --input "$d/b.img" --output "$d/b.chd" --outputparent "$d/a.chd")
warm_up() {
    # Every symbol file fetched once, so no timed run downloads.
    local s
    s=$(date +%s)
    timeout -k 30 600 "${gdb_child[@]}" > "$d/warm.out" 2>&1
    if ! command grep -q "exited normally" "$d/warm.out"; then
        echo "chdman-bt: the warm-up run did not finish in 600 s; its end:"
        tail -n 15 "$d/warm.out"
        exit 2
    fi
    echo "chdman-bt: warm-up finished in $(($(date +%s) - s)) s"
}

outcome() {
    # What gdb.out says the run did: finished, hung, or unstarted.
    if command grep -q "exited normally" "$d/gdb.out"; then
        echo finished
    elif command grep -q "Thread .* received signal SIGINT\|Program received signal SIGINT" "$d/gdb.out"; then
        echo hung
    else
        echo unstarted
    fi
}

warm_up
stop=${STOP:-}
finished=0
unstarted=0
for n in $(seq 1 "$runs"); do
    if [ -n "$stop" ] && [ -e "$stop" ]; then
        echo "chdman-bt: another copy caught the hang; stopped at run $n ($finished finished, $unstarted not started)"
        exit 1
    fi
    rm -f "$d/b.chd"
    # SIGINT after LIMIT stops chdman if it is still going.
    timeout -s INT -k 30 "$limit" "${gdb_child[@]}" > "$d/gdb.out" 2>&1
    case $(outcome) in
        finished) finished=$((finished + 1)); continue ;;
        unstarted) unstarted=$((unstarted + 1)); continue ;;
    esac
    {
        echo "# chdman-hang-backtrace: run $n of $runs, still running after ${limit}s"
        echo "# $("$chdman" --help 2>&1 | head -1)"
        echo "# child at $(stat -c %s "$d/b.chd" 2>/dev/null || echo absent) bytes; $(uname -srm); nproc $(nproc)"
        cat "$d/gdb.out"
    } > "$out"
    if [ -n "$stop" ]; then touch "$stop"; fi
    echo "chdman-bt: hung at run $n ($finished finished before it); every thread's stack in $out"
    exit 0
done
echo "chdman-bt: no hang in $runs runs: $finished finished, $unstarted not started"
exit 1
