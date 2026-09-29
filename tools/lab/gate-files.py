#!/usr/bin/env python3
"""What does check.py say about each file, one line per file?

ANSWERS: which of the named files (or, with --tracked, every tracked
  .py/.sh/.bash/.js/.mjs/.cjs file) check.py fails or flags, with the rule and
  line of the first few findings, so a whole-tree gate reads in one screen.
  --base REF judges smells against REF, as CI does with the PR's base; without
  it check.py's default base is HEAD, where a new smell only warns.

TESTED AGAINST: this repo's by-path-load branch, --tracked --base
  origin/master, from a worktree with no node_modules/: 141 files; every
  Python and shell file exit 0, the five JS files exit 2 with eslint:error
  (the missing node_modules, see below). Its 20 new or changed files gated by
  name: all exit 0. Positive control: a file holding only `import os`
  printed `F401 os imported but unused` and exit 1.

NOT TESTED AGAINST: a tree without node_modules/ or vendor/. There eslint and
  phpstan cannot load and check.py exits 2 for those files: read an
  `eslint:error:0` line as a missing tool, not as a finding.

usage: tools/lab/gate-files.py [--only-bad] [--base REF] (--tracked | FILE ...)
  Run from the checkout root; it runs that checkout's check.py.
"""
import json
import subprocess
import sys

SOURCE = ("*.py", "*.sh", "*.bash", "*.js", "*.mjs", "*.cjs")


def _name_process(name: bytes) -> None:
    """Name the process (what top and btop show) instead of python3."""
    try:
        import ctypes
        ctypes.CDLL(None).prctl(15, name, 0, 0, 0)
    except (OSError, AttributeError):
        # reason: no libc or no prctl: the process runs unnamed, which is never fatal
        pass


def tracked() -> list[str]:
    r = subprocess.run(["git", "ls-files", *SOURCE], capture_output=True, text=True, check=True)
    return r.stdout.split()


def summary(path: str, base: list[str]) -> tuple[int, list[str]]:
    """check.py's exit code for PATH, and a line per non-clean check."""
    r = subprocess.run([sys.executable, "check.py", path, *base],
                       capture_output=True, text=True, check=False)
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        return r.returncode, [f"NOT JSON: {(r.stdout + r.stderr)[:200]}"]
    bad = []
    for c in doc.get("checks", []):
        if c["status"] != "pass" or c["issues"]:
            bad.append(f"{c['tool']}:{c['status']}:{len(c['issues'])}")
            bad += [f"\n      {i.get('line')}: {i.get('rule')} {i.get('message', '')[:110]}"
                    for i in c["issues"][:6]]
    return r.returncode, bad


def main(argv: list[str]) -> int:
    only_bad = "--only-bad" in argv
    args = [a for a in argv if a != "--only-bad"]
    base: list[str] = []
    if "--base" in args:
        i = args.index("--base")
        base = ["--smells-base", args[i + 1]]
        del args[i:i + 2]
    files = tracked() if args == ["--tracked"] else args
    if not files:
        sys.exit(__doc__)
    worst = 0
    for f in files:
        rc, bad = summary(f, base)
        worst = max(worst, rc)
        if bad or rc or not only_bad:
            print(f"{f:28s} rc={rc} {' '.join(bad)}")
    print(f"{len(files)} file(s), worst exit {worst}")
    return worst


if __name__ == "__main__":
    _name_process(b"gate-files")
    sys.exit(main(sys.argv[1:]))
