#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_procname.py
platformtools.procname.name_process: names the process on Linux, and can
never stop a script anywhere else.

WHY
    Sixteen scripts each carried their own copy of the naming call,
    ctypes.CDLL(None).prctl(...), inside except (OSError, AttributeError).
    On Windows CDLL(None) raises TypeError, so every one of them died on its
    first line there; fix-dashes.py runs in every repo's pre-commit hook, so
    every commit in a hooked repo on a Windows box read BROKEN.

    python3 tests/test_procname.py
"""

import ctypes
import subprocess
import sys
from unittest.mock import patch

from _harness import ROOT, check, check_true, run
from platformtools import procname


class _Exploding:
    """Stands in for ctypes.CDLL: raises ERROR when called, and counts calls."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def __call__(self, *_: object) -> None:
        self.calls += 1
        raise self.error


def _with(platform: str, cdll: _Exploding) -> tuple[bool, int]:
    """(whether name_process raised, how often it called CDLL) on PLATFORM."""
    with patch.object(sys, "platform", platform), patch.object(ctypes, "CDLL", cdll):
        try:
            procname.name_process("jp-test")
            raised = False
        except Exception:  # noqa: BLE001  # reason: the property under test is that nothing escapes
            raised = True
    return raised, cdll.calls


def test_off_linux_libc_is_never_touched() -> None:
    for platform in ("win32", "darwin"):
        raised, calls = _with(platform, _Exploding(RuntimeError("must not be called")))
        check(f"{platform}: never raises, never calls CDLL", (raised, calls), (False, 0))


def test_a_libc_that_will_not_load_is_never_fatal() -> None:
    """TypeError is what CDLL(None) raises on Windows (measured there)."""
    for error in (TypeError("LoadLibrary() argument 1 must be str, not None"),
                  OSError("no libc"), AttributeError("no prctl")):
        raised, calls = _with("linux", _Exploding(error))
        check(f"linux, CDLL raising {type(error).__name__}: never raises", (raised, calls), (False, 1))


def test_names_the_process_on_linux() -> None:
    if not sys.platform.startswith("linux"):
        print("SKIP: naming is Linux only, and this is not Linux")
        return
    program = ("import sys; sys.path.insert(0, sys.argv[1]);"
               "from platformtools.procname import name_process;"
               "name_process('jp-procname-test-long');"
               "print(open('/proc/self/comm').read().strip())")
    r = subprocess.run([sys.executable, "-c", program, str(ROOT)],
                       capture_output=True, text=True, check=False)
    check("the kernel's name, cut to 15 characters", (r.returncode, r.stdout.strip()), (0, "jp-procname-tes"))


def test_no_script_carries_its_own_copy() -> None:
    """One helper, so the next platform fix lands once."""
    r = subprocess.run(["git", "-C", str(ROOT), "grep", "-l", "-F", "CDLL(None)", "--", "*.py"],
                       capture_output=True, text=True, check=False)
    found = sorted(set(r.stdout.split()) - {"platformtools/procname.py", "tests/test_procname.py"})
    check_true("control: git grep ran", r.returncode in (0, 1))
    check("no other tracked .py calls CDLL(None)", found, [])


if __name__ == "__main__":
    sys.exit(run(globals(), "procname"))
