#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_scratch_refs.py
Nothing relied on runs from scratch: the hook refuses a commit whose staged
files name a scratch path anywhere in them, and scratch-refs.py --check lists
each one.

Pins: every path form is refused and words that only look like one are not;
the whole staged file is read, old lines included, and the staged copy rather
than the working one; a binary file is not read; a rename is read under its
new name, a deletion is not read; a git step that fails is NOT_RUN and a
checker that crashes is BROKEN, never clean.

Every scratch path here is built when the test runs, so this file does not
hold one. Uses .md files, which check.py skips, so ruff and mypy cannot blur
the verdict. The hook is installed (a shim) with JP_TOOLS_DIR naming this tree.

    python3 tests/test_scratch_refs.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, run
from jpexit import FINDINGS, NOT_RUN, OK
from process_check import NONZERO, check_exit

WORD = "scr" + "atch"
CLAUDE_TMP = "/tmp/claude-" + "1000"
REFUSED = (
    f"~/{WORD}/run.sh", f"$HOME/{WORD}/x", f"${{HOME}}/{WORD}/x", f"/home/joe/{WORD}/state",
    f"/root/{WORD}/x", f"bash ~/{WORD}", f"{CLAUDE_TMP}/x", f"/a/b/{WORD}pad/c.sh",
)
LET_BY = (
    f"a {WORD} directory gets deleted", f"~/{WORD}ing/x", f"/home/joe/{WORD}y/x",
    "/tmp/claude-abc/x", f"~/projects/{WORD}/x", f"{WORD}pad",
)


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """git in REPO; the installed hook told which JP_TOOLS to run, this tree
    unless ENV says otherwise. Named on every call, never left to the
    environment: under pytest a suite's __main__ never runs, and a value
    another suite set in the same worker would decide which tree ran."""
    return subprocess.run(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=JP_TOOLS test",
                           "-c", "commit.gpgsign=false", *args], cwd=repo, capture_output=True, text=True,
                          check=False, env=env or dict(os.environ, JP_TOOLS_DIR=str(ROOT)))


def _check(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ROOT / "scratch-refs.py"), "--check"], cwd=repo,
                          capture_output=True, text=True, check=False)


def _repo(where: Path, hook: bool = True) -> Path:
    """A repo whose committed old.md names a scratch path, then the hook."""
    repo = where / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "old.md").write_text(f"line one\nran ~/{WORD}/x.sh\n", encoding="utf-8")
    _git(repo, "add", "old.md")
    _git(repo, "commit", "-q", "-m", "history, before the hook")
    if hook:
        subprocess.run([sys.executable, str(ROOT / "install-hooks.py"), str(repo)], capture_output=True,
                       check=False)
    return repo


def _stage(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)


def test_every_form_is_listed_and_lookalikes_are_not() -> None:
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(Path(td), hook=False)
        _stage(repo, "a.md", "".join(f"{t}\n" for t in (*LET_BY, *REFUSED)))
        p = _check(repo)
        first = len(LET_BY) + 1
        check("each form: FINDINGS, its line listed, no lookalike",
              (p.returncode, p.stdout),
              (FINDINGS, "".join(f"  a.md:{first + i}: {t}\n" for i, t in enumerate(REFUSED))))
        _stage(repo, "a.md", "".join(f"{t}\n" for t in LET_BY))
        p = _check(repo)
        check("CONTROL: only lookalikes staged: OK, silent", (p.returncode, p.stdout, p.stderr), (OK, "", ""))


def test_the_hook_refuses_the_whole_file_as_staged() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(Path(td))
        _stage(repo, "new.md", "nothing here\n")
        r = _git(repo, "commit", "-m", "clean")
        check_exit("CONTROL: a file with none commits", r, 0)
        _stage(repo, "old.md", f"line one\nran ~/{WORD}/x.sh\nline three\n")
        r = _git(repo, "commit", "-m", "touches the old file")
        check_exit("a staged file whose OLD line names scratch is refused, named, and the idiot told", r, NONZERO,
                   says=[f"old.md:2: ran ~/{WORD}/x.sh", "you fucking idiot", "METHODOLOGY 2.11, rule 6"])
        (repo / "old.md").write_text("line one\nfixed\n", encoding="utf-8")
        r = _git(repo, "commit", "-m", "fixed in the tree only")
        check_exit("the staged copy is what is read: fixed only in the working tree, still refused", r, NONZERO)
        _git(repo, "add", "old.md")
        (repo / "old.md").write_text(f"~/{WORD}/again\n", encoding="utf-8")
        r = _git(repo, "commit", "-m", "fixed as staged")
        check_exit("  and fixed as staged, with scratch only in the working tree, it commits", r, 0)


def test_what_is_and_is_not_read() -> None:
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(Path(td), hook=False)
        (repo / "b.bin").write_bytes(b"\0" + f"~/{WORD}/x".encode())
        _git(repo, "add", "b.bin")
        check("a binary file is not read", _check(repo).returncode, OK)
        _git(repo, "commit", "-q", "-m", "binary")
        _git(repo, "rm", "-q", "old.md")
        check("a deleted file is not read", _check(repo).returncode, OK)
        _git(repo, "reset", "-q", "--hard")
        _git(repo, "mv", "old.md", "moved.md")
        p = _check(repo)
        check("a renamed file is read under its new name", (p.returncode, p.stdout),
              (FINDINGS, f"  moved.md:2: ran ~/{WORD}/x.sh\n"))


