#!/usr/bin/env python
"""
JP_TOOLS/tests/test_install_hooks.py
Tests the generated pre-commit hook against a real git repo.

Builds a throwaway repo that already contains a failing file, installs the
hook, and drives real `git commit` calls through it. No pytest, no
dependencies -- the toolbox has none and this should not add the first.

WHAT THIS PINS DOWN
    The hook checks the STAGED FILES, one at a time, not the whole repo. On a
    clean repo the two are indistinguishable. On an inherited one a whole-repo
    check blocks the first commit on every pre-existing error in the tree, so
    the hook is uninstallable and the only remaining moves are a cleanup
    nobody scheduled or --no-verify forever.

    Also pinned:
      - it runs `python3` where there is no bare `python`;
      - a path with a space is one path, not two that do not exist and are
        skipped in silence;
      - a file whose language check.py cannot detect does not block a commit,
        and is not counted as checked: the verdict says how many of the
        staged files were, and why the others were skipped;
      - the commit-msg hook refuses a message linking a Claude Code session,
        anywhere in it, in any case, naming the line, and passes one that
        only mentions the site; install adds it beside an existing
        pre-commit hook, --remove takes both, --status calls a repo without
        it STALE, and a symlinked commit-msg hook is refused before anything
        is written;
      - a commit in a worktree of the JP_TOOLS repository the hook runs from
        is linted by that worktree's own check.py, and the hook says so; the
        main checkout and any other repository, by TOOLS_DIR's.

Skips rather than fails when the environment cannot support it:
  - no git         -> skip
  - no ruff        -> skip (check.py cannot produce a failing verdict)

    python tests/test_install_hooks.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT

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
    SAYS it set up is a separate claim from what it wrote.
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
    """A hook that is a symlink into the repo is never written through.

    A repo that ships hooks/pre-commit as a tracked file and links it into
    .git/hooks must not get the JP_TOOLS hook appended INTO that tracked file,
    nor a dangling link's target created. Needs no ruff, so it runs before the
    ruff skip.
    """
    print("Symlinked hook:")
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

    # A symlinked commit-msg hook beside no pre-commit hook: refused before
    # the pre-commit shim is written, so a refused repo is left as it was.
    repo = tmp / "linked-msg"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "hooks").mkdir()
    (repo / "hooks" / "commit-msg").write_text("#!/bin/sh\n", encoding="utf-8")
    msg_link = repo / ".git" / "hooks" / "commit-msg"
    msg_link.parent.mkdir(exist_ok=True)
    msg_link.symlink_to(Path("..") / ".." / "hooks" / "commit-msg")
    r = run_installer(repo)
    check("a symlinked commit-msg hook is refused, exit 2, and no pre-commit hook written",
          r.returncode == 2 and not (repo / ".git" / "hooks" / "pre-commit").exists(),
          r.stdout + r.stderr)
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
    """A JP_TOOLS tree that cannot run the hook is REFUSED, not trusted
    and not skipped.

    The stub passes anything it is handed, so trusting it is a silent false
    pass. Skipping it would let a usable tree further down quietly override the
    one that was chosen, so the recorded install-time path here is a usable
    tree (the main checkout, since installing from a worktree records that),
    and the commit must STILL be refused.
    """
    print("\nA JP_TOOLS tree that cannot run the hook:")
    stub = tmp / "stale-jp-tools"
    stub.mkdir()
    (stub / "check.py").write_text(STUB_CHECK_PY, encoding="utf-8")
    # The template is current, so what is refused is the stale check.py (the
    # flag check in hooks/pre-commit.sh), not a too-old tree.
    (stub / "hooks").mkdir()
    shutil.copy2(ROOT / "hooks" / "pre-commit.sh", stub / "hooks" / "pre-commit.sh")
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
    """Install then --remove on a repo's OWN hook gives it back byte for
    byte, and a second cycle gives the same file: no stray shebang accumulates.
    Needs no ruff.
    """
    print("Appending to a repo's own hook:")
    # The last two shapes are the ones a line-based rewrite would change.
    shapes = {
        "sh, one final newline": "#!/bin/sh\necho repo's own gate\n",
        "bash, one final newline": "#!/usr/bin/env bash\necho repo's own gate\n",
        "no final newline": "#!/bin/sh\necho repo's own gate",
        "a trailing blank line": "#!/bin/sh\necho repo's own gate\n\n",
    }
    for i, (label, own) in enumerate(shapes.items()):
        repo = tmp / f"own-hook-{i}"
        repo.mkdir()
        git(repo, "init", "-q")
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(own, encoding="utf-8")
        hook.chmod(0o755)

        r = run_installer(repo)
        installed = hook.read_text(encoding="utf-8")
        check(f"[{label}] install appends, exit 0, the repo's hook still first",
              r.returncode == 0 and installed.startswith(own), r.stdout + r.stderr)
        check(f"[{label}] the appended section adds no second shebang",
              installed.count("#!") == 1, installed[:400])
        run_installer(repo, "--remove")
        after = hook.read_text(encoding="utf-8")
        check(f"[{label}] --remove gives the hook back byte for byte", after == own,
              repr(after))
        run_installer(repo)
        run_installer(repo, "--remove")
        after = hook.read_text(encoding="utf-8")
        check(f"[{label}] a second cycle gives the same file", after == own, repr(after))

    # A hook from an older install: the old template, shebang and all, appended.
    # --remove must still give back the repo's hook, without the stray shebang.
    repo = tmp / "old-install"
    repo.mkdir()
    git(repo, "init", "-q")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    own = "#!/bin/sh\necho repo's own gate\n"
    hook.write_text(own + "\n#!/bin/sh\n# JP_TOOLS pre-commit hook\necho old section\n",
                    encoding="utf-8")
    run_installer(repo, "--remove")
    after = hook.read_text(encoding="utf-8")
    check("[older install] --remove strips the old stray shebang too", after == own,
          repr(after))

    # The repo's own commit-msg hook: appended to, and given back the same way.
    repo = tmp / "own-msg-hook"
    repo.mkdir()
    git(repo, "init", "-q")
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.parent.mkdir(exist_ok=True)
    own = "#!/bin/sh\necho repo's own message check\n"
    hook.write_text(own, encoding="utf-8")
    run_installer(repo)
    installed = hook.read_text(encoding="utf-8")
    check("[own commit-msg] install appends after it, one shebang",
          installed.startswith(own) and installed.count("#!") == 1, installed[:400])
    run_installer(repo, "--remove")
    check("[own commit-msg] --remove gives it back byte for byte, and takes the pre-commit hook",
          hook.read_text(encoding="utf-8") == own
          and not (repo / ".git" / "hooks" / "pre-commit").exists())
    print()


def status_cases(tmp: Path) -> None:
    """--status says which installed hooks are stale. Needs no ruff.

    Five repos under one root, one of each state.
    """
    print("Installed-hook status:")
    root = tmp / "fleet"
    root.mkdir()

    def make(name: str) -> Path:
        r = root / name
        r.mkdir()
        git(r, "init", "-q")
        (r / ".git" / "hooks").mkdir(exist_ok=True)
        return r

    run_installer(make("current"))
    (make("old") / ".git" / "hooks" / "pre-commit").write_text(
        "#!/bin/sh\n# JP_TOOLS pre-commit hook\nTOOLS_DIR=/somewhere/JP_TOOLS\n",
        encoding="utf-8")
    edited = make("edited")
    run_installer(edited)
    h = edited / ".git" / "hooks" / "pre-commit"
    h.write_text(h.read_text(encoding="utf-8").replace("exec sh", "exec  sh", 1),
                 encoding="utf-8")
    make("none")
    pre_only = make("pre-only")
    run_installer(pre_only)
    (pre_only / ".git" / "hooks" / "commit-msg").unlink()
    linked = make("linked")
    (linked / "hooks").mkdir()
    (linked / "hooks" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    (linked / ".git" / "hooks" / "pre-commit").symlink_to(Path("..") / ".." / "hooks" / "pre-commit")

    r = run_installer(root, "--status")
    states = {Path(line.split()[1]).name: line for line in r.stdout.splitlines()
              if len(line.split()) > 1 and line.split()[0].isupper()}
    check("a root with a stale hook exits 1", r.returncode == 1, r.stdout + r.stderr)
    check("a fresh install reads CURRENT", states.get("current", "").startswith("CURRENT"),
          r.stdout)
    check("a hook with no run-time lookup reads STALE, saying why",
          states.get("old", "").startswith("STALE") and "older install-hooks.py" in states.get("old", ""),
          r.stdout)
    check("an edited install reads STALE, as differing from the template",
          states.get("edited", "").startswith("STALE") and "differs" in states.get("edited", ""),
          r.stdout)
    check("no hook reads NONE, a symlinked one SYMLINK",
          states.get("none", "").startswith("NONE")
          and states.get("linked", "").startswith("SYMLINK"), r.stdout)
    check("a current pre-commit hook with no commit-msg hook reads STALE, saying so",
          states.get("pre-only", "").startswith("STALE") and "no commit-msg hook" in states.get("pre-only", ""),
          r.stdout)
    check("the summary counts 6 repos and 3 STALE", "6 repos, 3 STALE" in r.stdout, r.stdout)

    r = run_installer(root / "current", "--status")
    check("a root holding only a current hook exits 0", r.returncode == 0, r.stdout + r.stderr)
    run_installer(pre_only)
    r = run_installer(pre_only, "--status")
    check("a plain install adds the missing commit-msg hook: CURRENT again",
          r.returncode == 0 and "CURRENT" in r.stdout, r.stdout + r.stderr)
    print()


SESSION_LINK = "https://claude.ai/code/session_01ExampleOnlyNotARealId"


def commit_msg_cases(tmp: Path) -> None:
    """The commit-msg hook refuses a message linking a Claude Code session,
    wherever it is and in any case, and names the line; a message that only
    mentions the site passes."""
    print("\nThe commit-msg hook:")
    repo, _ = build_repo(tmp)

    def commit(name: str, message: str) -> subprocess.CompletedProcess[str]:
        (repo / name).write_text("# n\n", encoding="utf-8")
        git(repo, "add", "--", name)
        return git(repo, "commit", "-m", message)

    r = commit("a.md", "add notes\n\nsee claude.ai for the docs")
    check("CONTROL: a message that only mentions the site commits",
          r.returncode == 0, r.stdout + r.stderr)
    for label, message in (("in the subject", f"add notes {SESSION_LINK}"),
                           ("in the body", f"add notes\n\nmore\n\n{SESSION_LINK}"),
                           ("in another case", "add notes\n\n" + SESSION_LINK.upper())):
        r = commit("b.md", message)
        out = r.stdout + r.stderr
        check(f"a session link {label} is refused, naming the line",
              r.returncode != 0 and "commit REFUSED" in out and "session_" in out.lower()
              and "BROKEN" not in out, out)
    count = git(repo, "rev-list", "--count", "HEAD").stdout.strip()
    check("... and none of those commits was made", count == "2", count)

    # `git commit -v` puts the staged diff below the scissors line, and git
    # cuts it off before saving: a link there is in a FILE, not the message.
    # The editor stands in for a person typing SUBJECT above what git wrote.
    def commit_verbose(subject: str) -> subprocess.CompletedProcess[str]:
        # The subject in the file too, so each call stages a change.
        (repo / "c.md").write_text(f"{SESSION_LINK} {subject}\n", encoding="utf-8")
        git(repo, "add", "--", "c.md")
        editor = f"sh -c 'printf \"%s\\n\" \"{subject}\" | cat - \"$1\" > \"$1.t\" && mv \"$1.t\" \"$1\"' -"
        return subprocess.run(
            ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=JP_TOOLS test",
             "-c", "commit.gpgsign=false", "commit", "-v"],
            cwd=repo, capture_output=True, text=True, check=False,
            env=dict(os.environ, GIT_EDITOR=editor))

    r = commit_verbose("edit notes")
    check("git commit -v: a link only in the staged diff, below the scissors line, commits",
          r.returncode == 0, r.stdout + r.stderr)
    r = commit_verbose(f"edit notes {SESSION_LINK}")
    check("git commit -v: a link in the message above the scissors line is still refused",
          r.returncode != 0 and "commit REFUSED" in r.stdout + r.stderr, r.stdout + r.stderr)


def fake_jp_tools(at: Path, with_template: bool = True, git_repo: bool = True) -> Path:
    """A JP_TOOLS tree from this checkout's working files: a git repo on master
    whose origin/master is set, locally, to its first commit. With
    git_repo=False, a plain copy with no .git at all."""
    at.mkdir(parents=True)
    shutil.copy2(ROOT / "check.py", at / "check.py")
    # platformtools: fix-dashes.py names its process through it.
    for package in ("jpcheck", "jpexit", "platformtools"):
        shutil.copytree(ROOT / package, at / package,
                        ignore=shutil.ignore_patterns("__pycache__"))
    # The template runs these before check.py and fails closed without them, so
    # a tree missing one would read as BROKEN rather than as the case under test.
    for script in ("fix-dashes.py", "scratch-refs.py"):
        shutil.copy2(ROOT / script, at / script)
    shutil.copytree(ROOT / "configs", at / "configs")
    if with_template:
        (at / "hooks").mkdir()
        for name in ("pre-commit.sh", "commit-msg.sh"):
            shutil.copy2(ROOT / "hooks" / name, at / "hooks" / name)
    if not git_repo:
        return at
    git(at, "init", "-q", "-b", "master")
    git(at, "add", "-A")
    git(at, "commit", "-q", "-m", "fake JP_TOOLS")
    git(at, "update-ref", "refs/remotes/origin/master", "HEAD")
    return at


def shim_cases(tmp: Path) -> None:
    """The shim's acceptance cases: a feature-branch tree found by the search,
    and a shim from an older interface, each print BROKEN, with master as the
    control. Plus: an explicit JP_TOOLS_DIR runs on any branch and says so; a
    tree with no template and a named tree that is not there both refuse,
    never falling through to another candidate.
    """
    print("\nThe shim:")
    home = tmp / "home"
    tree = fake_jp_tools(home / "projects" / "JP_TOOLS")
    env = {k: v for k, v in os.environ.items() if k != "JP_TOOLS_DIR"}
    env["HOME"] = str(home)
    repo, _ = build_repo(tmp)
    hook = repo / ".git" / "hooks" / "pre-commit"
    text = hook.read_text(encoding="utf-8")
    check("the installed hook is the shim, not a copy of the template",
          "exec sh" in text and "--skip-unsupported" not in text)
    _shim_search(tmp, tree, env, repo)
    _shim_refusals(tmp, env, repo)
    git(tree, "checkout", "-q", "master")
    hook.write_text(hook.read_text(encoding="utf-8").replace(
        "JP_TOOLS_HOOK_API=1", "JP_TOOLS_HOOK_API=0"), encoding="utf-8")
    r = commit_with_env(repo, "c6.py", CLEAN_PY, env)
    out = r.stdout + r.stderr
    check("a shim from an older interface is refused against a newer template",
          r.returncode != 0 and "BROKEN" in out and "interface" in out, out)


def _ran(r: subprocess.CompletedProcess[str]) -> bool:
    """The commit went through the checks and succeeded."""
    out = r.stdout + r.stderr
    return r.returncode == 0 and "staged file(s) checked" in out and "BROKEN" not in out


def _shim_search(tmp: Path, tree: Path, env: dict[str, str], repo: Path) -> None:
    """The tree found by the search: used at master, refused on a feature branch."""
    r = commit_with_env(repo, "c1.py", CLEAN_PY, env)
    check("CONTROL: a tree found by the search, at its origin/master, runs the checks",
          _ran(r), r.stdout + r.stderr)

    # In a linked worktree git runs the hook with GIT_DIR set to that
    # worktree's gitdir, which outranks `git -C` on the JP_TOOLS tree, so a
    # plain read gets the committing repo's HEAD and master. A commit in the
    # main checkout moves master past the worktree's HEAD, so those two differ
    # there, as they do in any repo whose worktree is behind.
    wt = tmp / "repo-worktree"
    git(repo, "worktree", "add", "-q", "-b", "wt", str(wt))
    commit_with_env(repo, "w0.py", CLEAN_PY, env)
    r = commit_with_env(wt, "w1.py", CLEAN_PY, env)
    check("a commit from a LINKED WORKTREE finds the same tree at master and runs the checks",
          _ran(r), r.stdout + r.stderr)

    git(tree, "checkout", "-q", "-b", "feature/x")
    (tree / "x.txt").write_text("x\n", encoding="utf-8")
    git(tree, "add", "x.txt")
    git(tree, "commit", "-q", "-m", "feature work")
    r = commit_with_env(repo, "c2.py", CLEAN_PY, env)
    out = r.stdout + r.stderr
    check("a feature-branch tree found by the search is refused: BROKEN, naming the branch",
          r.returncode != 0 and "BROKEN" in out and "feature/x" in out
          and "staged file(s) checked" not in out, out)
    check("... and the refusal prints the command that clears it", "pull --ff-only" in out, out)

    r = commit_with_env(repo, "c3.py", CLEAN_PY, dict(env, JP_TOOLS_DIR=str(tree)))
    out = r.stdout + r.stderr
    check("an EXPLICIT JP_TOOLS_DIR on that feature branch runs, and announces itself",
          r.returncode == 0 and "JP_TOOLS_DIR is set; running" in out and "feature/x" in out, out)


def _shim_refusals(tmp: Path, env: dict[str, str], repo: Path) -> None:
    """Trees the shim must refuse, never falling through to another candidate."""
    old = fake_jp_tools(tmp / "old-jp-tools", with_template=False)
    r = commit_with_env(repo, "c4.py", CLEAN_PY, dict(env, JP_TOOLS_DIR=str(old)))
    out = r.stdout + r.stderr
    check("a tree with no hooks/pre-commit.sh is refused as too old",
          r.returncode != 0 and "too old" in out, out)

    r = commit_with_env(repo, "c5.py", CLEAN_PY, dict(env, JP_TOOLS_DIR=str(tmp / "nowhere")))
    out = r.stdout + r.stderr
    check("a named JP_TOOLS_DIR that is not there is refused, not passed over",
          r.returncode != 0 and "has no check.py" in out and "staged file(s) checked" not in out,
          out)

    # A copy with no .git must be refused with a Fix that can work there, not
    # "on ? ?" and "checkout master".
    home2 = tmp / "home2"
    copy = fake_jp_tools(home2 / "projects" / "JP_TOOLS", git_repo=False)
    env2 = dict(env, HOME=str(home2))
    r = commit_with_env(repo, "c7.py", CLEAN_PY, env2)
    out = r.stdout + r.stderr
    check("a JP_TOOLS copy with no .git, found by the search, is refused as not a git checkout",
          r.returncode != 0 and "not a git checkout" in out and "on ? ?" not in out, out)
    check("... with a Fix that can work there (name it with JP_TOOLS_DIR)",
          "export JP_TOOLS_DIR=" in out, out)
    r = commit_with_env(repo, "c8.py", CLEAN_PY, dict(env2, JP_TOOLS_DIR=str(copy)))
    out = r.stdout + r.stderr
    check("... and naming that copy on purpose runs it, saying it is not a git checkout",
          r.returncode == 0 and "(not a git checkout)" in out, out)


# A branch's check.py, told from master's by what it prints: it has the flags
# the hook asks for, and fails every file it is handed, naming itself.
BRANCH_CHECK_PY = (
    "import sys\n"
    "\n"
    "if '--help' in sys.argv:\n"
    "    print('usage: check.py [--skip-unsupported] [--baseline FILE] [--touched] target')\n"
    "    sys.exit(0)\n"
    "print('the branch check.py ran')\n"
    "sys.exit(1)\n"
)


def own_worktree_cases(tmp: Path) -> None:
    """A commit in JP_TOOLS itself, from a worktree on a branch, is linted by
    that worktree's own check.py; one in another repository, by TOOLS_DIR's."""
    print("\nA commit in a JP_TOOLS worktree:")
    tree = fake_jp_tools(tmp / "JP_TOOLS")
    env = dict(os.environ, JP_TOOLS_DIR=str(tree), HOME=str(tmp))
    install = run_installer(tree)
    check("CONTROL: the hook installs into the fake JP_TOOLS", install.returncode == 0,
          install.stdout + install.stderr)
    wt = tmp / "JP_TOOLS.worktrees" / "branch"
    git(tree, "worktree", "add", "-q", "-b", "branch", str(wt))
    (wt / "check.py").write_text(BRANCH_CHECK_PY, encoding="utf-8")
    git(wt, "add", "check.py")
    r = commit_with_env(wt, "w.py", CLEAN_PY, env)
    out = r.stdout + r.stderr
    check("the worktree's own check.py lints it, and the hook says which",
          r.returncode != 0 and "the branch check.py ran" in out
          and f"linting with this worktree's check.py ({wt.resolve()})" in out, out)
    git(wt, "reset", "-q", "--hard", "HEAD")
    r = commit_with_env(tree, "m.py", CLEAN_PY, env)
    out = r.stdout + r.stderr
    check("CONTROL: the main checkout keeps TOOLS_DIR's check.py, and passes",
          _ran(r) and "the branch check.py ran" not in out, out)
    check("  and, being TOOLS_DIR itself, says nothing of a worktree",
          "linting with this worktree" not in out, out)
    repo, _ = build_repo(tmp)
    (repo / "check.py").write_text(BRANCH_CHECK_PY, encoding="utf-8")  # a check.py, but not JP_TOOLS'
    git(repo, "add", "check.py")
    r = commit_with_env(repo, "o.py", CLEAN_PY, env)
    out = r.stdout + r.stderr
    check("another repository is linted by TOOLS_DIR's check.py, saying nothing of a worktree",
          _ran(r) and "linting with this worktree" not in out, out)


def commit_files(repo: Path, files: dict[str, str]) -> str:
    """Write and stage FILES, commit them, and return the hook's verdict line."""
    for name, body in files.items():
        (repo / name).write_text(body, encoding="utf-8")
    git(repo, "add", "--", *files)
    r = git(repo, "commit", "-m", "several")
    lines = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.startswith("JP_TOOLS: ")]
    return lines[-1] if lines else f"(rc {r.returncode}, no verdict) {r.stdout + r.stderr}"


