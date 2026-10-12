#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_gate.py
mutmut-gate.py holds each function to its baseline, and gets mutants/ ready.

    - The rule (Joe, 2026-10-10, strict; 2026-10-11, no tests): a baseline
      function fails when its survivors or its "no tests" rise; a function
      the baseline lacks fails on any of either. Kills turning into "no
      tests" (the test reaching it gone) fail; "timeout" counts as detected.
    - A status that is no verdict ("suspicious", "not checked") in a number
      the baseline does not hold is NOT_RUN (2), naming it; a finding wins.
    - Results are counted per function, the name before `__mutmut_`.
    - prepare makes mutants/ a worktree of HEAD whose index lists the tracked
      files while none is checked out, links node_modules/ and vendor/, follows
      a new HEAD, and rebuilds the link when the state came from elsewhere.
    - A run that cannot be read is NOT_RUN (2), never a pass.

    python3 tests/test_mutmut_gate.py
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

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
    check("a rise in either, and a new function's survivor or untested mutant, break the rule, "
          "each said whole", gate.violations(base, now), [
              "f.more: survived 1 -> 2",
              "f.new_untested: NEW, 2 mutant(s) no test reaches; a new function's tests reach all of its",
              "f.new_weak: NEW, 1 mutant(s) survived; a new function kills all of its",
              "f.untested: no tests 4 -> 6"])
    check("a baseline function with no survivor recorded holds 0, and says so",
          gate.violations({"f.clean": {"killed": 3}}, {"f.clean": {"killed": 2, "survived": 1}}),
          ["f.clean: survived 0 -> 1"])


# The baseline: x_f kills all three; x_g kills two, one survives.
BASE = {"m.x_f": {"killed": 3}, "m.x_g": {"killed": 2, "survived": 1}}


def test_reach_lost_or_never_given_fails() -> None:
    check("CONTROL: the baseline itself passes", gate.violations(BASE, BASE), [])
    lost = {**BASE, "m.x_f": {"no tests": 3}}
    check("f's kills becoming no tests (its test gone) fails",
          gate.violations(BASE, lost), ["m.x_f: no tests 0 -> 3"])
    fewer = {**BASE, "m.x_g": {"killed": 1, "no tests": 1, "survived": 1}}
    check("  a single kill becoming no tests fails too",
          gate.violations(BASE, fewer), ["m.x_g: no tests 0 -> 1"])
    untested = {**BASE, "m.x_h": {"no tests": 2}}
    check_true("a new function no test reaches fails",
               [f.split(":")[0] for f in gate.violations(BASE, untested)] == ["m.x_h"])
    timeout = {**BASE, "m.x_f": {"timeout": 3}}
    check("kills becoming timeouts pass: a looping mutant was caught",
          gate.violations(BASE, timeout) + gate.no_verdicts(BASE, timeout), [])


def test_a_status_that_is_no_verdict_is_not_run() -> None:
    for status in ("suspicious", "not checked", "segfault", "skipped"):
        now = {**BASE, "m.x_f": {"killed": 2, status: 1}}
        check(f"{status!r}: no finding", gate.violations(BASE, now), [])
        check("  but no verdict, named", gate.no_verdicts(BASE, now),
              [f"m.x_f: 1 mutant(s) {status!r}, which is no verdict (the baseline has 0)"])
    held = {"m.x_f": {"killed": 2, "suspicious": 1}}
    check("the baseline's own count of one is carried, not judged again",
          gate.no_verdicts(held, held), [])
    check("  in a new function it is judged",
          len(gate.no_verdicts({}, held)), 1)
    more = {"m.x_f": {"killed": 1, "suspicious": 2}}
    check("  a different count of it is judged, naming what the baseline holds",
          gate.no_verdicts(held, more),
          ["m.x_f: 2 mutant(s) 'suspicious', which is no verdict (the baseline has 1)"])


