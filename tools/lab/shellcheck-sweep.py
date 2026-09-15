#!/usr/bin/env python3
"""How much would a shellcheck gate find across a tree of git repos?

ANSWERS: for every git-tracked .sh/.bash file, and every extensionless file
  whose #! names sh or bash (python excluded), in each repo directly under the
  given roots: how many files, how many are clean, findings by shellcheck
  level, and which repos carry the most. The number #57 said to measure before
  shellcheck becomes a gate, so the design is chosen from a count, not a guess.

TESTED AGAINST: iteration8 ~/projects, 2026-09-14 about 15:20 local,
  shellcheck 0.9.0: 261 shell files in 18 repos, 118 clean; error 5, warning
  106, style 141, info 477. Posted to JP_TOOLS #57. It ran then as an inline
  script over ssh; this file is that script, saved so it can be re-run rather
  than quoted.

NOT TESTED AGAINST: a repo whose files git cannot list (skipped silently, it is
  not a git repo); a shebang like "#!/usr/bin/env -S bash -e" (matched by the
  "bash" test, as intended, but not exercised); shellcheck versions other than
  0.9.0, whose levels or codes may differ; files that are not UTF-8 (read as
  bytes for the shebang only, then handed to shellcheck as-is).

usage: tools/lab/shellcheck-sweep.py [ROOT ...]      (default: ~/projects)
"""
import collections
import json
import os
import shutil
import subprocess
import sys


def name_process() -> None:
    try:
        import ctypes
        ctypes.CDLL(None).prctl(15, b"shellcheck-swp", 0, 0, 0)
    except (OSError, AttributeError):
        # reason: no libc or no prctl (named types): the process runs unnamed, which is never fatal
        pass


def shell_files(root: str) -> list[tuple[str, str]]:
    out = []
    for repo in sorted(os.listdir(root)):
        rpath = os.path.join(root, repo)
        if not os.path.isdir(os.path.join(rpath, ".git")):
            continue
        ls = subprocess.run(["git", "-C", rpath, "ls-files"], capture_output=True,
                            text=True, check=False).stdout.split("\n")
        for rel in ls:
            p = os.path.join(rpath, rel)
            if not rel or not os.path.isfile(p):
                continue
            if rel.endswith((".sh", ".bash")):
                out.append((repo, p))
                continue
            if "." not in os.path.basename(rel):
                try:
                    with open(p, "rb") as fh:
                        first = fh.readline(256)
                except OSError as e:
                    # Said, not skipped: a script left out of the sweep is a
                    # script the sweep reports nothing about.
                    print(f"shellcheck-sweep: {p} unreadable ({e}); NOT swept", file=sys.stderr)
                    continue
                if (first.startswith(b"#!") and b"python" not in first
                        and (first.split(b"/")[-1].strip().startswith(b"sh") or b"bash" in first)):
                    out.append((repo, p))
    return out


def main() -> int:
    name_process()
    if not shutil.which("shellcheck"):
        print("shellcheck not found; nothing measured", file=sys.stderr)
        return 2
    roots = sys.argv[1:] or [os.path.expanduser("~/projects")]
    files = [f for r in roots for f in shell_files(r)]
    print(f"shell files: {len(files)} in {len({r for r, _ in files})} repos")
    levels: collections.Counter[str] = collections.Counter()
    by_repo: collections.Counter[str] = collections.Counter()
    clean = unparsed = 0
    for repo, p in files:
        r = subprocess.run(["shellcheck", "--format=json1", p], capture_output=True,
                           text=True, check=False)
        try:
            comments = json.loads(r.stdout or "{}").get("comments", [])
        except ValueError:
            unparsed += 1
            continue
        if not comments:
            clean += 1
        for c in comments:
            levels[c["level"]] += 1
            by_repo[repo] += 1
    print(f"files with zero findings: {clean} | could not parse output: {unparsed}")
    print("findings by level:", dict(levels))
    print("top repos by findings:", by_repo.most_common(8))
    # A count that could not be taken is not a zero.
    return 2 if unparsed else 0


if __name__ == "__main__":
    sys.exit(main())