def test_what_cannot_look_says_so() -> None:
    with tempfile.TemporaryDirectory() as td:
        plain = Path(td) / "plain"
        plain.mkdir()
        p = _check(plain)
        check("not a repository: NOT_RUN, said on stderr", (p.returncode, p.stdout, p.stderr),
              (NOT_RUN, "", "scratch-refs: not in a git repository\n"))
        p = subprocess.run([sys.executable, str(ROOT / "scratch-refs.py")], cwd=plain, capture_output=True,
                           text=True, check=False)
        check_exit("no --check: NOT_RUN and the usage, never a silent OK", p, NOT_RUN, says=["usage:"])
        p = subprocess.run([sys.executable, str(ROOT / "scratch-refs.py"), "--help"], cwd=plain,
                           capture_output=True, text=True, check=False)
        check_exit("--help: OK and the usage", p, OK, says=["usage:"])
        if os.geteuid() == 0:
            print("SKIP: running as root, which reads a mode-000 index anyway")
            return
        repo = _repo(Path(td), hook=False)
        _stage(repo, "a.md", f"~/{WORD}/x\n")
        index = repo / ".git" / "index"
        index.chmod(0)
        try:
            p = _check(repo)
        finally:
            index.chmod(0o644)
        check_true("a git step that fails: NOT_RUN, naming it, never clean",
                   p.returncode == NOT_RUN and p.stderr.startswith("scratch-refs: git diff exited ")
                   and p.stderr.rstrip().endswith("; nothing was checked") and p.stdout == "")


def test_a_checker_that_crashes_is_broken() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        tools = Path(td) / "tools"
        tools.mkdir()
        for name in ("check.py", "install-hooks.py", "fix-dashes.py"):
            shutil.copy2(ROOT / name, tools / name)
        for package in ("jpcheck", "jpexit", "platformtools"):
            shutil.copytree(ROOT / package, tools / package, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "hooks", tools / "hooks")
        shutil.copytree(ROOT / "configs", tools / "configs")
        (tools / "scratch-refs.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
        _git(tools, "init", "-q")
        _git(tools, "add", "-A")
        _git(tools, "commit", "-q", "-m", "a JP_TOOLS copy whose scratch check exits 3")
        repo = _repo(Path(td) / "r")
        _stage(repo, "a.md", "clean\n")
        r = _git(repo, "commit", "-m", "x", env=dict(os.environ, JP_TOOLS_DIR=str(tools)))
        check_exit("a scratch check that exits 3 refuses the commit as BROKEN, not as clean", r, NONZERO,
                   says=["BROKEN", "scratch-refs.py --hook) exited 3"])


def _run(repo: Path, flag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ROOT / "scratch-refs.py"), flag], cwd=repo,
                          capture_output=True, text=True, check=False)


def test_all_reads_every_tracked_file() -> None:
    """--all is CI's: a commit made past the hook still cannot land."""
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(Path(td), hook=False)
        p = _check(repo)
        check("--check with nothing staged: OK, though a tracked file names scratch",
              (p.returncode, p.stdout), (OK, ""))
        p = _run(repo, "--all")
        check("--all: the committed file listed, FINDINGS", (p.returncode, p.stdout),
              (FINDINGS, f"  old.md:2: ran ~/{WORD}/x.sh\n"))
        (repo / "loose.md").write_text(f"~/{WORD}/y\n", encoding="utf-8")
        check("  a file git does not track is not read",
              _run(repo, "--all").stdout, f"  old.md:2: ran ~/{WORD}/x.sh\n")
        _git(repo, "rm", "-q", "old.md")
        p = _run(repo, "--all")
        check("CONTROL: with no tracked file naming scratch, --all is OK and silent",
              (p.returncode, p.stdout), (OK, ""))
    p = _run(ROOT, "--all")
    check("this repository's own tracked files name no scratch path", (p.returncode, p.stdout), (OK, ""))


def test_hook_prints_the_refusal_around_the_list() -> None:
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(Path(td), hook=False)
        _stage(repo, "a.md", "clean\n")
        p = _run(repo, "--hook")
        check("--hook with nothing found: OK and silent", (p.returncode, p.stdout), (OK, ""))
        _stage(repo, "b.md", f"~/{WORD}/x\n")
        p = _run(repo, "--hook")
        bar = "=====================================\n"
        check("--hook: FINDINGS, the refusal head, the list, the rule, the fix",
              (p.returncode, p.stdout),
              (FINDINGS, "\n" + bar
               + " Pre-commit check FAILED: you fucking idiot, this commit relies on scratch:\n"
               + f"  b.md:1: ~/{WORD}/x\n"
               + " Nothing anyone relies on runs from scratch or reads anything from it\n"
               + " (METHODOLOGY 2.11, rule 6). Commit what these point at, in the\n"
               + " repository, point at that instead, and delete the scratch copy.\n" + bar))


if __name__ == "__main__":
    sys.exit(run(globals(), "scratch-refs"))
