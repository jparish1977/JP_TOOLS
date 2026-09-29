#!/usr/bin/env python3
"""Which other repositories load one of this repo's scripts by path?

ANSWERS: before moving or splitting a root script, who outside this repo
  imports it as a module rather than running it, and which names they read
  off it. Such a consumer breaks on a move that every suite here passes. It
  lists each tracked .py/.sh file in the given repos that names one of this
  repo's root scripts AND uses a by-path loader, with the attributes read off
  a module variable.

TESTED AGAINST: the 49 other git repos under ~/projects on a laptop: nine
  files named a script; two really loaded JP_TOOLS' check.py (another repo's
  tests/gate-baseline.py, reading is_project_file and lab_claim, and
  a consuming repo's tools/lab/gate/checkmeasure.py, reading _smell_measure),
  the other seven named another repo's own check.py or fix.py; 0 unread.
  Positive control: checkmeasure.py found with `_smell_measure` among its
  reads. Its first run reported four a consuming repo files UNREAD: they were
  names with spaces that a whitespace split had broken, the tool's own bug,
  fixed by reading `ls-files -z`; that is why unread files are counted.

NOT TESTED AGAINST: repositories not cloned on this box, and loaders it does
  not name (a subprocess `python -c` building a path at runtime). The
  attribute list is a regex over common module variable names, so read the
  file before trusting it. It over-reports on purpose: a false hit costs a
  look, a missed consumer costs a broken gate.

usage: tools/lab/by-path-consumers.py [REPOS_DIR]   (default ~/projects)
  Run from the checkout root.
"""
import re
import subprocess
import sys
from pathlib import Path

LOADERS = ("spec_from_file_location", "run_path", "SourceFileLoader", "load_script",
           "import_module")
ATTR = re.compile(r"\b(?:check|chk|mod|module|ck|_check)\.([a-z_][a-z0-9_]*)\b")


def _name_process(name: bytes) -> None:
    """Name the process (what top and btop show) instead of python3."""
    try:
        import ctypes
        ctypes.CDLL(None).prctl(15, name, 0, 0, 0)
    except (OSError, AttributeError):
        # reason: no libc or no prctl: the process runs unnamed, which is never fatal
        pass


def tracked(repo: Path) -> list[str]:
    """Tracked .py and .sh files, NUL-separated so a name with a space stays whole."""
    r = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "*.py", "*.sh"],
                       capture_output=True, text=True, check=False)
    return [n for n in r.stdout.split("\0") if n]


def own_repos(here: Path) -> set[Path]:
    """This checkout, and the repository it is a worktree of, if it is one."""
    r = subprocess.run(["git", "-C", str(here), "rev-parse", "--git-common-dir"],
                       capture_output=True, text=True, check=False)
    common = (here / r.stdout.strip()).resolve() if r.returncode == 0 else here / ".git"
    return {here, common.parent}


def hits_in(repo: Path, scripts: list[str], unread: list[str]) -> list[str]:
    """Hits in REPO; each file that could not be read is added to UNREAD by name."""
    out = []
    for rel in tracked(repo):
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            unread.append(f"{repo.name}/{rel} ({exc.__class__.__name__})")
            continue
        named = [s for s in scripts if s in text]
        if named and any(ld in text for ld in LOADERS):
            attrs = sorted(set(ATTR.findall(text)))
            out.append(f"{repo.name}/{rel}: names {named}; attrs read {attrs[:25]}")
    return out


def main(argv: list[str]) -> int:
    here = Path.cwd().resolve()
    base = Path(argv[0]).expanduser() if argv else Path.home() / "projects"
    scripts = sorted(p.name for p in here.glob("*.py"))
    hits: list[str] = []
    unread: list[str] = []
    own = own_repos(here)
    repos = [p.parent for p in sorted(base.glob("*/.git")) if p.parent.resolve() not in own]
    for repo in repos:
        hits += hits_in(repo, scripts, unread)
    print("\n".join(hits))
    for u in unread:
        print(f"UNREAD {u}")
    print(f"{len(repos)} repo(s): {len(hits)} file(s) name a root script of this repo "
          f"next to a by-path loader; {len(unread)} tracked file(s) could not be read")
    return 1 if unread else 0


if __name__ == "__main__":
    _name_process(b"by-path-users")
    sys.exit(main(sys.argv[1:]))
