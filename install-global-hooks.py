#!/usr/bin/env python3
"""
JP_TOOLS/install-global-hooks.py
Put every git repository on this box behind the scratch commit guard.

Nothing anyone relies on runs from scratch (METHODOLOGY 2.11, rule 6). A hook
installed repository by repository leaves out every repository nobody ran
the installer in, and every clone and `git init` to come. So this sets git's
GLOBAL core.hooksPath to one directory of hooks, each two lines running
hooks/global-dispatch.sh from this checkout: pre-commit runs the scratch check
(fails closed), and every hook then runs the repository's own hook, so
nothing a repository had stops running.

A repository whose LOCAL core.hooksPath is set reads its own directory, not
the global one. Each such repository under the scanned directory is named:
covered when its own pre-commit runs scratch-refs.py, NOT COVERED otherwise.

usage:
    install-global-hooks.py [--scan DIR]   install, then name the repositories
                                           under DIR (default ~/projects)
                                           that the global hooks cannot reach
    install-global-hooks.py --check [--scan DIR]
                                           install nothing; say whether it is
                                           installed and current, and scan

Exit codes (jpexit): 0 OK, installed (or, with --check, installed and
current) and every repository found is covered; 1 FINDINGS, a repository
found is NOT COVERED, or with --check the hooks are missing or stale; 2
NOT_RUN, the global core.hooksPath already names another directory, which is
refused rather than replaced.
"""

import subprocess
import sys
from pathlib import Path

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jpexit
from platformtools.procname import name_process

TOOLS = Path(__file__).resolve().parent
DISPATCH = TOOLS / "hooks" / "global-dispatch.sh"
# Every hook git runs on a client. A name missing here would leave that hook
# of every repository unrun once core.hooksPath points away from .git/hooks.
HOOKS = (
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
    "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout",
    "post-merge", "pre-push", "pre-auto-gc", "post-rewrite", "sendemail-validate",
    "fsmonitor-watchman", "post-index-change", "reference-transaction", "push-to-checkout",
)


def hooks_dir() -> Path:
    return Path.home() / ".config" / "git" / "jp-tools-hooks"


def shim(name: str) -> str:
    return f'#!/bin/sh\nexec sh "{DISPATCH.as_posix()}" {name} "$@"\n'


def git_global(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "config", "--global", *args], capture_output=True, text=True,
                          check=False)


def current(where: Path) -> list[str]:
    """Each hook name whose file in WHERE is missing or not this checkout's shim."""
    stale = []
    for name in HOOKS:
        p = where / name
        if not p.is_file() or p.read_text(encoding="utf-8") != shim(name):
            stale.append(name)
    return stale


def install(where: Path) -> None:
    where.mkdir(parents=True, exist_ok=True)
    for name in HOOKS:
        p = where / name
        p.write_text(shim(name), encoding="utf-8", newline="\n")
        p.chmod(0o755)


def local_hooks_paths(scan: Path) -> list[tuple[Path, str, bool]]:
    """(repository, its local core.hooksPath, whether that directory's
    pre-commit runs scratch-refs.py) for each repository directly under SCAN
    that sets one."""
    out = []
    for repo in sorted(p for p in scan.iterdir() if (p / ".git").exists()):
        r = subprocess.run(["git", "-C", str(repo), "config", "--local", "--get", "core.hooksPath"],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            continue
        local = r.stdout.strip()
        pre = (repo / local if not Path(local).is_absolute() else Path(local)) / "pre-commit"
        covered = pre.is_file() and "scratch-refs.py" in pre.read_text(encoding="utf-8", errors="replace")
        out.append((repo, local, covered))
    return out


def report_scan(scan: Path) -> int:
    if not scan.is_dir():
        print(f"install-global-hooks: no {scan} to scan")
        return jpexit.OK
    uncovered = 0
    for repo, local, covered in local_hooks_paths(scan):
        if covered:
            print(f"  covered: {repo} (its own core.hooksPath={local} runs scratch-refs.py)")
        else:
            uncovered += 1
            print(f"  NOT COVERED: {repo} sets core.hooksPath={local}, whose pre-commit does not "
                  f"run scratch-refs.py; add: python3 \"{(TOOLS / 'scratch-refs.py').as_posix()}\" --hook")
    return jpexit.FINDINGS if uncovered else jpexit.OK


def main() -> int:
    name_process("jpt-global-hook")
    args = sys.argv[1:]
    if {"-h", "--help"} & set(args):
        print(__doc__)
        return jpexit.OK
    scan = Path(args[args.index("--scan") + 1]) if "--scan" in args else Path.home() / "projects"
    where = hooks_dir()
    have = git_global("--get", "core.hooksPath").stdout.strip()
    if have and Path(have).expanduser() != where:
        print(f"install-global-hooks: the global core.hooksPath is already {have}: refused, not "
              f"replaced. Unset it (git config --global --unset core.hooksPath) or point it here: {where}")
        return jpexit.NOT_RUN
    if "--check" in args:
        stale = current(where)
        if not have or stale:
            print(f"install-global-hooks: NOT INSTALLED or stale at {where}: "
                  f"core.hooksPath={have or 'unset'}, {len(stale)} hook(s) missing or stale")
            report_scan(scan)
            return jpexit.FINDINGS
        print(f"install-global-hooks: installed and current at {where}, from {TOOLS}")
        return report_scan(scan)
    install(where)
    set_rc = git_global("core.hooksPath", where.as_posix())
    if set_rc.returncode != 0:
        print(f"install-global-hooks: git config --global core.hooksPath failed: {set_rc.stderr.strip()}")
        return jpexit.NOT_RUN
    print(f"install-global-hooks: {len(HOOKS)} hooks in {where}, global core.hooksPath set; "
          f"every repository on this box now runs {DISPATCH}")
    return report_scan(scan)


if __name__ == "__main__":
    sys.exit(main())
