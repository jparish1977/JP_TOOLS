#!/usr/bin/env python
"""
JP_TOOLS/tests/test_install_hooks.py
Tests the generated pre-commit hook against a real git repo.

Builds a throwaway repo that already contains a failing file, installs the
hook, and drives real `git commit` calls through it. No pytest, no
dependencies -- the toolbox has none and this should not add the first.

THE BUG THIS PINS DOWN
    The hook's own comment said it "runs check.py against staged files". It
    collected them into $STAGED, used that list only to decide whether
    anything had been staged at all, and then ran `check.py .` against the
    whole repo.

    On a clean repo the two are indistinguishable, which is why it survived.
    On an inherited one it means the first commit is blocked by every
    pre-existing error in the tree, so the hook is uninstallable and the only
    remaining moves are a cleanup nobody scheduled or --no-verify forever.
    Measured on batocera-watch: 924 pre-existing errors, so every commit.

    Three further defects in the same thirty lines:
      - `python`, which does not exist on most of this fleet.
      - $STAGED split on unquoted $IFS, so any path with a space became two
        paths, neither of which existed, and both were skipped in silence.
      - check.py exits 2 on a file whose language it cannot detect, so a
        commit touching only a README would have been blocked once the loop
        was per-file.

Skips rather than fails when the environment cannot support it:
  - no git         -> skip
  - no ruff        -> skip (check.py cannot produce a failing verdict)

    python tests/test_install_hooks.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL_HOOKS = ROOT / "install-hooks.py"

CLEAN_PY = "x = 1\n"
DIRTY_PY = "import os\n"          # F401, unused import

fails = 0
checks = 0


def check(what: str, ok: bool, detail: str = "") -> None:
    global fails, checks
    checks += 1
    if not ok:
        fails += 1
    print(f"  [{' ok ' if ok else 'FAIL'}] {what}")
    if not ok and detail:
        for line in detail.strip().splitlines():
            print(f"         {line}")


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git in `repo` with signing and user identity pinned."""
    cmd = [
        "git",
        "-c", "user.email=test@example.invalid",
        "-c", "user.name=JP_TOOLS test",
        "-c", "commit.gpgsign=false",
        *args,
    ]
    # check=False: callers assert on returncode, a failing git is the point.
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                          check=False)


def commit_file(repo: Path, name: str, body: str) -> subprocess.CompletedProcess:
    """Write, stage and attempt to commit one file. Returns the commit result."""
    (repo / name).write_text(body, encoding="utf-8")
    add = git(repo, "add", "--", name)
    if add.returncode != 0:
        raise RuntimeError(f"git add failed: {add.stderr}")
    return git(repo, "commit", "-m", f"add {name}")


def build_repo(tmp: Path) -> Path:
    """A repo carrying pre-existing debt, with the hook installed over it."""
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q")

    # Pre-existing failure, committed before the hook exists. This is the
    # inherited-codebase condition the whole section is about.
    (repo / "legacy.py").write_text(DIRTY_PY, encoding="utf-8")
    git(repo, "add", "--", "legacy.py")
    git(repo, "commit", "-q", "-m", "legacy code, predates the gate")

    install = subprocess.run(
        [sys.executable, str(INSTALL_HOOKS), str(repo)],
        capture_output=True, text=True, check=False,
    )
    if install.returncode != 0:
        raise RuntimeError(f"install-hooks.py failed: {install.stderr}")
    return repo


def main() -> int:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return 0
    if not shutil.which("ruff"):
        print("SKIP: ruff not found, check.py cannot fail a file")
        return 0

    print("Generated hook, static:")
    hook_src = INSTALL_HOOKS.read_text(encoding="utf-8")
    check("does not invoke a bare `python`",
          '"$PY" "$CHECK_PY"' in hook_src and 'python "$CHECK_PY"' not in hook_src)
    check("passes --skip-unsupported", "--skip-unsupported" in hook_src)
    check("does not check the repo root",
          '"$CHECK_PY" . ' not in hook_src and '"$CHECK_PY" .\n' not in hook_src)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repo = build_repo(tmp)

        hook = repo / ".git" / "hooks" / "pre-commit"
        check("hook installed", hook.is_file())
        check("hook is executable", os.access(hook, os.X_OK))

        print("\nAgainst a repo that already fails:")

        r = commit_file(repo, "clean.py", CLEAN_PY)
        check("a clean file commits despite pre-existing errors elsewhere",
              r.returncode == 0, r.stdout + r.stderr)
        check("success is announced, not silent",
              "staged file(s) checked" in (r.stdout + r.stderr),
              r.stdout + r.stderr)

        r = commit_file(repo, "broken.py", DIRTY_PY)
        check("a staged file with errors is blocked",
              r.returncode != 0, r.stdout + r.stderr)
        git(repo, "reset", "-q", "HEAD", "--", "broken.py")
        (repo / "broken.py").unlink()

        r = commit_file(repo, "README.md", "# notes\n")
        check("a file with no detectable language does not block",
              r.returncode == 0, r.stdout + r.stderr)

        print("\nPaths with spaces:")

        r = commit_file(repo, "with space.py", CLEAN_PY)
        check("a clean path containing a space commits",
              r.returncode == 0, r.stdout + r.stderr)

        # The one that catches an unquoted $IFS split. A split path does not
        # exist, so `[ -f "$f" ]` skips it and the commit SUCCEEDS: the check
        # is not merely wrong here, it is absent, and absence looks like a pass.
        r = commit_file(repo, "bad space.py", DIRTY_PY)
        check("a failing path containing a space is still blocked",
              r.returncode != 0, r.stdout + r.stderr)

    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