def skipped_count_cases(tmp: Path) -> None:
    """A skipped file is not counted as checked: the verdict says how many of
    the staged files were checked, and why the rest were not."""
    print("\nSkipped files in the verdict:")
    repo, _ = build_repo(tmp)
    cases = (
        ("every file checked",
         {"one.py": CLEAN_PY},
         "JP_TOOLS: 1 of 1 staged file(s) checked, clean."),
        ("some skipped, one reason",
         {"two.py": CLEAN_PY, "NOTES.md": "# n\n", "data.json": "{}\n"},
         "JP_TOOLS: 1 of 3 staged file(s) checked, clean (2 skipped: no language detected)."),
        ("every file skipped",
         {"NOTES.md": "# m\n", "data.json": "[]\n"},
         "JP_TOOLS: 0 of 2 staged file(s) checked, nothing here to check (2 skipped: no language detected)."),
        ("two reasons, each counted",
         {".jp-tools-ignore": "gen.py  # generated, not ours\n", "gen.py": CLEAN_PY,
          "three.py": CLEAN_PY},
         ("JP_TOOLS: 1 of 3 staged file(s) checked, clean "
          "(2 skipped: 1 ignored by .jp-tools-ignore, 1 no language detected).")),
    )
    for label, files, verdict in cases:
        got = commit_files(repo, files)
        check(f"{label}: {verdict}", got == verdict, got)


