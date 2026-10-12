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

    python3 tests/test_harness.py
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, run

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


def test_every_suite_imports_the_harness() -> None:
    missing = [p.name for p in sorted((ROOT / "tests").glob("test_*.py"))
               if not any(isinstance(n, ast.ImportFrom) and n.module == "_harness"
                          for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))))]
    check("every suite imports _harness, so none runs git with the repository variables", missing, [])


if __name__ == "__main__":
    sys.exit(run(globals(), "harness"))
