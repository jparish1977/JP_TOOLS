#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_global_hooks.py
install-global-hooks.py and hooks/global-dispatch.sh: every repository on a
box behind the scratch commit guard, its own hooks still run.

Pins: a repository with no hook of its own, made after the install, refuses
a commit naming a scratch path and lets a clean one through; a repository's
own pre-commit and commit-msg still run, with their arguments, and their
refusal still refuses; a scratch check that cannot run refuses every commit;
a repository with a local core.hooksPath is named NOT COVERED unless its own
pre-commit runs the check; another global core.hooksPath is refused, not
replaced; --check tells installed from not.

Every run has HOME pointed at a throwaway directory and the system config
off, so the box's own git configuration is never read or written.

    python3 tests/test_global_hooks.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, run
from jpexit import FINDINGS, NOT_RUN, OK

WORD = "scr" + "atch"
INSTALL = ROOT / "install-global-hooks.py"


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "XDG_CONFIG_HOME"}
    return dict(env, HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
                GIT_COMMITTER_EMAIL="t@example.invalid")


def _install(home: Path, *args: str, tools: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(tools / "install-global-hooks.py"), *args],
                          capture_output=True, text=True, check=False, env=_env(home))


def _git(home: Path, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, capture_output=True,
                          text=True, check=False, env=_env(home))


def _new_repo(home: Path, name: str) -> Path:
    repo = home / "projects" / name
    repo.mkdir(parents=True)
    _git(home, repo, "init", "-q")
    return repo


def _commit(home: Path, repo: Path, name: str, text: str, msg: str = "m") -> subprocess.CompletedProcess[str]:
    (repo / name).write_text(text, encoding="utf-8")
    _git(home, repo, "add", name)
    return _git(home, repo, "commit", "-m", msg)


def test_every_repository_is_behind_the_guard() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "projects").mkdir()
        p = _install(home)
        check("install: exit 0, saying where", (p.returncode, "global core.hooksPath set" in p.stdout), (OK, True))
        repo = _new_repo(home, "made-after")
        r = _commit(home, repo, "a.md", "clean\n")
        check("CONTROL: a repository made after the install commits a clean file", r.returncode, 0)
        r = _commit(home, repo, "b.md", f"run ~/{WORD}/x.sh\n")
        out = r.stdout + r.stderr
        check_true("  and refuses one naming a scratch path, saying so",
                   r.returncode != 0 and f"b.md:1: run ~/{WORD}/x.sh" in out and "you fucking idiot" in out)
        p = _install(home, "--check")
        check("--check after the install: OK, installed and current",
              (p.returncode, "installed and current" in p.stdout), (OK, True))
        p = _install(home)
        check("installing again over its own: OK", p.returncode, OK)


def test_a_repository_s_own_hooks_still_run() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "projects").mkdir()
        repo = _new_repo(home, "own")
        hooks = repo / ".git" / "hooks"
        (hooks / "pre-commit").write_text("#!/bin/sh\necho own-pre-commit ran >&2\n"
                                          "[ -e refuse ] && exit 1\nexit 0\n", encoding="utf-8")
        (hooks / "commit-msg").write_text('#!/bin/sh\necho "own-commit-msg got $1" >&2\n'
                                          'grep -q nope "$1" && exit 1\nexit 0\n', encoding="utf-8")
        for h in ("pre-commit", "commit-msg"):
            (hooks / h).chmod(0o755)
        _install(home)
        r = _commit(home, repo, "a.md", "clean\n")
        out = r.stdout + r.stderr
        check_true("its own pre-commit and commit-msg run, commit-msg with git's argument",
                   r.returncode == 0 and "own-pre-commit ran" in out
                   and "own-commit-msg got .git/COMMIT_EDITMSG" in out)
        (repo / "refuse").write_text("x", encoding="utf-8")
        r = _commit(home, repo, "b.md", "clean too\n")
        check("  its own pre-commit refusing still refuses", r.returncode != 0, True)
        (repo / "refuse").unlink()
        r = _commit(home, repo, "c.md", "clean\n", msg="nope")
        check("  and its own commit-msg refusing still refuses", r.returncode != 0, True)
        r = _commit(home, repo, "d.md", f"~/{WORD}/x\n")
        check_true("  and a scratch path is refused before its own hook runs",
                   r.returncode != 0 and "own-pre-commit ran" not in r.stdout + r.stderr)


def test_a_check_that_cannot_run_refuses_every_commit() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "projects").mkdir()
        tools = home / "tools"
        (tools / "hooks").mkdir(parents=True)
        for name in ("install-global-hooks.py",):
            shutil.copy2(ROOT / name, tools / name)
        shutil.copy2(ROOT / "hooks" / "global-dispatch.sh", tools / "hooks" / "global-dispatch.sh")
        for package in ("jpexit", "platformtools"):
            shutil.copytree(ROOT / package, tools / package, ignore=shutil.ignore_patterns("__pycache__"))
        (tools / "scratch-refs.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
        _install(home, tools=tools)
        repo = _new_repo(home, "r")
        r = _commit(home, repo, "a.md", "clean\n")
        out = r.stdout + r.stderr
        check_true("a scratch check exiting 3: even a clean commit is refused, BROKEN, naming the exit",
                   r.returncode != 0 and "BROKEN" in out and "--hook) exited 3" in out)


def test_what_the_global_hooks_cannot_reach_is_named() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "projects").mkdir()
        for name, runs_check in (("bare-local", False), ("checked-local", True)):
            repo = _new_repo(home, name)
            (repo / "hooks").mkdir()
            body = "#!/bin/sh\n" + ("python3 /x/scratch-refs.py --hook\n" if runs_check else "exit 0\n")
            (repo / "hooks" / "pre-commit").write_text(body, encoding="utf-8")
            _git(home, repo, "config", "--local", "core.hooksPath", "hooks")
        _new_repo(home, "plain")
        p = _install(home)
        check_true("a local core.hooksPath whose pre-commit skips the check: FINDINGS, NOT COVERED, named",
                   p.returncode == FINDINGS and "NOT COVERED: " in p.stdout and "bare-local" in p.stdout)
        check_true("  one whose pre-commit runs it: covered", "covered: " in p.stdout
                   and "checked-local" in p.stdout)
        check_true("  and a plain repository is not listed at all", "plain" not in p.stdout)


def test_another_global_hooks_path_is_refused() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        p = _install(home, "--check")
        check("--check before any install: FINDINGS, not installed",
              (p.returncode, "NOT INSTALLED" in p.stdout), (FINDINGS, True))
        subprocess.run(["git", "config", "--global", "core.hooksPath", "/elsewhere"], env=_env(home), check=True)
        p = _install(home)
        check_true("another global core.hooksPath: NOT_RUN, refused, naming it",
                   p.returncode == NOT_RUN and "already /elsewhere: refused" in p.stdout)
        got = subprocess.run(["git", "config", "--global", "--get", "core.hooksPath"], env=_env(home),
                             capture_output=True, text=True, check=False).stdout.strip()
        check("  and it is left as it was", got, "/elsewhere")


if __name__ == "__main__":
    sys.exit(run(globals(), "global hooks"))