def ratchet_cases(tmp: Path) -> None:
    """With quality-baseline.json, a commit that changes a file with findings
    must fix at least one, and the lowered count goes into the same commit."""
    print("\nThe baseline ratchet (--touched):")
    repo, _ = build_repo(tmp)
    debt = "import os\nimport sys\n\nx = 1\n"   # two F401s
    (repo / "debt.py").write_text(debt, encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "check.py"), ".", "--record-baseline",
                    "quality-baseline.json"], cwd=repo, capture_output=True, check=False)
    git(repo, "add", "--", "debt.py", "quality-baseline.json")
    git(repo, "commit", "-q", "--no-verify", "-m", "debt, and its baseline")
    recorded = (repo / "quality-baseline.json").read_text(encoding="utf-8")
    check("CONTROL: the baseline records debt.py", '"debt.py"' in recorded, recorded)

    r = commit_file(repo, "debt.py", debt.replace("x = 1", "x = 2"))
    out = r.stdout + r.stderr
    check("changing a file with findings and fixing none is refused",
          r.returncode != 0 and "still has 2 of its 2" in out, out)
    check("  and leaves the baseline as staged",
          git(repo, "diff", "--quiet", "--", "quality-baseline.json").returncode == 0
          and (repo / "quality-baseline.json").read_text(encoding="utf-8") == recorded)

    r = commit_file(repo, "debt.py", "import os\n\nx = 2\n")
    out = r.stdout + r.stderr
    check("fixing one commits", r.returncode == 0, out)
    check("  and says the baseline was lowered and staged", "lowered, and staged" in out, out)
    committed = git(repo, "show", "HEAD:quality-baseline.json").stdout
    check("  and the commit carries the lowered count",
          '"errors": 1' in committed and committed != recorded, committed)
    # Tracked files only: under CI's coverage, each check.py subprocess leaves
    # a .coverage.* data file in its cwd, which is untracked and not the hook's.
    tracked = git(repo, "status", "--porcelain", "--untracked-files=no").stdout
    check("  leaving nothing unstaged", tracked == "", tracked)

    r = commit_file(repo, "fresh.py", CLEAN_PY)
    check("a clean new file commits and leaves the baseline alone",
          r.returncode == 0 and "lowered" not in r.stdout + r.stderr, r.stdout + r.stderr)

    edited = (repo / "quality-baseline.json").read_text(encoding="utf-8") + "\n"
    (repo / "quality-baseline.json").write_text(edited, encoding="utf-8")
    r = commit_file(repo, "fresh2.py", CLEAN_PY)
    out = r.stdout + r.stderr
    check("an unstaged edit to the baseline is refused, not swept into the commit",
          r.returncode != 0 and "unstaged changes" in out, out)
    check("  and the edit is left as it was",
          (repo / "quality-baseline.json").read_text(encoding="utf-8") == edited)


