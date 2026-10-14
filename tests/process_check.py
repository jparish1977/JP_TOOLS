"""check_exit: a check on a process's exit code that, when it fails, says what
the process printed.

A check on an exit code alone fails with two numbers: a hook that refused a
commit says only "expected 0, got 1", and finding out why takes another run.
check_exit records the exit, what was wanted, any expected text the output
lacks, and the last lines of the process's own output (stdout, then stderr),
so the failure carries its reason.

    from process_check import check_exit, NONZERO
    r = subprocess.run([...], capture_output=True, text=True, check=False)
    check_exit("a clean commit goes through", r, 0)
    check_exit("a scratch path is refused, saying so", r, NONZERO, says=["relies on scratch"])

Not in _harness on purpose: a change there makes every test count as edited
for mutation testing, and only the suites that import this read it.
"""

import subprocess
from collections.abc import Iterable

from _harness import FAILURES

# Any exit but 0, for a check that something was refused without pinning how.
NONZERO = -1
# How much of the process's output a failed check carries.
TAIL = 20


def check_exit(label: str, proc: subprocess.CompletedProcess[str], want: int,
               says: Iterable[str] = (), lacks: Iterable[str] = ()) -> None:
    """Record a failure unless PROC exited WANT (NONZERO: anything but 0), its
    output holds every one of SAYS, and none of LACKS."""
    out = (proc.stdout or "") + (proc.stderr or "")
    missing = [s for s in says if s not in out]
    there = [s for s in lacks if s in out]
    exited = proc.returncode != 0 if want == NONZERO else proc.returncode == want
    if exited and not missing and not there:
        return
    wanted = "not 0" if want == NONZERO else str(want)
    why = f"exit {proc.returncode}, wanted {wanted}"
    if missing:
        why += f"; its output lacks {missing!r}"
    if there:
        why += f"; its output holds {there!r}"
    lines = out.splitlines()[-TAIL:]
    FAILURES.append(f"{label}: {why}; the last {len(lines)} line(s) it printed:\n"
                    + "\n".join(f"    {line}" for line in lines))
