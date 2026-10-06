#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_smells.py
check.py's smells arm: a smell that is NEW fails, one that EXISTS warns,
and one that exists and GROWS fails.

THE RULE THIS PINS DOWN
    Four smells (METHODOLOGY 2.14): a file too long, a function too long, a
    function too complex, a file too complex. Each FAILS when it was not there
    at the base (HEAD by default, --smells-base in CI) or was there and got
    bigger, and WARNS when it was already there and did not grow, so adopting
    the gate never blocks on what is already there, and nothing gets worse.

    A smell that MOVES is existing: a renamed file, or a function moved to
    another file. Identity is the file and the qualified name, so a function
    inherits a measure from elsewhere only when the base tree defined that name
    in exactly one file and that file no longer defines it.

Runs `check.py --tools smells` in a throwaway git repo, so ruff and mypy
cannot blur the verdict. One small function per group: this file is held to
the rule it tests.

    python3 tests/test_check_smells.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CHECK_PY = ROOT / "check.py"

fails = 0
checks = 0


def check(what: str, ok: bool, detail: str = "") -> None:
    global fails, checks
    checks += 1
    if not ok:
        fails += 1
    print(f"  [{' ok ' if ok else 'FAIL'}] {what}")
    if not ok and detail:
        for line in detail.strip().splitlines()[:12]:
            print(f"         {line}")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=JP_TOOLS test",
                    "-c", "commit.gpgsign=false", *args], cwd=repo, capture_output=True,
                   text=True, check=True)


def commit(repo: Path, msg: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True, check=True).stdout.strip()


def smells(target: Path, *extra: str) -> tuple[int, list[tuple[str, str]], str]:
    """(exit code, sorted (rule, severity) pairs, the full output for detail)."""
    r = subprocess.run([sys.executable, str(CHECK_PY), str(target), "--tools", "smells", *extra],
                       capture_output=True, text=True, check=False)
    raw = r.stdout + r.stderr
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        return r.returncode, [], raw
    issues: list[dict[str, Any]] = [i for c in doc.get("checks", []) for i in c.get("issues", [])]
    pairs = sorted((str(i["rule"]), str(i["severity"])) for i in issues)
    return r.returncode, pairs, raw


def long_func(name: str, n: int) -> str:
    """A function exactly n lines long."""
    return f"def {name}():\n" + "    x = 1\n" * (n - 1)


def complex_func(name: str, k: int) -> str:
    """A function of McCabe complexity exactly k."""
    body = "".join(f"    if a == {i}:\n        return {i}\n" for i in range(k - 1))
    return f"def {name}(a):\n{body}    return -1\n"


def busy_source() -> str:
    """Eleven functions of complexity 10: no function smell, file sum 110."""
    return "\n\n".join(complex_func(f"b{i}", 10) for i in range(11))


def function_length(repo: Path) -> None:
    print("Function length (100 lines):")
    f = repo / "m.py"
    f.write_text(long_func("f", 100), encoding="utf-8")
    rc, pairs, raw = smells(f)
    check("a NEW 100-line function fails, exit 1",
          rc == 1 and ("SMELL-FUNC-LINES", "error") in pairs, raw)
    commit(repo, "long function")
    f.write_text(long_func("f", 100) + "\n\ndef g():\n    return 1\n", encoding="utf-8")
    rc, pairs, raw = smells(f)
    check("the same function once committed only warns, exit 0",
          rc == 0 and pairs == [("SMELL-FUNC-LINES", "warning")], raw)
    f.write_text(long_func("h", 99), encoding="utf-8")
    commit(repo, "99 lines")
    rc, pairs, raw = smells(f)
    check("CONTROL: a 99-line function is no smell", rc == 0 and not pairs, raw)
    f.write_text(long_func("h", 100), encoding="utf-8")
    rc, pairs, raw = smells(f)
    check("growing it to 100 lines CROSSES the line: a new smell, exit 1",
          rc == 1 and ("SMELL-FUNC-LINES", "error") in pairs, raw)


def function_complexity(repo: Path) -> None:
    print("\nFunction complexity (over 10):")
    f = repo / "m.py"
    f.write_text(complex_func("c", 10), encoding="utf-8")
    rc, pairs, raw = smells(f)
    check("CONTROL: complexity 10 is no smell", rc == 0 and not pairs, raw)
    f.write_text(complex_func("c", 11), encoding="utf-8")
    rc, pairs, raw = smells(f)
    check("complexity 11 in a new function fails, exit 1",
          rc == 1 and ("SMELL-FUNC-COMPLEXITY", "error") in pairs, raw)