DEBT = "import os\nimport sys\n\nx = 1\n"   # two F401s


def debt_repo(tmp: Path) -> Path:
    """A hooked repo whose baseline records two F401s in each of a.py and b.py."""
    repo, _ = build_repo(tmp)
    for name in ("a.py", "b.py"):
        (repo / name).write_text(DEBT, encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "check.py"), ".", "--record-baseline",
                    "quality-baseline.json"], cwd=repo, capture_output=True, check=False)
    git(repo, "add", "--", "a.py", "b.py", "quality-baseline.json")
    git(repo, "commit", "-q", "--no-verify", "-m", "debt, and its baseline")
    return repo


def baseline_errors(repo: Path, rev: str, path: str) -> int:
    doc = json.loads(git(repo, "show", f"{rev}:quality-baseline.json").stdout)
    return int(doc["files"][path]["ruff"]["errors"])


def tracked_changes(repo: Path) -> str:
    # Tracked files only: CI's coverage leaves untracked .coverage.* files.
    return git(repo, "status", "--porcelain", "--untracked-files=no").stdout


def lint_debt_ok_cases(tmp: Path) -> None:
    """A file that changed and fixed none of its findings goes through only
    with a reason in the message; a worse file never does."""
    print("\nLINT-DEBT-OK:")
    repo = debt_repo(tmp)
    recorded = (repo / "quality-baseline.json").read_text(encoding="utf-8")
    (repo / "a.py").write_text(DEBT.replace("x = 1", "x = 2"), encoding="utf-8")
    git(repo, "add", "--", "a.py")
    for label, msg in (("no line", "touch a"),
                       ("an empty reason", "touch a\n\nLINT-DEBT-OK:"),
                       ("a commented-out line", "touch a\n\n# LINT-DEBT-OK: no")):
        r = git(repo, "commit", "-m", msg)
        out = r.stdout + r.stderr
        check(f"{label}: refused, naming the file and the line that would pass",
              r.returncode != 0 and "a.py" in out and "LINT-DEBT-OK: <why" in out, out)
    check("  and the baseline is as staged",
          (repo / "quality-baseline.json").read_text(encoding="utf-8") == recorded
          and git(repo, "diff", "--quiet", "--", "quality-baseline.json").returncode == 0)

    r = git(repo, "commit", "-m", "touch a\n\nLINT-DEBT-OK: hotfix, the cleanup is #12")
    out = r.stdout + r.stderr
    check("with a reason: committed, and said", r.returncode == 0
          and "held for LINT-DEBT-OK" in out and "LINT-DEBT-OK in the message" in out, out)
    kept = baseline_errors(repo, "HEAD", "a.py")
    check("  keeping the count", kept == 2, str(kept))

    # One file held and one lowered: a refusal puts the lowered one back too.
    (repo / "a.py").write_text(DEBT.replace("x = 1", "x = 3"), encoding="utf-8")
    (repo / "b.py").write_text("import os\n\nx = 1\n", encoding="utf-8")
    git(repo, "add", "--", "a.py", "b.py")
    r = git(repo, "commit", "-m", "a held, b fixed")
    check("one held, one lowered, no line: refused", r.returncode != 0, r.stdout + r.stderr)
    check("  and the baseline is put back as staged, b.py's drop undone",
          (repo / "quality-baseline.json").read_text(encoding="utf-8") == recorded
          and git(repo, "diff", "--quiet", "--", "quality-baseline.json").returncode == 0)
    git(repo, "reset", "-q", "--hard")

    (repo / "a.py").write_text(DEBT + "import json\n", encoding="utf-8")
    git(repo, "add", "--", "a.py")
    r = git(repo, "commit", "-m", "worse\n\nLINT-DEBT-OK: no line clears this")
    check("a file that got worse is refused, whatever the message says",
          r.returncode != 0 and "LINT-DEBT-OK in the message" not in r.stdout + r.stderr,
          r.stdout + r.stderr)
    git(repo, "reset", "-q", "--hard")

    (repo / ".git" / "hooks" / "commit-msg").unlink()
    (repo / "a.py").write_text(DEBT.replace("x = 1", "x = 4"), encoding="utf-8")
    git(repo, "add", "--", "a.py")
    r = git(repo, "commit", "-m", "touch\n\nLINT-DEBT-OK: no hook to read me")
    check("with no commit-msg hook to read the line, a held file is refused",
          r.returncode != 0 and "no JP_TOOLS commit-msg hook" in r.stdout + r.stderr,
          r.stdout + r.stderr)


