#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_gate_prepare.py
mutmut-gate.py gets mutants/ ready, and names a failed git.

    - prepare makes mutants/ a worktree of HEAD whose index lists the tracked
      files while none is checked out (a file whose checkout fails is no
      error), links node_modules/ and vendor/ to ROOT's own (none for one ROOT
      lacks), follows a new HEAD (moving a HEAD pointed at a branch, never the
      branch), makes no branch, leaves a right worktree alone, and rebuilds
      the link when the state came from elsewhere.
    - _is_worktree_of answers for the repository, from any cwd.
    - A failed git is a GateError naming the command and git's error.

    python3 tests/test_mutmut_gate_prepare.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, run
from mutmut_gate_support import ENV as _ENV
from mutmut_gate_support import commit as _commit
from mutmut_gate_support import gate
from mutmut_gate_support import head as _head
from mutmut_gate_support import ls as _ls
from mutmut_gate_support import repo as _repo


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


def _branches(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "for-each-ref", "--format=%(refname) %(objectname)",
                           "refs/heads"], capture_output=True, text=True, check=True).stdout


def test_prepare_keeps_what_it_need_not_rebuild() -> None:
    """A worktree that is already right is left alone (a wrong 'not a
    worktree' would rebuild it every run); no branch is made; the installs
    are linked to ROOT's own and only where ROOT has them."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        before = _branches(root)
        mutants = gate.prepare(root)
        check("prepare makes no branch: ROOT's are as they were", _branches(root), before)
        link = (mutants / ".git").stat()
        gate.prepare(root)
        again = (mutants / ".git").stat()
        check("a second prepare leaves the worktree's .git link as it was, not rebuilt",
              (again.st_ino, again.st_mtime_ns), (link.st_ino, link.st_mtime_ns))
        check("the links lead to ROOT's own installs",
              [(mutants / n).resolve() for n in ("node_modules", "vendor")],
              [(root / n).resolve() for n in ("node_modules", "vendor")])
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        (root / "vendor").rmdir()
        mutants = gate.prepare(root)
        check("no link for an install ROOT does not have",
              ((mutants / "vendor").is_symlink(), (mutants / "node_modules").is_symlink()), (False, True))


def test_prepare_never_checks_files_out() -> None:
    """mutmut writes every file in mutants/, so prepare checks none out: a
    file whose checkout fails (a smudge filter that fails) is no error."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        for k, v in (("filter.boom.clean", "cat"), ("filter.boom.smudge", "false"),
                     ("filter.boom.required", "true")):
            subprocess.run(["git", "-C", str(root), "config", k, v], check=True, env=_ENV)
        _commit(root, {".gitattributes": "a.py filter=boom\n"}, "a.py cannot be checked out")
        control = subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", str(Path(td) / "co"), "HEAD"],
                                 capture_output=True, text=True, env=_ENV, check=False)
        check_true("CONTROL: checking a.py out fails", control.returncode != 0)
        mutants = gate.prepare(root)
        check("prepare does not: mutants/ lists both files, none checked out",
              (_ls(mutants), sorted(p.name for p in mutants.iterdir() if p.name != ".git")),
              (".gitattributes\na.py\n", ["node_modules", "vendor"]))


def test_prepare_moves_head_not_a_branch() -> None:
    """A mutants/ HEAD someone pointed at a branch: prepare moves HEAD to
    ROOT's new commit and leaves the branch where it was."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        first = _head(root)
        subprocess.run(["git", "-C", str(root), "branch", "side"], check=True, env=_ENV)
        subprocess.run(["git", "-C", str(mutants), "symbolic-ref", "HEAD", "refs/heads/side"], check=True, env=_ENV)
        second = _commit(root, {"a.py": "x = 2\n"}, "two")
        gate.prepare(root)
        side = subprocess.run(["git", "-C", str(root), "rev-parse", "side"], capture_output=True, text=True,
                              check=True).stdout.strip()
        check("HEAD in mutants/ is ROOT's new commit, and the branch did not move",
              (_head(mutants), side), (second, first))


def test_a_failed_git_is_named_with_its_error() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    nowhere = "/nonexistent/" + "d" * 400  # git's error quotes the path: longer than 300
    args = ("-C", nowhere, "status")
    said = subprocess.run(["git", *args], capture_output=True, text=True, check=False).stderr.strip()
    check_true("CONTROL: git's own error is longer than the 300 characters kept", len(said) > 300)
    try:
        gate._git(*args)
        raised = ""
    except gate.GateError as e:
        raised = str(e)
    check("a GateError naming the command and the first 300 characters of git's error",
          raised, f"git -C {nowhere} status: {said[:300]}")


def test_is_worktree_of() -> None:
    """Asked directly: prepare() rebuilds whenever this says no, so a wrong
    "no" shows through prepare only as a rebuild (see
    test_prepare_keeps_what_it_need_not_rebuild)."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        check("a worktree of ROOT's repository: yes", gate._is_worktree_of(mutants, root), True)
        check("  asked from either end's path as given, relative to another cwd",
              gate._is_worktree_of(Path(os.path.relpath(mutants)), Path(os.path.relpath(root))), True)
        check("ROOT itself (a .git directory, not a link): no", gate._is_worktree_of(root, root), False)
        plain = Path(td) / "plain"
        plain.mkdir()
        check("a directory with no .git: no", gate._is_worktree_of(plain, root), False)
        (Path(td) / "elsewhere").mkdir()
        other = _repo(Path(td) / "elsewhere")
        check("a worktree of ROOT asked about another repository: no",
              gate._is_worktree_of(mutants, other), False)
        (mutants / ".git").write_text("gitdir: /nonexistent/worktrees/mutants\n", encoding="utf-8")
        check("a .git link git cannot follow: no, not an error", gate._is_worktree_of(mutants, root), False)


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut gate prepare"))