def growth(repo: Path) -> None:
    """An existing smell may stay or shrink; growing it fails (#113)."""
    print("\nAn existing smell that grows:")
    f = repo / "grow.py"
    f.write_text(long_func("g", 120) + "\n\n" + complex_func("k", 15), encoding="utf-8")
    commit(repo, "smelly at base")
    f.write_text(long_func("g", 121) + "\n\n" + complex_func("k", 15), encoding="utf-8")
    rc, pairs, raw = smells(f)
    check("a 120-line function grown to 121 fails, exit 1, saying it grew",
          rc == 1 and ("SMELL-FUNC-LINES", "error") in pairs and "GREW from 120" in raw, raw)
    f.write_text(long_func("g", 120) + "\n\n" + complex_func("k", 16), encoding="utf-8")
    rc, pairs, raw = smells(f)
    check("a complexity-15 function made 16 fails, exit 1, saying it grew",
          rc == 1 and ("SMELL-FUNC-COMPLEXITY", "error") in pairs and "GREW from 15" in raw, raw)
    f.write_text(long_func("g", 110) + "\n\n" + complex_func("k", 12), encoding="utf-8")
    rc, pairs, raw = smells(f)
    check("CONTROL: both shrunk but still smells only warn, exit 0",
          rc == 0 and pairs == [("SMELL-FUNC-COMPLEXITY", "warning"), ("SMELL-FUNC-LINES", "warning")],
          raw)
    f.write_text(long_func("g", 120) + "\n\n" + complex_func("k", 15), encoding="utf-8")
    rc, pairs, raw = smells(f)
    check("CONTROL: both unchanged only warn, exit 0",
          rc == 0 and pairs == [("SMELL-FUNC-COMPLEXITY", "warning"), ("SMELL-FUNC-LINES", "warning")],
          raw)


def file_smells(repo: Path) -> None:
    print("\nFile length (1000 lines) and file complexity (over 100):")
    big = repo / "big.py"
    big.write_text("x = 1\n" * 1000, encoding="utf-8")
    rc, pairs, raw = smells(big)
    # 1000 lines of code at the top level: the file is long, and so is its main.
    check("a NEW 1000-line file fails, exit 1, and so does its 1000-line top level",
          rc == 1 and pairs == [("SMELL-FILE-LINES", "error"), ("SMELL-FUNC-LINES", "error")],
          raw)
    commit(repo, "big file")
    big.write_text("y = 1\n" * 1000, encoding="utf-8")
    rc, pairs, raw = smells(big)
    check("an existing 1000-line file edited at the same size only warns, exit 0, and so does its top level",
          rc == 0 and pairs == [("SMELL-FILE-LINES", "warning"), ("SMELL-FUNC-LINES", "warning")],
          raw)
    big.write_text("x = 1\n" * 1001, encoding="utf-8")
    rc, pairs, raw = smells(big)
    check("an existing 1000-line file GROWN to 1001 fails, exit 1, saying it grew",
          rc == 1 and ("SMELL-FILE-LINES", "error") in pairs and "GREW from 1000" in raw, raw)
    big.write_text("x = 1\n" * 1000, encoding="utf-8")
    busy = repo / "busy.py"
    busy.write_text(busy_source(), encoding="utf-8")
    rc, pairs, raw = smells(busy)
    check("eleven functions of complexity 10 (sum 110) fail as a NEW file-complexity smell",
          rc == 1 and pairs == [("SMELL-FILE-COMPLEXITY", "error")], raw)


def the_base(repo: Path) -> None:
    print("\nThe base:")
    busy = repo / "busy.py"
    early = commit(repo, "busy file")
    rc, pairs, raw = smells(busy)
    check("committed, the busy file only warns against HEAD, exit 0",
          rc == 0 and pairs == [("SMELL-FILE-COMPLEXITY", "warning")], raw)
    git(repo, "rm", "-q", "busy.py")
    commit(repo, "drop busy")
    busy.write_text(busy_source(), encoding="utf-8")
    commit(repo, "busy again")
    rc, pairs, raw = smells(busy, "--smells-base", f"{early}~1")
    check("against an OLDER base (a PR's) where the file did not exist, it is new, exit 1",
          rc == 1 and ("SMELL-FILE-COMPLEXITY", "error") in pairs, raw)
    rc, pairs, raw = smells(busy, "--smells-base", "no-such-ref")
    # The message, not just the code: exit 2 alone also passes on a check.py
    # with no --smells-base at all, where argparse refuses the flag.
    check("a --smells-base that does not resolve is refused as not run, saying so, exit 2",
          rc == 2 and "does not resolve" in raw, raw)


