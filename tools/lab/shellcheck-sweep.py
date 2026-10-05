#!/usr/bin/env python3
"""How much would a shellcheck gate find across a tree of git repos?

ANSWERS: for every git-tracked .sh/.bash file, and every extensionless file
  whose #! names sh or bash (python excluded), in each repo directly under the
  given roots: how many files, how many are clean, findings by shellcheck
  level, and which repos carry the most: the count a shellcheck gate's
  design is chosen from, rather than a guess.

TESTED AGAINST: a tree of 18 repos holding 261 shell files, shellcheck
  0.9.0: 118 clean; error 5, warning 106, style 141, info 477. It first ran
  as an inline script; this file is that script, since restructured for the
  smell limits, and it prints byte-identical output to that form over the
  same tree.

NOT TESTED AGAINST: a repo whose files git cannot list (skipped silently, it is
  not a git repo); a shebang like "#!/usr/bin/env -S bash -e" (matched by the
  "bash" test, as intended, but not exercised); shellcheck versions other than
  0.9.0, whose levels or codes may differ; files that are not UTF-8 (read as
  bytes for the shebang only, then handed to shellcheck as-is).

Exit status: 0 measured, 2 shellcheck missing or any output unparseable (a
count that could not be taken is not a zero).

usage: tools/lab/shellcheck-sweep.py [ROOT ...]      (default: the current directory)
"""
import collections
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# The repo root, so platformtools imports from here.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platformtools.procname import name_process


def _names_shell(first_line: bytes) -> bool:
    """Does a #! line run sh or bash (and not python)?"""
    return (first_line.startswith(b"#!") and b"python" not in first_line
            and (first_line.split(b"/")[-1].strip().startswith(b"sh") or b"bash" in first_line))


def _is_shell_script(path: str) -> bool:
    """A .sh/.bash file, or an extensionless one whose #! names a shell.

    An unreadable candidate is named on stderr: a script left out of the
    sweep is a script the sweep reports nothing about.
    """
    if path.endswith((".sh", ".bash")):
        return True
    if "." in os.path.basename(path):
        return False
    try:
        with open(path, "rb") as fh:
            return _names_shell(fh.readline(256))
    except OSError as e:
        print(f"shellcheck-sweep: {path} unreadable ({e}); NOT swept", file=sys.stderr)
        return False


def shell_files(root: str) -> list[tuple[str, str]]:
    """(repo, path) for every shell script tracked in the repos directly under ROOT."""
    out = []
    for repo in sorted(os.listdir(root)):
        rpath = os.path.join(root, repo)
        if not os.path.isdir(os.path.join(rpath, ".git")):
            continue
        ls = subprocess.run(["git", "-C", rpath, "ls-files"], capture_output=True,
                            text=True, check=False).stdout.split("\n")
        paths = (os.path.join(rpath, rel) for rel in ls if rel)
        out += [(repo, p) for p in paths if os.path.isfile(p) and _is_shell_script(p)]
    return out


def _findings(path: str) -> list[dict[str, str]] | None:
    """shellcheck's comments on PATH, or None when its output does not parse."""
    r = subprocess.run(["shellcheck", "--format=json1", path], capture_output=True,
                       text=True, check=False)
    try:
        comments: list[dict[str, str]] = json.loads(r.stdout or "{}").get("comments", [])
    except ValueError:
        # reason: None is the "could not parse" outcome, counted and printed
        return None
    return comments


Results = list[tuple[str, list[dict[str, str]] | None]]


def _tally(results: Results) -> tuple[collections.Counter[str], collections.Counter[str]]:
    """(findings by level, findings by repo)."""
    levels: collections.Counter[str] = collections.Counter()
    by_repo: collections.Counter[str] = collections.Counter()
    for repo, comments in results:
        for c in comments or []:
            levels[c["level"]] += 1
            by_repo[repo] += 1
    return levels, by_repo


def main() -> int:
    name_process("shellcheck-swp")
    if not shutil.which("shellcheck"):
        print("shellcheck not found; nothing measured", file=sys.stderr)
        return 2
    files = [f for r in (sys.argv[1:] or ["."]) for f in shell_files(r)]
    print(f"shell files: {len(files)} in {len({r for r, _ in files})} repos")
    results: Results = [(repo, _findings(p)) for repo, p in files]
    unparsed = _report(results)
    return 2 if unparsed else 0


def _report(results: Results) -> int:
    """Print the counts. Returns how many outputs could not be parsed."""
    unparsed = sum(1 for _, c in results if c is None)
    levels, by_repo = _tally(results)
    print(f"files with zero findings: {sum(1 for _, c in results if c == [])} "
          f"| could not parse output: {unparsed}")
    print("findings by level:", dict(levels))
    print("top repos by findings:", by_repo.most_common(8))
    return unparsed


if __name__ == "__main__":
    sys.exit(main())
