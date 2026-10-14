#!/usr/bin/env python3
"""
JP_TOOLS/scratch-refs.py
Refuse a commit whose files point into a scratch directory.

Nothing anyone relies on runs from scratch, or reads anything from it
(METHODOLOGY 2.11, rule 6). A committed file naming a scratch path is a
script, a doc or a CI step that relies on something that is not committed,
so the pre-commit hook refuses the commit.

THE WHOLE FILE IS READ, as staged, not only the lines the commit adds: a file
that relies on scratch is wrong however old the line is, and a commit that
touches it is the moment to fix it.

A scratch path is one of (SCRATCH below; written here so this file does not
hold one):
    a directory named "scratch" right under ~, $HOME, ${HOME}, /home/USER or /root
    /tmp/claude- and a user id: a Claude Code session's scratch directory
    any directory named "scratchpad"
each followed by "/" or the end of the word.

usage:
    scratch-refs.py --check   list every scratch path in the files staged for
                              commit; exit 1 if there are any

Exit codes (jpexit): 0 OK, none; 1 FINDINGS, listed on stdout as
"  PATH:LINE: TEXT"; 2 NOT_RUN, not in a git repository or a git step failed,
so nothing was checked. The hook reads anything but 0 or 1 as BROKEN.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jpexit
from platformtools.procname import name_process

# "scr[a]tch" matches the word and not this source, so the file that holds the
# rule can be committed under it.
SCRATCH = re.compile(r"(?:(?:~|\$HOME|\$\{HOME\}|/home/[^/\s]+|/root)/scr[a]tch"
                     r"|/tmp/claude-\d+|/scr[a]tchpad)(?:/|\b)")


class GitFailedError(Exception):
    """A git step this needs did not work; the message says which and why."""


def git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], capture_output=True, check=False)


def git_out(*args: str) -> bytes:
    """git's stdout, or GitFailedError: an empty list from a git that failed
    would read as "no scratch paths", and the hook would let the commit through."""
    result = git(*args)
    if result.returncode != 0:
        said = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitFailedError(f"git {args[0]} exited {result.returncode}: "
                             f"{said[-1] if said else 'no message'}")
    return result.stdout


def staged_files() -> list[str]:
    """Each staged added, copied, modified or renamed file. NUL-separated, so
    git does not quote a non-ASCII name."""
    out = git_out("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def scratch_lines(text: str) -> list[tuple[int, str]]:
    """(line number, the line) for each line of TEXT naming a scratch path."""
    return [(n, line.strip()) for n, line in enumerate(text.splitlines(), 1)
            if SCRATCH.search(line)]


def find_refs() -> dict[str, list[tuple[int, str]]]:
    """{path: [(line, text)]} over every staged file's staged copy. A file
    holding a NUL byte is binary and not read."""
    found: dict[str, list[tuple[int, str]]] = {}
    for path in staged_files():
        blob = git_out("show", f":{path}")
        if b"\0" in blob:
            continue
        if lines := scratch_lines(blob.decode("utf-8", "replace")):
            found[path] = lines
    return found


def main() -> int:
    name_process("scratch-refs")
    if {"-h", "--help"} & set(sys.argv[1:]) or "--check" not in sys.argv[1:]:
        print(__doc__)
        return jpexit.OK if {"-h", "--help"} & set(sys.argv[1:]) else jpexit.NOT_RUN
    top = git("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        print("scratch-refs: not in a git repository", file=sys.stderr)
        return jpexit.NOT_RUN
    os.chdir(top.stdout.decode("utf-8", "surrogateescape").strip())
    try:
        found = find_refs()
    except GitFailedError as e:
        print(f"scratch-refs: {e}; nothing was checked", file=sys.stderr)
        return jpexit.NOT_RUN
    for path, lines in sorted(found.items()):
        print("\n".join(f"  {path}:{n}: {text}" for n, text in lines))
    return jpexit.FINDINGS if found else jpexit.OK


if __name__ == "__main__":
    sys.exit(main())