def moves(repo: Path) -> None:
    """A smell that MOVES (a renamed file, a function moved to another file) is
    not new, or the arm fails the very split the file-length smell asks for."""
    print("\nMoving a smell:")
    old = repo / "old.py"
    old.write_text(long_func("lng", 100) + "\n\n" + complex_func("cpx", 11), encoding="utf-8")
    commit(repo, "old smells")
    git(repo, "mv", "old.py", "moved.py")
    rc, pairs, raw = smells(repo / "moved.py")
    check("D: a file moved with git mv keeps its smells as existing: warnings, exit 0",
          rc == 0 and pairs == [("SMELL-FUNC-COMPLEXITY", "warning"),
                                ("SMELL-FUNC-LINES", "warning")], raw)
    commit(repo, "moved")
    moved = repo / "moved.py"
    moved.write_text(long_func("lng", 100), encoding="utf-8")
    part = repo / "part.py"
    part.write_text(complex_func("cpx", 11), encoding="utf-8")
    rc, pairs, raw = smells(part)
    check("E: a smelly function moved out into a new file is existing: a warning, exit 0",
          rc == 0 and pairs == [("SMELL-FUNC-COMPLEXITY", "warning")], raw)
    part.write_text(complex_func("cpx", 11) + "\n\n" + complex_func("fresh", 11),
                    encoding="utf-8")
    rc, pairs, raw = smells(part)
    check("CONTROL: a genuinely new smelly function in that file still fails, exit 1",
          rc == 1 and ("SMELL-FUNC-COMPLEXITY", "error") in pairs, raw)
    part.write_text(complex_func("cpx", 11), encoding="utf-8")
    commit(repo, "split")
    same_name(repo)


def same_name(repo: Path) -> None:
    """A name alone carries no measure between files (#114): identity is the
    file and the qualified name, so a measure travels only from the one base
    definition that is gone from its file."""
    keep = repo / "keep.py"
    keep.write_text(complex_func("main", 20), encoding="utf-8")
    commit(repo, "a smelly main that stays")
    fresh = repo / "fresh.py"
    fresh.write_text(complex_func("main", 12), encoding="utf-8")
    rc, pairs, raw = smells(fresh)
    check("F: a NEW main of complexity 12 does not inherit the 20 of a main still in keep.py: fails, exit 1",
          rc == 1 and ("SMELL-FUNC-COMPLEXITY", "error") in pairs and "was 0" in raw, raw)
    fresh.unlink()
    (repo / "twin.py").write_text(complex_func("main", 20), encoding="utf-8")
    commit(repo, "a second smelly main")
    keep.write_text("def other():\n    return 1\n", encoding="utf-8")
    moved = repo / "moved_main.py"
    moved.write_text(complex_func("main", 20), encoding="utf-8")
    rc, pairs, raw = smells(moved)
    check("G: a main gone from keep.py, while twin.py has one too, is not known to have moved: fails, exit 1",
          rc == 1 and ("SMELL-FUNC-COMPLEXITY", "error") in pairs, raw)


def top_level_source(k: int) -> str:
    """Code outside any function, of complexity exactly k."""
    return "a = 1\n" + "".join(f"if a == {i}:\n    a = {i}\n" for i in range(k - 1))


def top_level(repo: Path) -> None:
    """Code outside every function is the file's main, measured as one more function."""
    print("\nThe top level (code outside every function):")
    script = repo / "script.py"
    script.write_text(top_level_source(11), encoding="utf-8")
    rc, pairs, raw = smells(script)
    check("top-level code of complexity 11 fails as a NEW function smell, exit 1",
          rc == 1 and ("SMELL-FUNC-COMPLEXITY", "error") in pairs and "(top level)" in raw, raw)
    script.write_text(top_level_source(10), encoding="utf-8")
    rc, pairs, raw = smells(script)
    check("CONTROL: top-level code of complexity 10 is no smell", rc == 0 and not pairs, raw)
    script.write_text("x = 1\n" * 100, encoding="utf-8")
    rc, pairs, raw = smells(script)
    check("100 top-level statements fail as a NEW function-length smell, exit 1",
          rc == 1 and ("SMELL-FUNC-LINES", "error") in pairs and "(top level)" in raw, raw)
    script.write_text('"""A docstring."""\n' + "import os\n" * 99 + "\n\n" + long_func("f", 5),
                      encoding="utf-8")
    rc, pairs, raw = smells(script)
    check("CONTROL: a docstring, imports and definitions are not top-level code",
          rc == 0 and not pairs, raw)


def outside_and_clean(repo: Path, tmp: Path) -> None:
    print("\nNo history, and a clean file:")
    loose = tmp / "loose.py"
    loose.write_text(long_func("z", 100), encoding="utf-8")
    rc, pairs, raw = smells(loose)
    check("outside git there is no history, so a smell is new, exit 1",
          rc == 1 and ("SMELL-FUNC-LINES", "error") in pairs, raw)
    check("... and it says there is no history, not that it was 0 before",
          "no git history here" in raw and "was 0 before" not in raw, raw)
    clean = repo / "clean.py"
    clean.write_text("def ok():\n    return 1\n", encoding="utf-8")
    rc, pairs, raw = smells(clean)
    check("CONTROL: a small clean file has no smells, exit 0", rc == 0 and not pairs, raw)


def main() -> int:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repo = tmp / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        function_length(repo)
        function_complexity(repo)
        growth(repo)
        file_smells(repo)
        the_base(repo)
        moves(repo)
        top_level(repo)
        outside_and_clean(repo, tmp)
    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
