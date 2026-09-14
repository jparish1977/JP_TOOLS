#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_dashes.py
No NEW em-dashes: the hook refuses a commit that ADDS one, and fix-dashes.py
fixes exactly those lines and nothing else.

Joe, 2026-09-14: "appeasing the fickle joe is only worth it if this does the
trcik, if it doesnt and it keeps wasting time then ill just drop the damned
ban", and on a way through: "itll need a flag to allow instances where its
actually a thing we need to do". So the tests pin both halves: that it stops a
new one cheaply, and that a line already there, a dated record, is never
touched.

Uses .md files, which check.py skips, so ruff and mypy cannot blur the verdict.
The hook is installed (a shim) with JP_TOOLS_DIR naming this tree.

    python3 tests/test_dashes.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASH = chr(0x2014)  # built from its code point, so this file does not carry one

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


def run(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=repo, capture_output=True, text=True, check=False,
                          env=env)


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return run(repo, "git", "-c", "user.email=test@example.invalid", "-c", "user.name=JP_TOOLS test",
               "-c", "commit.gpgsign=false", *args, env=env)


def setup(tmp: Path) -> Path:
    """A repo whose committed notes.md already has an em-dash, then the hook."""
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "notes.md").write_text(f"old line {DASH} a dated record\n", encoding="utf-8")
    git(repo, "add", "notes.md")
    git(repo, "commit", "-q", "-m", "history, before the hook")
    run(repo, sys.executable, str(ROOT / "install-hooks.py"), str(repo))
    return repo


def add_line(repo: Path, name: str, text: str) -> None:
    p = repo / name
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    p.write_text(old + text + "\n", encoding="utf-8")
    git(repo, "add", name)


def refusal_cases(repo: Path) -> None:
    print("The hook:")
    add_line(repo, "notes.md", f"new line {DASH} added today")
    r = git(repo, "commit", "-m", "adds one")
    out = r.stdout + r.stderr
    check("a commit that ADDS an em-dash is refused", r.returncode != 0 and "em-dash" in out, out)
    check("... naming the file and line, and the command that fixes it",
          "notes.md:2" in out and "fix-dashes.py" in out, out)
    check("... and not naming the old line, which is history", "notes.md:1" not in out, out)
    r = git(repo, "commit", "-m", "allowed", env=dict(os.environ, JP_TOOLS_ALLOW_EMDASH="1"))
    out = r.stdout + r.stderr
    check("JP_TOOLS_ALLOW_EMDASH=1 lets that commit through, and says so",
          r.returncode == 0 and "JP_TOOLS_ALLOW_EMDASH is set" in out, out)


def fixer_cases(repo: Path) -> None:
    print("\nThe fixer:")
    # notes.md: line 1 committed before the hook, line 2 by the allowed commit.
    add_line(repo, "notes.md", f"another {DASH} new line")
    r = run(repo, sys.executable, str(ROOT / "fix-dashes.py"), "--check")
    check("--check lists the staged added line and exits 1",
          r.returncode == 1 and r.stdout.split() == ["notes.md:3"], r.stdout + r.stderr)
    r = run(repo, sys.executable, str(ROOT / "fix-dashes.py"))
    text = (repo / "notes.md").read_text(encoding="utf-8").splitlines()
    check("fix-dashes.py rewrites only the new line, to --",
          r.returncode == 0 and len(text) == 3 and text[2] == "another -- new line",
          r.stdout + str(text))
    check("... and leaves the old lines as they were", DASH in text[0] and DASH in text[1], str(text))
    r = git(repo, "commit", "-m", "fixed")
    check("after the fix the commit goes through", r.returncode == 0, r.stdout + r.stderr)
    r = run(repo, sys.executable, str(ROOT / "fix-dashes.py"), "--check")
    check("CONTROL: with nothing staged, --check is silent and exits 0",
          r.returncode == 0 and not r.stdout.strip(), r.stdout)


def unstaged_case(repo: Path) -> None:
    print("\nUnstaged changes:")
    add_line(repo, "other.md", f"staged {DASH} line")
    with open(repo / "other.md", "a", encoding="utf-8") as fh:
        fh.write("an unstaged edit\n")
    r = run(repo, sys.executable, str(ROOT / "fix-dashes.py"))
    check("a file with unstaged changes too is refused, not rewritten",
          r.returncode == 1 and "unstaged" in r.stdout
          and DASH in (repo / "other.md").read_text(encoding="utf-8"), r.stdout)


def edge_cases(tmp: Path) -> None:
    """claude-config's review of #84: two ways a new em-dash got past --check.
    Each in a fresh repo, so nothing staged earlier can make it pass."""
    print("\nDiff edges (#84 review):")
    edges = (
        ("plus.md", f"++ reads like a header {DASH} but is not",
         "an added line starting '++ ' (a '+++ ' row in the diff)"),
        ("nöte.md", f"a line {DASH} in a non-ASCII file",
         "a file with a non-ASCII name (quoted in a diff header)"),
    )
    for i, (name, text, what) in enumerate(edges):
        d = tmp / f"edge{i}"
        d.mkdir()
        repo = setup(d)
        add_line(repo, name, text)
        r = git(repo, "commit", "-m", "edge")
        out = r.stdout + r.stderr
        check(f"{what} is refused", r.returncode != 0 and f"{name}:1" in out, out)
        run(repo, sys.executable, str(ROOT / "fix-dashes.py"))
        r = git(repo, "commit", "-m", "edge, fixed")
        fixed = (repo / name).read_text(encoding="utf-8")
        check("... and fix-dashes.py fixes it, so the commit goes through",
              r.returncode == 0 and DASH not in fixed, r.stdout + r.stderr + fixed)


def rename_cases(tmp: Path) -> None:
    """claude-config's second review of #84: the lines a moved file carries are
    not added lines, and a commit that renames is still checked."""
    print("\nRenames (#84 review):")
    allow = dict(os.environ, JP_TOOLS_ALLOW_EMDASH="1")
    record = "".join(f"line {n}\n" for n in range(1, 6)) + f"dated {DASH} record\n"
    cases = (
        ("a plain new line", False, "moving a dated record and adding a plain line passes"),
        (f"a new {DASH} line", True, "moving a file and adding a dash line is refused, line 7 only"),
    )
    for i, (added, want_refused, what) in enumerate(cases):
        d = tmp / f"rename{i}"
        d.mkdir()
        repo = setup(d)
        (repo / "record.md").write_text(record, encoding="utf-8")
        git(repo, "add", "record.md")
        git(repo, "commit", "-q", "-m", "the record", env=allow)
        git(repo, "mv", "record.md", "moved.md")
        add_line(repo, "moved.md", added)
        r = git(repo, "commit", "-m", "moved")
        out = r.stdout + r.stderr
        ok = (r.returncode != 0 and "moved.md:7" in out) if want_refused else r.returncode == 0
        check(what, ok and "moved.md:6" not in out, out)


def main() -> int:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return 0
    os.environ["JP_TOOLS_DIR"] = str(ROOT)
    with tempfile.TemporaryDirectory() as td:
        repo = setup(Path(td))
        refusal_cases(repo)
        fixer_cases(repo)
        unstaged_case(repo)
        edge_cases(Path(td))
        rename_cases(Path(td))
    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
