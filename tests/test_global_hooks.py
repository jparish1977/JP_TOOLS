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
from process_check import NONZERO, check_exit

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
        check_exit("install: exit 0, saying where", p, OK, says=["global core.hooksPath set"])
        repo = _new_repo(home, "made-after")
        r = _commit(home, repo, "a.md", "clean\n")
        check_exit("CONTROL: a repository made after the install commits a clean file", r, 0)
        r = _commit(home, repo, "b.md", f"run ~/{WORD}/x.sh\n")
        check_exit("  and refuses one naming a scratch path, saying so", r, NONZERO,
                   says=[f"b.md:1: run ~/{WORD}/x.sh", "you fucking idiot"])
        p = _install(home, "--check")
        check_exit("--check after the install: OK, installed and current", p, OK, says=["installed and current"])
        p = _install(home)
        check_exit("installing again over its own: OK", p, OK)


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
        check_exit("its own pre-commit and commit-msg run, commit-msg with git's argument", r, 0,
                   says=["own-pre-commit ran", "own-commit-msg got .git/COMMIT_EDITMSG"])
        (repo / "refuse").write_text("x", encoding="utf-8")
        r = _commit(home, repo, "b.md", "clean too\n")
        check_exit("  its own pre-commit refusing still refuses", r, NONZERO)
        (repo / "refuse").unlink()
        r = _commit(home, repo, "c.md", "clean\n", msg="nope")
        check_exit("  and its own commit-msg refusing still refuses", r, NONZERO)
        r = _commit(home, repo, "d.md", f"~/{WORD}/x\n")
        check_exit("  and a scratch path is refused before its own hook runs", r, NONZERO,
                   lacks=["own-pre-commit ran"])


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
        check_exit("a scratch check exiting 3: even a clean commit is refused, BROKEN, naming the exit", r,
                   NONZERO, says=["BROKEN", "--hook) exited 3"])


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
        handoff = _new_repo(home, "handoff-local")
        (handoff / "hooks").mkdir()
        (handoff / "hooks" / "pre-commit").write_text(
            '#!/bin/sh\nsh "$HOME/.config/git/jp-tools-hooks/pre-commit" "$@" || exit 1\n', encoding="utf-8")
        (handoff / "hooks" / "pre-commit").chmod(0o755)
        _git(home, handoff, "config", "--local", "core.hooksPath", "hooks")
        r = _commit(home, handoff, "a.md", "clean\n")
        check_exit("before any install, the hand-off fails closed: even a clean commit is refused", r, NONZERO)
        _git(home, handoff, "reset", "-q")
        _new_repo(home, "plain")
        p = _install(home)
        check_exit("a local core.hooksPath whose pre-commit skips the check: FINDINGS, NOT COVERED, named", p,
                   FINDINGS, says=["NOT COVERED: ", "bare-local"])
        check_true("  with the hand-off line to add",
                   'sh "$HOME/.config/git/jp-tools-hooks/pre-commit" "$@" || exit 1' in p.stdout)
        checked = home / "projects" / "checked-local"
        check_true("  one whose pre-commit runs it: covered, and only the one not covered is NOT COVERED",
                   f"\n  covered: {checked} " in "\n" + p.stdout and p.stdout.count("NOT COVERED: ") == 1)
        check_true("  one whose pre-commit hands to the global one: covered",
                   f"covered: {handoff} " in p.stdout)
        check_true("  and a plain repository is not listed at all", "plain" not in p.stdout)
        r = _commit(home, handoff, "a.md", "clean\n")
        check_exit("the hand-off after the install: a clean commit goes through", r, 0)
        r = _commit(home, handoff, "b.md", f"~/{WORD}/x\n")
        check_exit("  and one naming a scratch path is refused", r, NONZERO, says=["you fucking idiot"])


def test_another_global_hooks_path_is_refused() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        p = _install(home, "--check")
        check_exit("--check before any install: FINDINGS, not installed", p, FINDINGS, says=["NOT INSTALLED"])
        subprocess.run(["git", "config", "--global", "core.hooksPath", "/elsewhere"], env=_env(home), check=True)
        p = _install(home)
        check_exit("another global core.hooksPath: NOT_RUN, refused, naming it", p, NOT_RUN,
                   says=["already /elsewhere: refused"])
        got = subprocess.run(["git", "config", "--global", "--get", "core.hooksPath"], env=_env(home),
                             capture_output=True, text=True, check=False).stdout.strip()
        check("  and it is left as it was", got, "/elsewhere")


if __name__ == "__main__":
    sys.exit(run(globals(), "global hooks"))
