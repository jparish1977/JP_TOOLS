#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_harness.py
tests/_harness.py's run(): what every no-pytest test file reports through.

WHAT IS CHECKED
    A test that raises is one FAIL line under its own name, and the tests
    sorted after it still run and report their own checks. A file whose tests
    all pass exits 0 and says so; any failure exits 1.

    python3 tests/test_harness.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, run

PROBE = """\
import sys
from _harness import check, run

def test_a_crashes():
    raise RuntimeError("boom")

def test_b_checks():
    check("b ran", 1, 2)

sys.exit(run(globals(), "probe"))
"""

PASSING = """\
import sys
from _harness import check, run

def test_a():
    check("a", 1, 1)

sys.exit(run(globals(), "probe"))
"""


def run_probe(source: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(source, encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "tests"))
        return subprocess.run([sys.executable, str(probe)], capture_output=True, text=True,
                              check=False, env=env, timeout=60)


def test_a_crash_is_one_failure_and_the_run_goes_on() -> None:
    r = run_probe(PROBE)
    fails = [line for line in r.stdout.splitlines() if line.startswith("FAIL ")]
    check("a crashing test: exit 1, its own FAIL line, and the next test's check still reported",
          (r.returncode, fails),
          (1, ["FAIL test_a_crashes raised RuntimeError: boom", "FAIL b ran: expected 2, got 1"]))
    check("a crashing test: its traceback is on stderr", "RuntimeError: boom" in r.stderr, True)


def test_all_passing_exits_zero() -> None:
    r = run_probe(PASSING)
    check("control, every check passes: exit 0 and says so",
          (r.returncode, r.stdout.strip()), (0, "all probe tests passed"))


if __name__ == "__main__":
    sys.exit(run(globals(), "harness"))
