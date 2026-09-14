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


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
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


def commit_file(repo: Path, name: str, body: str) -> subprocess.CompletedProcess[str]:
    """Write, stage and attempt to commit one file. Returns the commit result."""
    (repo / name).write_text(body, encoding="utf-8")
    add = git(repo, "add", "--", name)
    if add.returncode != 0:
        raise RuntimeError(f"git add failed: {add.stderr}")
    return git(repo, "commit", "-m", f"add {name}")


def build_repo(tmp: Path) -> tuple[Path, str]:
    """A repo carrying pre-existing debt, with the hook installed over it.

    Returns the repo and the installer's stdout, because what the installer
    SAYS it set up is a separate claim from what it wrote, and the two drifted
    apart once already.
    """
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
    return repo, install.stdout


def run_installer(repo: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(INSTALL_HOOKS), *flags, str(repo)],
                          capture_output=True, text=True, check=False)


def symlinked_hook_cases(tmp: Path) -> None:
    """#67: a hook that is a symlink into the repo is never written through.

    projectbook ships hooks/pre-commit as a tracked file and links it into
    .git/hooks. Before the fix, install appended the JP_TOOLS hook INTO that
    tracked file and chmod'ed it; a dangling link had its target created.
    Needs no ruff, so it runs before the ruff skip.
    """
    print("Symlinked hook (#67):")
    repo = tmp / "linked"
    repo.mkdir()
    git(repo, "init", "-q")
    tracked = repo / "hooks" / "pre-commit"
    tracked.parent.mkdir()
    own = "#!/bin/sh\necho repo's own hook\n"
    tracked.write_text(own, encoding="utf-8")
    tracked.chmod(0o644)
    link = repo / ".git" / "hooks" / "pre-commit"
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(Path("..") / ".." / "hooks" / "pre-commit")

    r = run_installer(repo)
    check("install refuses a symlinked hook, exit 2",
          r.returncode == 2, r.stdout + r.stderr)
    # The target as readlink reports it. A bare "hooks/pre-commit" would also
    # match the ordinary install message's path, so it could not fail.
    check("the refusal names the link target",
          "is a symlink to ../../hooks/pre-commit" in r.stdout, r.stdout)
    check("the tracked target is byte for byte unchanged",
          tracked.read_text(encoding="utf-8") == own)
    check("the tracked target's mode is unchanged",
          (tracked.stat().st_mode & 0o777) == 0o644, oct(tracked.stat().st_mode))
    check("the link is still a link", link.is_symlink())

    r = run_installer(repo, "--remove")
    check("--remove refuses a symlinked hook, exit 2",
          r.returncode == 2, r.stdout + r.stderr)
    check("--remove leaves the target unchanged",
          tracked.read_text(encoding="utf-8") == own)

    link.unlink()
    link.symlink_to(Path("..") / ".." / "hooks" / "missing-hook")
    r = run_installer(repo)
    check("install refuses a DANGLING symlinked hook, exit 2",
          r.returncode == 2, r.stdout + r.stderr)
    check("the dangling link's target is not created",
          not (repo / "hooks" / "missing-hook").exists())
    print()


def commit_with_env(repo: Path, name: str, body: str,
                    env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """commit_file, with the hook seeing `env` (JP_TOOLS_DIR, HOME)."""
    (repo / name).write_text(body, encoding="utf-8")
    git(repo, "add", "--", name)
    return subprocess.run(
        ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=JP_TOOLS test",
         "-c", "commit.gpgsign=false", "commit", "-m", f"add {name}"],
        cwd=repo, capture_output=True, text=True, check=False, env=env)


# A stale JP_TOOLS: has a check.py, whose --help lacks the hook's flags, and
# which "passes" anything it is handed. Trusting it is a silent false pass.
STUB_CHECK_PY = (
    "import sys\n"
    "if '--help' in sys.argv:\n"
    "    print('usage: check.py [-h] target')\n"
    "sys.exit(0)\n"
)


