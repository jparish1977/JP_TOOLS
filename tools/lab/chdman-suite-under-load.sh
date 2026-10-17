#!/usr/bin/env bash
# tools/lab/chdman-suite-under-load.sh -- tests/test_chd_chdman.py, several
# copies at once, counted
#
#   [CONTROL=1] bash tools/lab/chdman-suite-under-load.sh [COPIES] [RUNS]    (from a checkout)
#
# ANSWERS: with the test's short chdman stall, does a chdman hang end as a
#   rerun inside the run (the suite still passes), and does a call that is
#   only slow on a loaded box get killed as stalled (the suite fails)? Starts
#   COPIES (default 8) loops at once, each running the file RUNS times
#   (default 25). Prints one line per failed run, with its last lines, then
#   the totals: runs, failed, reruns, and the slowest run's seconds. Exit 0
#   only when no run failed.
#
# HOW: a `chdman` first on PATH writes each call's arguments to a file per
#   run and execs the real one. chd.py reruns a stalled call at once with the
#   same arguments, so a call identical to the one before it is a rerun
#   (chd.py says so only on stderr, which the suite captures). CONTROL=1 makes that chdman
#   hang, writing nothing, on each run's first child CHD (--outputparent):
#   every run must then pass with at least one rerun.
#
# Why: the hang is 7 to 13 in 1600 children with eight at once
#   (chdman-delta-hang.sh), and a mutant's tests get as little as 5 s of wall
#   clock under mutmut, so the stall must be short; too short and a slow call
#   on a busy box fails the test instead.
#
# NOT TESTED AGAINST: a box loaded by something other than these copies.
printf '%s' chdman-suite > /proc/$$/comm 2>/dev/null || true
set -u
copies=${1:-8}
runs=${2:-25}
py=${PYTHON:-python3}
real=$(command -v chdman) || { echo "chdman-suite-under-load: no chdman on PATH" >&2; exit 2; }
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
mkdir "$d/bin"
cat > "$d/bin/chdman" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "\$CHDMAN_CALLS"
if [ "\${CONTROL:-}" = 1 ] && [[ " \$* " == *" --outputparent "* ]] && [ ! -e "\$CHDMAN_CALLS.hung" ]; then
    : > "\$CHDMAN_CALLS.hung"
    exec sleep 60
fi
exec "$real" "\$@"
EOF
chmod +x "$d/bin/chdman"
loop() {
    local c=$1 n start end
    for n in $(seq 1 "$runs"); do
        start=$(date +%s.%N)
        CHDMAN_CALLS="$d/$c-$n.calls" PATH="$d/bin:$PATH" "$py" tests/test_chd_chdman.py > "$d/$c-$n.log" 2>&1
        echo "$?" > "$d/$c-$n.rc"
        end=$(date +%s.%N)
        echo "$start $end" > "$d/$c-$n.time"
    done
}
for c in $(seq 1 "$copies"); do loop "$c" & done
wait
"$py" - "$d" "$copies" "$runs" <<'PY'
import sys
from pathlib import Path

d, copies, runs = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
failed = 0
slowest = 0.0
calls = {}
for c in range(1, copies + 1):
    for n in range(1, runs + 1):
        log = (d / f"{c}-{n}.log").read_text(errors="replace")
        rc = (d / f"{c}-{n}.rc").read_text().strip()
        start, end = map(float, (d / f"{c}-{n}.time").read_text().split())
        slowest = max(slowest, end - start)
        f = d / f"{c}-{n}.calls"
        lines = f.read_text().splitlines() if f.exists() else []
        calls[(c, n)] = (len(lines), sum(a == b for a, b in zip(lines, lines[1:])))
        if rc != "0":
            failed += 1
            tail = " | ".join(log.strip().splitlines()[-3:])
            print(f"copy {c} run {n}: rc={rc}, {end - start:.1f} s: {tail}")
fewest = min(k for k, _ in calls.values())
reruns = sum(r for _, r in calls.values())
rerun_runs = sum(r > 0 for _, r in calls.values())
total = copies * runs
print(f"chdman-suite-under-load: {total} runs, {failed} failed, {reruns} reruns in {rerun_runs} runs "
      f"({fewest} to {max(k for k, _ in calls.values())} calls a run), slowest {slowest:.1f} s")
sys.exit(1 if failed or fewest == 0 else 0)
PY
