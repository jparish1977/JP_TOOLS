#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_smells_history.py
The smells arm's history half, run in this process: what a smell reports,
and where the "before" it is compared with comes from.

WHAT THIS PINS DOWN
    _smell: the whole issue dict for each verdict (new, grown, unchanged,
    shrunk, no history), at and around the limit.
    _smells_context: the repo root and base for a file in a repo with
    history; None outside git and in a repo with no commits; a named base
    that does not resolve to one commit (a missing ref, a range) is refused,
    naming it.
    _base_text: the file at the base, None when the base has no such file.
    _base_measure: the base file's measure, by its path or the path it was
    renamed from; nothing for a file new at the base or one that will not
    parse there.
    _function_smells: the full list for each function, its before taken from
    the base, from the one other file it moved out of, or none.

Each git repo is a throwaway one; the code under test is imported, never run
as a subprocess, so a mutation run sees every line.

    python3 tests/test_smells_history.py
"""

import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from _harness import check, run
from jpcheck import smells
from jpcheck.measure import TOP_LEVEL, measure_python


def long_func(name: str, n: int) -> str:
    """A function exactly n lines long, of complexity 1."""
    return f"def {name}():\n" + "    x = 1\n" * (n - 1)


def complex_func(name: str, k: int) -> str:
    """A short function of McCabe complexity exactly k."""
    body = "".join(f"    if a == {i}:\n        return {i}\n" for i in range(k - 1))
    return f"def {name}(a):\n{body}    return -1\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=JP_TOOLS test",
                           "-c", "commit.gpgsign=false", *args], cwd=repo, capture_output=True,
                          text=True, check=True).stdout


def repo_with(files: dict[str, str]) -> Path:
    """A new repo with FILES committed; the caller removes its parent."""
    repo = Path(tempfile.mkdtemp()).resolve() / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    if files:
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
    return repo


def drop(repo: Path) -> None:
    shutil.rmtree(repo.parent, ignore_errors=True)


def attempt(fn: Callable[[], Any]) -> Any:
    """FN's result, or what it raised: a crash is a wrong answer under the
    check's own label, not a failure under some other name."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 -- reason: the exception is the answer being checked
        return f"raised {type(e).__name__}: {e}"


def issue(f: Path, line: int, rule: str, severity: str, message: str) -> dict[str, Any]:
    return {"file": str(f), "line": line, "col": 0, "severity": severity, "rule": rule,
            "message": message, "fixable": False, "fixable_unsafe": False}


F = Path("pkg/m.py")


# ── _smell ──────────────────────────────────────────────────────────────────

def test_smell_new() -> None:
    check("smell: new at the limit", smells._smell(F, 7, "R-X", "thing", (10, 3, 10), True),
          issue(F, 7, "R-X", "error", "thing is 10 (a smell at 10); NEW: was 3 before this change"))


def test_smell_under() -> None:
    check("smell: under the limit", smells._smell(F, 7, "R-X", "thing", (9, 0, 10), True), None)


def test_smell_unchanged() -> None:
    check("smell: unchanged at the limit",
          smells._smell(F, 2, "R-Y", "other", (10, 10, 10), True),
          issue(F, 2, "R-Y", "warning", "other is 10 (a smell at 10); existing: was already 10, so a warning"))


def test_smell_grew() -> None:
    check("smell: grew", smells._smell(F, 3, "R-Z", "thing", (12, 10, 10), True),
          issue(F, 3, "R-Z", "error", "thing is 12 (a smell at 10); existing, and it GREW from 10: "
                                      "a smell may shrink or stay, not grow"))


def test_smell_shrunk() -> None:
    check("smell: shrunk", smells._smell(F, 4, "R-Z", "thing", (11, 15, 10), True),
          issue(F, 4, "R-Z", "warning", "thing is 11 (a smell at 10); existing: was already 15, so a warning"))


def test_smell_no_history() -> None:
    check("smell: no history", smells._smell(F, 5, "R-Q", "thing", (10, 0, 10), False),
          issue(F, 5, "R-Q", "error", "thing is 10 (a smell at 10); NEW: no git history here, "
                                      "so its age cannot be told and it counts as new"))


# ── _smells_context ─────────────────────────────────────────────────────────

def test_context_committed() -> None:
    repo = repo_with({"sub/a.py": "x = 1\n"})
    try:
        check("context: committed repo, HEAD",
              attempt(lambda: smells._smells_context(repo / "sub" / "a.py")), (repo, "HEAD"))
        sha = git(repo, "rev-parse", "HEAD").strip()
        smells._SMELLS["base"] = sha
        check("context: committed repo, a named base",
              attempt(lambda: smells._smells_context(repo / "sub" / "a.py")), (repo, sha))
        check("context: a named base that does not resolve", refusal(repo / "sub" / "a.py", "no-such"),
              ("no-such",))
        check("context: a range is not one commit", refusal(repo / "sub" / "a.py", "HEAD..HEAD"),
              ("HEAD..HEAD",))
    finally:
        smells._SMELLS["base"] = "HEAD"
        drop(repo)


def refusal(path: Path, base: str) -> Any:
    """The refusal's arguments for BASE, or what happened instead."""
    smells._SMELLS["base"] = base
    try:
        return f"no error: {smells._smells_context(path)}"
    except smells._NoSmellsBaseError as e:
        return e.args
    finally:
        smells._SMELLS["base"] = "HEAD"