def _printed(baseline: dict[str, dict[str, int]], current: dict[str, dict[str, int]]) -> tuple[int, list[str]]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = gate._gate(baseline, current)
    return rc, out.getvalue().splitlines()


def test_what_the_gate_prints() -> None:
    check("a pass: exit 0, and says PASS", _printed(BASE, BASE), (0, ["mutmut-gate: PASS"]))
    worse = {**BASE, "m.x_g": {"killed": 1, "survived": 2}}
    check("a finding: exit 1, the line, then the count",
          _printed(BASE, worse), (1, ["FAIL m.x_g: survived 1 -> 2", "mutmut-gate: 1 rule(s) broken"]))
    odd = {**BASE, "m.x_f": {"killed": 2, "suspicious": 1}}
    check("no verdict: exit 2, the line, then what could not be judged",
          _printed(BASE, odd), (2, [
              "NOT RUN m.x_f: 1 mutant(s) 'suspicious', which is no verdict (the baseline has 0)",
              "mutmut-gate: could not judge 1 function(s)"]))


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


def test_run_traces_reach_afresh_and_keeps_results() -> None:
    """Kept stats naming no traced commit cannot say what moved: run drops
    them whole, so all reach is traced, and keeps the mutant results."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        (mutants / gate.STATS).write_text("{}", encoding="utf-8")
        (mutants / "a.py.meta").write_text("{}", encoding="utf-8")
        asked = _run_mutmut(root, full=False)
        check("mutmut was run as a module of this python, with the children asked for",
              [c["cmd"] for c in asked],
              [[sys.executable, "-m", "mutmut", "run", "--max-children", "2"]])
        check("  from the checkout", [c["cwd"] for c in asked], [root])
        check_true("  after the kept test reach was dropped", not (mutants / gate.STATS).exists())
        check_true("  and with the kept mutant results left in place", (mutants / "a.py.meta").exists())
        check("a run with no stats kept runs just the same", len(_run_mutmut(root, full=False)), 1)


def _commit(root: Path, files: dict[str, str], message: str) -> str:
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(body, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=_ENV)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--no-verify", "-m", message],
                   check=True, env=_ENV)
    return _head(root)


def test_only_the_reach_a_change_can_move_is_traced_again() -> None:
    """The tests in a changed file, and the tests reaching a function in a
    changed source file, are taken out of the kept stats; the rest stay."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        traced = _commit(root, {"pkg/mod.py": "y = 1\n", "b.py": "z = 1\n",
                                "tests/test_c.py": "", "tests/test_d.py": ""}, "traced")
        stats = Path(td) / "stats.json"
        # t6 is reached but has no duration kept; t5 is a class's test; t7's
        # module (c) does not change and stays.
        reach = {"a.x_f": ["tests/test_d.py::t1"],
                 "pkg.mod.x_g": ["tests/test_d.py::t2", "tests/test_e.py::t6"],
                 "b.x_h": ["tests/test_d.py::t3"], "c.x_k": ["tests/test_d.py::t7"]}
        durations = dict.fromkeys(("tests/test_d.py::t1", "tests/test_d.py::t2", "tests/test_d.py::t3",
                                   "tests/test_c.py::t4", "tests/test_c.py::K::t5",
                                   "tests/test_d.py::t7"), 1.0)
        stats.write_text(json.dumps({"git_commit": traced, "tests_by_mangled_function_name": reach,
                                     "duration_by_test": durations}), encoding="utf-8")
        _commit(root, {"a.py": "x = 3\n", "pkg/mod.py": "y = 2\n", "tests/test_c.py": "# changed\n"},
                "the change")
        (root / "b.py").write_text("z = 2\n", encoding="utf-8")  # changed, not committed
        said = gate.forget_moved_reach(root, stats)
        doc = json.loads(stats.read_text(encoding="utf-8"))
        check("the tests reaching a changed module, and in a changed test file, go",
              sorted(doc["duration_by_test"]), ["tests/test_d.py::t7"])
        check("  and leave the reach they had", doc["tests_by_mangled_function_name"],
              {"a.x_f": [], "pkg.mod.x_g": [], "b.x_h": [], "c.x_k": ["tests/test_d.py::t7"]})
        check("  and it says how many: an uncommitted change counts too", said,
              "4 file(s) changed since the traced commit: 6 test(s) traced again")