def incapable_tools_cases(tmp: Path) -> None:
    """#65: a JP_TOOLS tree that cannot run the hook is REFUSED, not trusted
    and not skipped.

    The stub passes anything it is handed, so trusting it is a silent false
    pass. Skipping it would let a usable tree further down quietly override the
    one that was chosen, so the recorded install-time path here is a usable
    tree (the main checkout, since installing from a worktree records that),
    and the commit must STILL be refused.
    """
    print("\nA JP_TOOLS tree that cannot run the hook (#65):")
    stub = tmp / "stale-jp-tools"
    stub.mkdir()
    (stub / "check.py").write_text(STUB_CHECK_PY, encoding="utf-8")
    home = tmp / "home"
    home.mkdir()
    env = dict(os.environ, JP_TOOLS_DIR=str(stub), HOME=str(home))

    repo, _ = build_repo(tmp)
    r = commit_with_env(repo, "clean.py", CLEAN_PY, env)
    out = r.stdout + r.stderr
    check("a commit is refused when the chosen tree cannot run the hook, even a clean one",
          r.returncode != 0, out)
    check("... as BROKEN and NOTHING WAS CHECKED, not as a failing check",
          "BROKEN" in out and "NOTHING WAS CHECKED" in out
          and "Pre-commit check FAILED" not in out, out)
    check("... naming the tree and the flag it lacks",
          str(stub) in out and "--skip-unsupported" in out, out)
    check("... and not falling through to a usable tree further down",
          "staged file(s) checked" not in out, out)


def append_roundtrip_cases(tmp: Path) -> None:
    """#66: install then --remove on a repo's OWN hook gives it back byte for
    byte, and a second cycle gives the same file.

    Before the fix, install appended the template with its own #!/bin/sh and
    --remove kept every line above the marker, so each cycle left one more
    stray shebang (romtools went from 1 to 2). Needs no ruff.
    """
    print("Appending to a repo's own hook (#66):")
    repo = tmp / "own-hook"
    repo.mkdir()
    git(repo, "init", "-q")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    own = "#!/bin/sh\necho repo's own gate\n"
    hook.write_text(own, encoding="utf-8")
    hook.chmod(0o755)

    r = run_installer(repo)
    installed = hook.read_text(encoding="utf-8")
    check("install appends to the repo's own hook, exit 0", r.returncode == 0,
          r.stdout + r.stderr)
    check("the repo's own hook is still first", installed.startswith(own),
          installed[:200])
    check("the appended section adds no second shebang",
          installed.count("#!/bin/sh") == 1, installed[:400])

    run_installer(repo, "--remove")
    after = hook.read_text(encoding="utf-8")
    check("--remove gives the repo's hook back byte for byte", after == own, after)

    run_installer(repo)
    run_installer(repo, "--remove")
    after = hook.read_text(encoding="utf-8")
    check("a second install/remove cycle gives the same file", after == own, after)
    print()


def main() -> int:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return 0
    with tempfile.TemporaryDirectory() as td:
        symlinked_hook_cases(Path(td))
    with tempfile.TemporaryDirectory() as td:
        append_roundtrip_cases(Path(td))
    if not shutil.which("ruff"):
        print(f"SKIP the rest: ruff not found, check.py cannot fail a file ({checks - fails}/{checks} passed above)")
        return 1 if fails else 0

    print("Generated hook, static:")
    hook_src = INSTALL_HOOKS.read_text(encoding="utf-8")
    check("does not invoke a bare `python`",
          '"$PY" "$CHECK_PY"' in hook_src and 'python "$CHECK_PY"' not in hook_src)
    check("passes --skip-unsupported", "--skip-unsupported" in hook_src)
    check("does not check the repo root",
          '"$CHECK_PY" . ' not in hook_src and '"$CHECK_PY" .\n' not in hook_src)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repo, install_out = build_repo(tmp)

        hook = repo / ".git" / "hooks" / "pre-commit"
        check("hook installed", hook.is_file())
        check("hook is executable", os.access(hook, os.X_OK))
        # The installer announced "python check.py ." for one commit after the
        # hook stopped doing that. Same defect as the original: a description
        # that outlived the behaviour it described.
        check("installer describes what it installed",
              "staged" in install_out and "check.py ." not in install_out,
              install_out)

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

    with tempfile.TemporaryDirectory() as td:
        incapable_tools_cases(Path(td))

    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
