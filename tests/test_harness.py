#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_harness.py
tests/_harness.py's run(): what every no-pytest test file reports through.

WHAT IS CHECKED
    A test that raises is one FAIL line under its own name, and the tests
    sorted after it still run and report their own checks. A file whose tests
    all pass exits 0 and says so; any failure exits 1.

    Importing the harness takes git's repository variables away: a suite run
    with GIT_DIR naming a real repo (as under `git bisect run` or a hook)
    makes, commits in and reconfigures its scratch repo, and the real one is
    unchanged; without the import (the control) it is made bare and given the
    suite's commit. Every suite imports the harness.

    CHECKOUT, what a whole-repo self-check reads: under mutmut, in its
    mutants/ copy, the checkout that copy was made from; otherwise ROOT.

    python3 tests/test_harness.py
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import CHECKOUT, ROOT, check, check_true, checkout_of, run

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


def test_a_long_mismatch_says_what_differs() -> None:
    """Two copies of a long output on one line hide the line that changed: a
    long mismatch is a diff of the lines that differ, short ones stay whole."""
    from _harness import mismatch
    check("short values: both, whole", mismatch(2, 1), "expected 2, got 1")
    help_was = [f"  --flag{n}    what flag {n} does, said at length so the help is long" for n in range(8)]
    help_now = [*help_was[:3], "  --flag3    what flag 3 does, said differently now", *help_was[4:]]
    check("long lists of lines: only the changed line and one line each side",
          mismatch(help_was, help_now),
          "differs (- expected, + got):\n@@ -3,3 +3,3 @@\n"
          f" {help_was[2]!r}\n-{help_was[3]!r}\n+{help_now[3]!r}\n {help_was[4]!r}")
    text_was, text_now = "\n".join(help_was) + "\n", "\n".join(help_was)
    check("long text: a trailing newline lost is shown, each line as its repr",
          mismatch(text_was, text_now),
          "differs (- expected, + got):\n@@ -7,2 +7,2 @@\n"
          f" {help_was[6] + chr(10)!r}\n-{help_was[7] + chr(10)!r}\n+{help_was[7]!r}")
    check("a long tuple: each differing item by index, the same way",
          mismatch((0, help_was), (1, help_now)),
          "[0] expected 0, got 1\n[1] " + mismatch(help_was, help_now))
    check("long text against its own lines as a list: both whole, since no line differs",
          mismatch(text_was, text_was.splitlines(keepends=True)).startswith("expected "), True)
    check("long values of other kinds: both whole",
          mismatch({"k": "v" * 300}, {"k": "w" * 300}), f"expected {{'k': {'v' * 300!r}}}, got {{'k': {'w' * 300!r}}}")


def test_all_passing_exits_zero() -> None:
    r = run_probe(PASSING)
    check("control, every check passes: exit 0 and says so",
          (r.returncode, r.stdout.strip()), (0, "all probe tests passed"))


# A suite that makes a scratch repo, commits in it and points a remote at it:
# what many suites here do. {harness} is the import line, or nothing.
SCRATCH_GIT = """\
import subprocess, sys, tempfile
{harness}
ident = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
with tempfile.TemporaryDirectory() as d:
    for args in (["init", "-q", "-b", "probe", d],
                 ["-C", d, *ident, "commit", "-q", "--allow-empty", "-m", "seed"],
                 ["-C", d, "config", "core.bare", "true"]):
        subprocess.run(["git", *args], check=False, capture_output=True)
"""


def _repo_state(repo: Path) -> list[str]:
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                              check=False, env=dict(os.environ)).stdout
    return [git("config", "core.bare"), git("for-each-ref"), git("rev-parse", "HEAD"),
            git("rev-list", "--all", "--count")]


def _scratch_git_under(real: Path, harness: str) -> list[str]:
    """Run SCRATCH_GIT as `git bisect run` or a hook would, with git's
    repository variables naming REAL; return REAL's state after."""
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(SCRATCH_GIT.format(harness=harness), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "tests"), GIT_DIR=str(real / ".git"),
                   GIT_WORK_TREE=str(real), GIT_INDEX_FILE=str(real / ".git" / "index"))
        subprocess.run([sys.executable, str(probe)], check=False, env=env, timeout=60)
    return _repo_state(real)


def test_a_suite_cannot_reach_the_repo_git_points_at() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        real = Path(td) / "real"
        subprocess.run(["git", "init", "-q", "-b", "main", str(real)], check=True)
        subprocess.run(["git", "-C", str(real), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--allow-empty", "-m", "real"], check=True)
        before = _repo_state(real)
        check("importing the harness first: the real repo is untouched",
              _scratch_git_under(real, "import _harness"), before)
        after = _scratch_git_under(real, "")
        check_true("CONTROL, without it: the suite's git acted on the real repo (made bare, given its commit)",
                   after[0].strip() == "true" and after[3] != before[3])


def test_a_self_check_reads_the_checkout_not_mutmuts_copy() -> None:
    base = Path("/repo")
    mutants = base / "mutants"
    check("under mutmut, in its copy: the checkout it was made from",
          checkout_of(mutants, {"MUTANT_UNDER_TEST": "x.y__mutmut_1"}), base)
    check("mutmut's clean run sets it empty: still the checkout",
          checkout_of(mutants, {"MUTANT_UNDER_TEST": ""}), base)
    check("CONTROL, a dir named mutants with mutmut not running: itself",
          checkout_of(mutants, {}), mutants)
    check("CONTROL, mutmut running elsewhere: itself", checkout_of(base, {"MUTANT_UNDER_TEST": "stats"}), base)
    check("CHECKOUT is this run's own answer", CHECKOUT, checkout_of(ROOT, os.environ))


def test_every_suite_imports_the_harness() -> None:
    missing = [p.name for p in sorted((ROOT / "tests").glob("test_*.py"))
               if not any(isinstance(n, ast.ImportFrom) and n.module == "_harness"
                          for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))))]
    check("every suite imports _harness, so none runs git with the repository variables", missing, [])


if __name__ == "__main__":
    sys.exit(run(globals(), "harness"))