def test_a_file_tests_share_has_all_reach_traced() -> None:
    check("which files the tests share",
          {p: gate._shared_by_tests(p) for p in (
              "tests/conftest.py", "conftest.py", "tests/_harness.py", "tests/fixtures/a.json",
              "tests/_lab_python_cases.py", "tests/test_x.py", "tests/mutants/spec.json",
              "jpcheck/lab_use.py", "README.md", "pyproject.toml", "pytest.ini", "setup.cfg",
              "tox.ini", "sub/pyproject.toml")},
          {"tests/conftest.py": True, "conftest.py": True, "tests/_harness.py": True,
           "tests/fixtures/a.json": True, "tests/_lab_python_cases.py": True,
           "tests/test_x.py": False, "tests/mutants/spec.json": False,
           "jpcheck/lab_use.py": False, "README.md": False, "pyproject.toml": True,
           "pytest.ini": True, "setup.cfg": True, "tox.ini": True, "sub/pyproject.toml": False})
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        stats = Path(td) / "stats.json"
        stats.write_text(json.dumps({"git_commit": _head(root), "duration_by_test": {"tests/t.py::t": 1.0}}),
                         encoding="utf-8")
        _commit(root, {"tests/_harness.py": "# changed\n"}, "a helper")
        check("a helper changed: all reach traced, naming it", gate.forget_moved_reach(root, stats),
              "tests/_harness.py changed, which tests share: all test reach traced")
        check_true("  and the kept reach is gone", not stats.exists())


def test_kept_reach_missing_its_tables_is_read_as_empty() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        stats = Path(td) / "stats.json"
        stats.write_text(json.dumps({"git_commit": _head(root)}), encoding="utf-8")
        check("no reach or duration tables: nothing to take out",
              gate.forget_moved_reach(root, stats),
              "0 file(s) changed since the traced commit: 0 test(s) traced again")
        check("  and the file keeps them, empty", json.loads(stats.read_text(encoding="utf-8")),
              {"git_commit": _head(root), "tests_by_mangled_function_name": {}, "duration_by_test": {}})


