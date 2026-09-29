#!/usr/bin/env python3
"""Does every tracked Python file still import?

ANSWERS: after moving code between files, which tracked .py files fail at
  import time: a name imported from where it no longer is, a module that is
  gone. Gating only the files a change touched misses the importers it did not
  touch. Each file's module level runs, in its own interpreter so one file
  cannot affect the next; its `if __name__ == "__main__"` block does not.

TESTED AGAINST: this repo's by-path-load branch: 129 files, 0 failed.
  Positive control: a two-file git repo whose bad.py imports a name good.py
  lacks printed `FAIL bad.py: ImportError: cannot import name 'NOPE'` and
  exit 1. (The same check as a scratch script caught fix.py importing a
  helper the check.py split had moved, while every suite passed.)

NOT TESTED AGAINST: import-time side effects. Every module level here is
  definitions and constants, but a file that acts on import would act. Runs
  with the checkout and tests/ on sys.path, as the suites do, so it does NOT
  show a script that fails when another program loads it by path; that is
  tests/test_load_by_path.py.

usage: tools/lab/import-every-file.py [CHECKOUT]   (default: the current one)
"""
import subprocess
import sys
from pathlib import Path

# Runs argv[2]'s module level with argv[1] and argv[1]/tests on sys.path.
RUN = ("import runpy, sys\n"
       "sys.path[:0] = [sys.argv[1], sys.argv[1] + '/tests']\n"
       "runpy.run_path(sys.argv[2], run_name='not_main')\n")


def _name_process(name: bytes) -> None:
    """Name the process (what top and btop show) instead of python3."""
    try:
        import ctypes
        ctypes.CDLL(None).prctl(15, name, 0, 0, 0)
    except (OSError, AttributeError):
        # reason: no libc or no prctl: the process runs unnamed, which is never fatal
        pass


def main(argv: list[str]) -> int:
    root = Path(argv[0] if argv else ".").resolve()
    files = subprocess.run(["git", "-C", str(root), "ls-files", "*.py"],
                           capture_output=True, text=True, check=True).stdout.split()
    bad = 0
    for f in files:
        r = subprocess.run([sys.executable, "-c", RUN, str(root), str(root / f)],
                           capture_output=True, text=True, check=False, cwd=root)
        if r.returncode:
            bad += 1
            last = (r.stderr.strip().splitlines() or ["(no stderr)"])[-1]
            print(f"FAIL {f}: {last}")
    print(f"{len(files)} files, {bad} failed to import")
    return 1 if bad else 0


if __name__ == "__main__":
    _name_process(b"import-every")
    sys.exit(main(sys.argv[1:]))