def test_context_no_history() -> None:
    repo = repo_with({})
    try:
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        check("context: no commits", smells._smells_context(repo / "a.py"), None)
        outside = repo.parent / "a.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        check("context: outside git", smells._smells_context(outside), None)
    finally:
        drop(repo)


# ── _base_text ──────────────────────────────────────────────────────────────

def test_base_text() -> None:
    repo = repo_with({"a.py": "x = 1\n"})
    try:
        check("base_text: a file at the base", attempt(lambda: smells._base_text(repo, "HEAD", "a.py")),
              "x = 1\n")
        check("base_text: no such file at the base",
              attempt(lambda: smells._base_text(repo, "HEAD", "b.py")), None)
    finally:
        drop(repo)


# ── _base_measure ───────────────────────────────────────────────────────────

KEPT = long_func("kept", 30) + "\n\n" + complex_func("twisty", 6)
OLD = long_func("moving", 12) + "\n\n" + complex_func("winding", 4)
# A file named None at the base: a lookup that lost its path asks for
# "HEAD:None", and must not find something.
NONE_FILE = complex_func("decoy", 9)


def test_base_measure() -> None:
    repo = repo_with({"kept.py": KEPT, "old.py": OLD, "bad.py": "def (:\n", "None": NONE_FILE})
    ctx = (repo, "HEAD")
    try:
        check("base_measure: no history", smells._base_measure(repo / "kept.py", None), (0, {}))
        check("base_measure: by its own path", attempt(lambda: smells._base_measure(repo / "kept.py", ctx)),
              measure_python(KEPT))
        (repo / "new.py").write_text("y = 2\n", encoding="utf-8")
        check("base_measure: new at the base", attempt(lambda: smells._base_measure(repo / "new.py", ctx)),
              (0, {}))
        check("base_measure: unparsable at the base",
              attempt(lambda: smells._base_measure(repo / "bad.py", ctx)), (0, {}))
        git(repo, "mv", "old.py", "renamed.py")
        check("base_measure: by the path it was renamed from",
              attempt(lambda: smells._base_measure(repo / "renamed.py", ctx)), measure_python(OLD))
    finally:
        drop(repo)


# ── _function_smells ────────────────────────────────────────────────────────

def test_function_smells_no_history() -> None:
    f = Path("pkg/f.py")
    why = "NEW: no git history here, so its age cannot be told and it counts as new"
    check("function_smells: no history",
          attempt(lambda: smells._function_smells(f, {"f": (120, 12, 7)}, {}, None)),
          [issue(f, 7, "SMELL-FUNC-LINES", "error", f"function f length is 120 (a smell at 100); {why}"),
           issue(f, 7, "SMELL-FUNC-COMPLEXITY", "error", f"function f complexity is 12 (a smell at 11); {why}")])


def test_function_smells_history() -> None:
    lng = long_func("lng", 100)
    cpx = complex_func("cpx", 11)
    repo = repo_with({"old.py": lng + "\n\n" + cpx})
    ctx = (repo, "HEAD")
    try:
        base = measure_python(lng + "\n\n" + cpx)[1]
        (repo / "old.py").write_text("def other():\n    return 1\n", encoding="utf-8")
        part = repo / "part.py"
        part.write_text(cpx + "\n\n" + lng, encoding="utf-8")   # its language is read from the file
        lng_now = (base["lng"][0], base["lng"][1], 20)
        cpx_now = (base["cpx"][0], base["cpx"][1], 3)
        check("function_smells: moved from another file",
              attempt(lambda: smells._function_smells(part, {"cpx": cpx_now, "lng": lng_now}, {}, ctx)),
              [None,
               issue(part, 3, "SMELL-FUNC-COMPLEXITY", "warning",
                     "function cpx complexity is 11 (a smell at 11); existing: was already 11, so a warning"),
               issue(part, 20, "SMELL-FUNC-LINES", "warning",
                     "function lng length is 100 (a smell at 100); existing: was already 100, so a warning"),
               None])
        check("function_smells: before from the base file",
              attempt(lambda: smells._function_smells(part, {"keep": (120, 12, 9)}, {"keep": (120, 12, 9)}, ctx)),
              [issue(part, 9, "SMELL-FUNC-LINES", "warning",
                     "function keep length is 120 (a smell at 100); existing: was already 120, so a warning"),
               issue(part, 9, "SMELL-FUNC-COMPLEXITY", "warning",
                     "function keep complexity is 12 (a smell at 11); existing: was already 12, so a warning")])
        check("function_smells: new top level",
              attempt(lambda: smells._function_smells(part, {TOP_LEVEL: (120, 12, 1)}, {}, ctx)),
              [issue(part, 1, "SMELL-FUNC-LINES", "error",
                     f"function {TOP_LEVEL} length is 120 (a smell at 100); NEW: was 0 before this change"),
               issue(part, 1, "SMELL-FUNC-COMPLEXITY", "error",
                     f"function {TOP_LEVEL} complexity is 12 (a smell at 11); NEW: was 0 before this change")])
    finally:
        drop(repo)


if __name__ == "__main__":
    sys.exit(run(globals(), "smells history"))
