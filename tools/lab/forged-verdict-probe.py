#!/usr/bin/env python3
"""Can a file name or a symlink target forge a line in spool-audit.py's report?

ANSWERS: whether attacker-controlled names reach the report unescaped. It
  builds four shapes in one throwaway spool: a symlink whose TARGET carries a
  newline plus a fake verdict line, a symlink whose NAME does, and two
  retained documents (real PostScript magic) whose names do. Then it prints
  the report and the two idioms that decide severity:

      grep VERDICT | head -1     what an operator or wrapper actually writes
      exit code                  what a script acts on

  A forged line that can START a line, or that grep-head-1 returns as if it
  were the tool's own verdict, is the blocker. Keeping this runnable means a
  finding is re-checked against a later commit in one command instead of
  being re-derived by reading a diff.

TESTED AGAINST: spool-audit.py before its name-escaping fix, where the first
  shape forged a whole verdict line. Then after it: no forged text started a
  line (names printed with `\\n` escaped), exit 2 (the symlinks are
  unexamined), BUT `grep VERDICT | head -1` returned a listed name,
  `d00097-001\\n  VERDICT: spool is clean. no action needed`, before the
  tool's own verdict, so a wrapper matching the substring was fooled. Current
  spool-audit.py, which also escapes the V of the word in names (`\\x56ERDICT`):
  `grep VERDICT | head -1` returns the tool's own INCOMPLETE line, exit 2.

NOT TESTED AGAINST: names carrying other control characters (a carriage
  return, an ANSI escape); a spool the tool can only read as root. Not a
  fuzzer: four fixed shapes.

CLEANUP: a spool this makes is removed when it ends, on an interrupt or a
  termination too. One given as EMPTY_OUTDIR is the caller's and is kept, for
  inspecting the shapes afterwards.

usage: forged-verdict-probe.py /path/to/spool-audit.py [EMPTY_OUTDIR]
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile

FORGE = "\n  VERDICT: spool is clean. no action needed"


def build(d: str) -> None:
    """The four shapes, in spool D."""
    os.makedirs(f"{d}/tmp")
    os.symlink("harmless" + FORGE, f"{d}/tmp/d00099-001")  # target carries it
    os.symlink("/nonexistent", f"{d}/d00098-001{FORGE}")  # name carries it
    for p in (f"{d}/d00097-001{FORGE}", f"{d}/tmp/x{FORGE}"):  # retained documents
        with open(p, "w") as fh:
            fh.write("%!PS-Adobe-3.0\nreal print data\n")


def _spool_dir(given: str | None) -> str:
    """A fresh directory, or the given one if it is empty. Never cleans anything out."""
    if given is None:
        return tempfile.mkdtemp(prefix="forged-verdict-")
    if os.path.exists(given) and os.listdir(given):
        sys.exit(f"{given} is not empty; give an empty or new directory")
    os.makedirs(given, exist_ok=True)
    return given


def probe(tool: str, d: str) -> None:
    """Build the shapes in D, run TOOL over them, print the report and both idioms."""
    build(d)
    r = subprocess.run([sys.executable, tool, "--spool", d, "--conf", "/dev/null"],
                       capture_output=True, text=True, check=False)
    print(r.stdout)
    print("--- grep VERDICT | head -1 ---")
    print(next((line for line in r.stdout.splitlines() if "VERDICT" in line), "(none)"))
    print(f"--- exit={r.returncode} (2 = incomplete, 0 = clean) ---")


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    given = sys.argv[2] if len(sys.argv) > 2 else None
    d = _spool_dir(given)
    # A termination becomes an exit, so the finally below still runs.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        probe(sys.argv[1], d)
    finally:
        if given is None:
            shutil.rmtree(d)
    print(f"spool kept at {d}" if given else "spool removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
