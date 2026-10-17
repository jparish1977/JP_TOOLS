"""Name this process, as the fleet asks of every script, without ever failing.

The name is the kernel's `comm`, what btop and `pgrep -x` see. It is set
with prctl(PR_SET_NAME), which only Linux has, and is a courtesy: a script
that cannot be named runs unnamed, never stops.

Every script shares this one helper. Each used to carry its own copy, and
every copy caught (OSError, AttributeError); on Windows ctypes.CDLL(None)
raises TypeError, so every one of them died on its first line there.
"""

import ctypes
import sys

PR_SET_NAME = 15  # prctl option: set the calling thread's name
COMM_MAX = 15     # the kernel keeps this many bytes of a name


def name_process(name: str) -> None:
    """Set this process's name to NAME (cut to COMM_MAX bytes), on Linux only.
    A Linux that cannot (no libc, no prctl) runs on unnamed, and says so."""
    if not sys.platform.startswith("linux"):
        return
    try:
        ctypes.CDLL(None).prctl(PR_SET_NAME, name.encode()[:COMM_MAX], 0, 0, 0)
    except (OSError, AttributeError, TypeError, ValueError) as e:  # reason: unnamed is never fatal; it is said, so a missing name has a cause on record
        sys.stderr.write(f"procname: could not name this process {name!r}: {e}\n")
