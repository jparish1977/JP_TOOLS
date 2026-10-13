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
    _renamed_from: the base path of a file git sees as renamed, even with
    rename detection turned off in the repo's config and a space in the new
    name; None for a file that was not renamed, while another one was.
    _function_elsewhere: (size, complexity) at the base for a function, a
    nested method included, that left the one file defining it, whether that
    file still exists, was deleted, or has a colon in its name; (0, 0) for a
    language with no definition pattern, for a name defined in two files at
    the base, and for one still defined where it was. "Still defined" is read
    as UTF-8 whatever the default codec, and a stray byte that is not UTF-8
    does not stop it being read.

Each git repo is a throwaway one; the code under test is imported, never run
as a subprocess, so a mutation run sees every line.

    python3 tests/test_smells_history.py
"""

import io
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

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


# ── _renamed_from and _function_elsewhere ───────────────────────────────────

def test_renamed_from() -> None:
    repo = repo_with({"r1.py": complex_func("one", 5), "r2.py": complex_func("two", 7)})
    try:
        git(repo, "config", "diff.renames", "false")
        git(repo, "mv", "r1.py", "new name.py")
        check("renamed_from: a rename, detection off in config, a space in the name",
              attempt(lambda: smells._renamed_from(repo, "HEAD", "new name.py")), "r1.py")
        check("renamed_from: a file that was not renamed",
              attempt(lambda: smells._renamed_from(repo, "HEAD", "r2.py")), None)
    finally:
        drop(repo)


NESTED = "class A:\n    class B:\n" + "".join(
    "        " + line + "\n" for line in complex_func("deep", 6).splitlines())
# A function named outside ASCII, so a read in any codec but UTF-8 misses it.
STILL = complex_func("café", 8)


def base_of(src: str, name: str) -> tuple[int, int]:
    size, cx, _ = measure_python(src)[1][name]
    return (size, cx)


def test_function_elsewhere_moved() -> None:
    repo = repo_with({"old.py": NESTED, "gone.py": complex_func("removed", 9),
                      "a:b.py": complex_func("colon", 4)})
    try:
        (repo / "old.py").write_text("x = 1\n", encoding="utf-8")
        (repo / "a:b.py").write_text("x = 1\n", encoding="utf-8")
        git(repo, "rm", "-q", "gone.py")
        check("elsewhere: a nested method moved out",
              attempt(lambda: smells._function_elsewhere(repo, "HEAD", "A.B.deep", "python")),
              base_of(NESTED, "A.B.deep"))
        check("elsewhere: its file deleted",
              attempt(lambda: smells._function_elsewhere(repo, "HEAD", "removed", "python")),
              base_of(complex_func("removed", 9), "removed"))
        check("elsewhere: a colon in its file's name",
              attempt(lambda: smells._function_elsewhere(repo, "HEAD", "colon", "python")),
              base_of(complex_func("colon", 4), "colon"))
    finally:
        drop(repo)


def test_function_elsewhere_not_moved() -> None:
    repo = repo_with({"t1.py": complex_func("dup", 5), "t2.py": complex_func("dup", 5),
                      "still.py": STILL})
    try:
        (repo / "still.py").write_bytes(STILL.encode("utf-8") + b"# \xff\n")
        check("elsewhere: a language with no definition pattern",
              attempt(lambda: smells._function_elsewhere(repo, "HEAD", "dup", "no-such-language")), (0, 0))
        check("elsewhere: defined in two files at the base",
              attempt(lambda: smells._function_elsewhere(repo, "HEAD", "dup", "python")), (0, 0))
        check("elsewhere: still defined where it was",
              attempt(lambda: smells._function_elsewhere(repo, "HEAD", "café", "python")), (0, 0))
        with mock.patch.object(io, "text_encoding", cp1252_default):
            check("elsewhere: CONTROL under the patch a read with no codec decodes as cp1252",
                  attempt(lambda: (repo / "still.py").read_text().splitlines()[0]), "def cafÃ©(a):")
            got = attempt(lambda: smells._function_elsewhere(repo, "HEAD", "café", "python"))
        check("elsewhere: still defined, read as UTF-8 whatever the default codec", got, (0, 0))
    finally:
        drop(repo)


def cp1252_default(encoding: str | None, stacklevel: int = 2) -> str:
    """io.text_encoding with cp1252 as the codec a read with none gets."""
    return encoding or "cp1252"


if __name__ == "__main__":
    sys.exit(run(globals(), "smells history"))