def ratchet_index_cases(tmp: Path) -> None:
    """What a commit records is what its own tree has: `git commit <path>` and
    a partial stage."""
    print("\nThe ratchet and the index:")
    repo = debt_repo(tmp)
    (repo / "a.py").write_text("import os\n\nx = 1\n", encoding="utf-8")
    r = git(repo, "commit", "-m", "fix one, by path", "a.py")
    check("`git commit <path>` with a fix commits the lowered count",
          r.returncode == 0 and baseline_errors(repo, "HEAD", "a.py") == 1, r.stdout + r.stderr)
    check("  and says the index will show it changed", "git commit <path>" in r.stdout + r.stderr,
          r.stdout + r.stderr)
    r = commit_file(repo, "fresh.py", CLEAN_PY)
    check("the next commit is not refused over the index git left behind",
          r.returncode == 0, r.stdout + r.stderr)
    kept = baseline_errors(repo, "HEAD", "a.py")
    check("  and the gain is still there", kept == 1, str(kept))
    left = tracked_changes(repo)
    check("  and nothing tracked is left changed", left == "", left)

    # Staged: a comment only. Unstaged: a fix. The hook measures the working copy.
    (repo / "b.py").write_text("# note\n" + DEBT, encoding="utf-8")
    git(repo, "add", "--", "b.py")
    (repo / "b.py").write_text("# note\nimport os\n\nx = 1\n", encoding="utf-8")
    r = git(repo, "commit", "-m", "partial\n\nLINT-DEBT-OK: even with a reason")
    check("a file staged in part is refused, not measured on its working copy",
          r.returncode != 0 and "unstaged changes as well" in r.stdout + r.stderr,
          r.stdout + r.stderr)
    kept = baseline_errors(repo, "HEAD", "b.py")
    check("  and HEAD's baseline is unchanged", kept == 2, str(kept))
    check("  and so is the staged one",
          git(repo, "diff", "--quiet", "--cached", "--", "quality-baseline.json").returncode == 0)


