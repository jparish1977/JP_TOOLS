"""How many cores a run may use, and each of its children's share of them.

A container's --cpus is a cgroup CPU quota, not an affinity: inside one the
affinity still shows every core of the host, and a run sized from it starts
that many processes on a few cores' worth of time. mutmut-gate.py sizes its
children from cpu_cores() and tells each child its share in PYTHON_CPU_COUNT,
CPython's own variable for this (3.13's os.cpu_count() honours it by itself;
on 3.12 a suite that sizes a pool reads it first).
"""

import os
from pathlib import Path

# cgroup v2's CPU limit: "QUOTA PERIOD" in microseconds, or "max PERIOD" for none.
# cgroup v1's quota (cpu.cfs_quota_us) is not read: on a v1 host the affinity
# alone stands. The fleet's runners are v2.
CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
CPU_COUNT = "PYTHON_CPU_COUNT"


def cpu_cores(cpu_max: Path = CPU_MAX) -> int:
    """The cores this process may use: its affinity (os.cpu_count where there
    is none, as on Windows), held to its cgroup's CPU quota when one is set,
    rounded up so half a core counts as one."""
    try:
        cores = len(os.sched_getaffinity(0))
    except AttributeError:  # reason: no affinity on this platform; cpu_count is all it has
        cores = os.cpu_count() or 1
    try:
        # Bytes, as the kernel writes it: int() reads ASCII digits from bytes,
        # and nothing else is a quota.
        quota, period = cpu_max.read_bytes().split()
        return max(1, min(cores, -(-int(quota) // int(period))))
    except (OSError, ValueError, ZeroDivisionError):  # reason: no quota file, "max" (no quota) or one this cannot read: the affinity stands
        return cores


def child_cpu_count(env: dict[str, str], children: int, cores: int) -> dict[str, str]:
    """ENV with PYTHON_CPU_COUNT set to each of CHILDREN children's share of
    CORES, at least 1, so CHILDREN children do not each start a pool the
    size of the box. A count already set is the repo's own choice and is
    kept, but only a positive ASCII decimal: CPython 3.13 counts every core
    again on "default" and refuses to START on "0", "-2" or "2.5" (a fatal
    config_init_cpu_count error, exit 1; measured in python:3.13-slim), so
    anything else is replaced by the share. ENV itself is not changed."""
    preset = env.get(CPU_COUNT)
    if preset and preset.isascii() and preset.isdecimal() and int(preset) > 0:
        return env
    return {**env, CPU_COUNT: str(max(1, cores // max(1, children)))}