def test_run_keeps_the_reach_nothing_moved() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        kept = {"git_commit": _head(root), "tests_by_mangled_function_name": {"a.x_f": ["tests/t.py::t"]},
                "duration_by_test": {"tests/t.py::t": 1.0}}
        (mutants / gate.STATS).write_text(json.dumps(kept), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _run_mutmut(root, full=False)
        check("run keeps reach traced at its own commit, whole",
              json.loads((mutants / gate.STATS).read_text(encoding="utf-8")), kept)
        check_true("  and says so", "0 test(s) traced again" in out.getvalue())


def test_kept_reach_that_cannot_be_read_or_placed_is_traced_whole() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        stats = Path(td) / "stats.json"
        check("none kept", gate.forget_moved_reach(root, stats), "no test reach kept: all of it traced")
        stats.write_text("{not json", encoding="utf-8")
        check("unreadable: dropped", gate.forget_moved_reach(root, stats),
              "kept test reach unreadable: all of it traced")
        check_true("  and gone", not stats.exists())
        stats.write_text(json.dumps({"git_commit": "0" * 40}), encoding="utf-8")
        said = gate.forget_moved_reach(root, stats)
        check_true("a commit this clone does not have: all traced, saying which",
                   said.startswith("cannot diff against the traced commit " + "0" * 40))
        check_true("  and gone", not stats.exists())


def test_a_module_name_is_its_path() -> None:
    check("module names", [gate._module(p) for p in
                           ("jpcheck/lab_use.py", "jpcheck/__init__.py", "mutmut-gate.py", "README.md")],
          ["jpcheck.lab_use", "jpcheck", "mutmut-gate", None])


def _run_mutmut(root: Path, full: bool, rc: int = 0,
                env: dict[str, str] | None = None) -> list[dict[str, object]]:
    """Each mutmut invocation gate.run makes, mutmut itself stood in for and
    exiting RC; git runs for real. ENV replaces os.environ for the run."""
    asked: list[dict[str, object]] = []

    def fake(cmd: list[str], **kw: object) -> int:
        asked.append({"cmd": cmd, **kw})
        return rc

    with mock.patch.object(gate.subprocess, "call", side_effect=fake), \
            mock.patch.dict(gate.os.environ, env or {}, clear=env is not None):
        gate.run(root, full, 2)
    return asked


def test_run_puts_its_python_first_on_path() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    here = str(Path(sys.executable).parent)
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        git = str(Path(shutil.which("git") or "git").parent)
        env = {"PATH": git, "HOME": td, "KEEP": "1"}
        [call] = _run_mutmut(root, full=False, env=env)
        seen = call["env"]
        assert isinstance(seen, dict)
        check("PATH is mutmut's python's directory, then the PATH it had",
              seen["PATH"], os.pathsep.join([here, git]))
        check("  and the rest of the environment is kept", seen.get("KEEP"), "1")
        [bare] = _run_mutmut(root, full=False, env={"HOME": td})
        seen = bare["env"]
        assert isinstance(seen, dict)
        check("no PATH at all: its python's directory and an empty rest",
              seen["PATH"], here + os.pathsep)


def test_run_full_starts_from_nothing() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        (mutants / "a.py.meta").write_text("{}", encoding="utf-8")
        stale = Path(td) / "stale-wt"
        subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "--detach", str(stale)],
                       check=True, env=_ENV)
        shutil.rmtree(stale)
        check_true("CONTROL: git lists a worktree whose directory is gone",
                   str(stale) in _worktrees(root))
        _run_mutmut(root, full=True)
        check_true("--full drops every kept result", not (mutants / "a.py.meta").exists())
        check_true("  and prunes the worktree that was gone", str(stale) not in _worktrees(root))
        check("  and mutants/ is ready again", _ls(mutants), "a.py\n")


def test_run_says_how_mutmut_failed() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        try:
            _run_mutmut(root, full=False, rc=3)
            said = ""
        except gate.GateError as e:
            said = str(e)
    check("mutmut exiting 3 is a GateError naming the code", said, "mutmut run exited 3")


def _worktrees(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"],
                          capture_output=True, text=True, check=True).stdout


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
        odd = tmp / "odd.txt"
        odd.write_text(RESULTS + "    pkg.mod.x_alpha__mutmut_4: suspicious\n", encoding="utf-8")
        check("a mutant with no verdict: NOT_RUN (2)",
              gate.main(["gate", str(base), "--results", str(odd)]), 2)
        both = tmp / "both.txt"
        both.write_text(RESULTS + "    pkg.mod.x_alpha__mutmut_4: suspicious\n"
                        "    pkg.mod.x_alpha__mutmut_5: survived\n", encoding="utf-8")
        check("  and with a finding too, the finding wins: FINDINGS (1)",
              gate.main(["gate", str(base), "--results", str(both)]), 1)
        check("a missing baseline: NOT_RUN (2)",
              gate.main(["gate", str(tmp / "nope.json"), "--results", str(res)]), 2)
        check("missing results: NOT_RUN (2)",
              gate.main(["gate", str(base), "--results", str(tmp / "nope.txt")]), 2)
        check("a usage error: NOT_RUN (2)", gate.main(["gate"]), 2)


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut gate"))
