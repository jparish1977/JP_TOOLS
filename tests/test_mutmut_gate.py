#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_gate.py
mutmut-gate.py holds each function to its baseline, and gets mutants/ ready.

    - The rule (Joe, 2026-10-11, strict): a baseline function fails when its
      survivors rise; a function the baseline lacks fails on any survivor;
      "no tests" mutants never count.
    - Results are counted per function, the name before `__mutmut_`.
    - prepare makes mutants/ a worktree of HEAD whose index lists the tracked
      files while none is checked out, links node_modules/ and vendor/, follows
      a new HEAD, and rebuilds the link when the state came from elsewhere.
    - A run that cannot be read is NOT_RUN (2), never a pass.

    python3 tests/test_mutmut_gate.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, load_script, run

gate = load_script("mutmut-gate.py")

RESULTS = """\
    pkg.mod.x_alpha__mutmut_1: killed
    pkg.mod.x_alpha__mutmut_2: survived
    pkg.mod.x_alpha__mutmut_3: survived
    pkg.mod.x_beta__mutmut_1: no tests
    script.x_main__mutmut_1: killed
"""


def test_counts_are_per_function() -> None:
    check("each function's statuses counted, the name before __mutmut_",
          gate.by_function(RESULTS),
          {"pkg.mod.x_alpha": {"killed": 1, "survived": 2},
           "pkg.mod.x_beta": {"no tests": 1},
           "script.x_main": {"killed": 1}})


def test_results_with_no_mutants_are_not_a_pass() -> None:
    try:
        gate.by_function("mutmut said nothing useful\n")
        raised = False
    except gate.GateError:
        raised = True
    check_true("results naming no mutant are an error, not an empty pass", raised)


def test_the_strict_rule() -> None:
    base = {"f.same": {"survived": 2}, "f.fewer": {"survived": 3}, "f.more": {"survived": 1},
            "f.gone": {"survived": 5}, "f.untested": {"no tests": 4}}
    now = {"f.same": {"survived": 2, "killed": 9}, "f.fewer": {"survived": 1},
           "f.more": {"survived": 2}, "f.new_clean": {"killed": 4},
           "f.new_weak": {"killed": 3, "survived": 1}, "f.untested": {"no tests": 6},
           "f.new_untested": {"no tests": 2}}
    found = gate.violations(base, now)
    check("only a rise and a new function's survivor break the rule",
          [line.split(":")[0] for line in found], ["f.more", "f.new_weak"])
    check_true("  the rise names both numbers", any("survived 1 -> 2" in f for f in found))
    check_true("  the new function is called NEW", any("NEW" in f for f in found))


_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")


def _repo(tmp: Path) -> Path:
    root = tmp / "repo"
    root.mkdir()
    env = _ENV
    for args in (["init", "-q"], ["config", "commit.gpgsign", "false"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, env=env)
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "vendor").mkdir()
    subprocess.run(["git", "-C", str(root), "add", "a.py"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--no-verify", "-m", "one"],
                   check=True, env=env)
    return root


def _ls(path: Path) -> str:
    return subprocess.run(["git", "-C", str(path), "ls-files"], capture_output=True,
                          text=True, check=True).stdout


def _head(path: Path) -> str:
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


def test_prepare() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        check("git in mutants/ lists the tracked files", _ls(mutants), "a.py\n")
        check_true("  with none checked out (mutmut writes them)", not (mutants / "a.py").exists())
        check_true("  node_modules/ and vendor/ linked, not copied",
                   all((mutants / n).is_symlink() for n in ("node_modules", "vendor")))
        check("  at the checkout's HEAD", _head(mutants), _head(root))

        first = _head(root)
        (root / "a.py").write_text("x = 2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "commit", "-q", "--no-verify", "-am", "two"],
                       check=True, env=_ENV)
        check_true("CONTROL: the checkout's HEAD moved", _head(root) != first)
        (mutants / "a.py").write_text("x = 'mutated'\n", encoding="utf-8")
        gate.prepare(root)
        check("a new HEAD is followed", _head(mutants), _head(root))
        check("  and a file mutmut wrote is left as it is",
              (mutants / "a.py").read_text(encoding="utf-8"), "x = 'mutated'\n")

        # A state restored from another checkout: its .git link points nowhere.
        (mutants / ".git").write_text("gitdir: /nonexistent/worktrees/mutants\n", encoding="utf-8")
        gate.prepare(root)
        check("a restored state's broken link is rebuilt", _ls(mutants), "a.py\n")
        check("  and mutmut's files survive the rebuild",
              (mutants / "a.py").read_text(encoding="utf-8"), "x = 'mutated'\n")
        check("  leaving no scratch worktree behind",
              sorted(p.name for p in root.iterdir() if p.name.startswith(".mutants-wt-")), [])


def test_the_command_line() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        res = tmp / "results.txt"
        res.write_text(RESULTS, encoding="utf-8")
        base = tmp / "base.json"
        check("record exits OK", gate.main(["record", str(base), "--results", str(res)]), 0)
        check("  and writes the per-function counts",
              json.loads(base.read_text(encoding="utf-8")), gate.by_function(RESULTS))
        check("gate against its own record: OK",
              gate.main(["gate", str(base), "--results", str(res)]), 0)
        worse = tmp / "worse.txt"
        worse.write_text(RESULTS + "    pkg.mod.x_alpha__mutmut_4: survived\n", encoding="utf-8")
        check("one more survivor: FINDINGS (1)",
              gate.main(["gate", str(base), "--results", str(worse)]), 1)
        check("a missing baseline: NOT_RUN (2)",
              gate.main(["gate", str(tmp / "nope.json"), "--results", str(res)]), 2)
        check("missing results: NOT_RUN (2)",
              gate.main(["gate", str(base), "--results", str(tmp / "nope.txt")]), 2)
        check("a usage error: NOT_RUN (2)", gate.main(["gate"]), 2)


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut gate"))