def main() -> int:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return 0
    # Every commit below goes through the shim, which runs the template from
    # the tree it resolves. Name THIS tree, the one under test; otherwise the
    # shim would find ~/projects/JP_TOOLS and test whatever that holds.
    os.environ["JP_TOOLS_DIR"] = str(ROOT)
    with tempfile.TemporaryDirectory() as td:
        symlinked_hook_cases(Path(td))
    with tempfile.TemporaryDirectory() as td:
        append_roundtrip_cases(Path(td))
    with tempfile.TemporaryDirectory() as td:
        status_cases(Path(td))
    if not shutil.which("ruff"):
        print(f"SKIP the rest: ruff not found, check.py cannot fail a file ({checks - fails}/{checks} passed above)")
        return 1 if fails else 0

    print("Hook template, static (hooks/pre-commit.sh, which the shim runs):")
    hook_src = (ROOT / "hooks" / "pre-commit.sh").read_text(encoding="utf-8")
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
        # What the installer says it set up is a separate claim from what it
        # wrote, and a description can outlive the behaviour it describes.
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
        check("... and its findings are printed above the verdict",
              "F401" in r.stdout + r.stderr, r.stdout + r.stderr)
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
        skipped_count_cases(Path(td))
    with tempfile.TemporaryDirectory() as td:
        ratchet_cases(Path(td))
    with tempfile.TemporaryDirectory() as td:
        lint_debt_ok_cases(Path(td))
    with tempfile.TemporaryDirectory() as td:
        ratchet_index_cases(Path(td))
    with tempfile.TemporaryDirectory() as td:
        commit_msg_cases(Path(td))
    with tempfile.TemporaryDirectory() as td:
        incapable_tools_cases(Path(td))
    with tempfile.TemporaryDirectory() as td:
        shim_cases(Path(td))
    with tempfile.TemporaryDirectory() as td:
        own_worktree_cases(Path(td))

    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
