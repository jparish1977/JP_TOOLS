#!/usr/bin/env python3
"""Do the home-directory paths written into our docs still resolve?

ANSWERS: which `~/`, `$HOME/` and `/home/<user>/` paths in markdown no longer
  resolve. A stale pointer fails no build and reads as authoritative, and one
  followed to nothing produces a confident wrong conclusion ("that tree only
  exists on another machine").

THREE OUTCOMES PER POINTER, NEVER TWO (METHODOLOGY 2.8):
  RESOLVES             the path exists on this machine
  NOT ON THIS MACHINE  absent here, under a prefix DECLARED to live elsewhere
  BROKEN               absent here, and nothing says it lives anywhere else
  With two buckets, every path that lives on one other machine reads as
  broken, someone deletes a good pointer, and the tool manufactures the
  defect it exists to find. The remote list is declared with --remote, never
  guessed.

TESTED AGAINST: a methodology document's pointer section, nine pointers,
  against a table of the same pointers checked by hand on the same machine;
  the tool's outcomes matched the table. Since restructured for the smell
  limits; it prints byte-identical output to that form over the same files.

NOT TESTED AGAINST:
  - Repo-relative paths, which are NOT checked at all. In a doc a relative
    path often names a file in ANOTHER repo, so flagging those would be the
    false BROKEN this tool must not produce.
  - A pointer quoted on purpose as a wrong path, to correct it ("this said
    ~/old-name/, which exists nowhere"). That reads as BROKEN. It is the
    right call for a pointer and the wrong one for a quotation, and only a
    person can tell them apart.
  - Windows paths (`~\\x`, `C:\\...`), and machines where HOME is not
    /home/<user>.
  - Placeholders (`<name>`, `{x}`, `OLDNAME`): the first two are skipped and
    counted; an uppercase word inside a path is indistinguishable from a real
    name and is checked like one.

FENCED CODE IS SKIPPED BY DEFAULT: usage examples (`~/backup1`, `~/data`)
  are arguments, not pointers, and read as BROKEN if checked. Paths inside
  fences are counted in the summary; --include-code checks them as well. An
  example in a fence that really is a pointer goes unchecked by default, and
  the count is what says so.

usage:
    doc-pointers.py [PATH ...]           markdown files or directories
                                         (default: this repo's tracked *.md)
    doc-pointers.py --remote '~/notes/=otherhost' FILE
    doc-pointers.py --all FILE           print RESOLVES rows too

Exit 0 when nothing is BROKEN, 1 when anything is, 2 when it read no markdown.
Read-only.
"""

import argparse
import glob
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# The repo root, so platformtools imports from here.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platformtools.procname import name_process

# A pointer: ~/..., $HOME/..., or /home/<user>/..., up to a character that
# cannot be part of a path in prose or markdown.
POINTER = re.compile(r"(?:~|\$HOME|/home/[A-Za-z0-9._-]+)/[^\s`'\"()\[\]<>{},;|]*")
TEMPLATE = re.compile(r"[<>{}]")
TRAILING = ".:,;!?"
OUTCOMES = ("RESOLVES", "NOT ON THIS MACHINE", "BROKEN")


@dataclass
class Tally:
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(OUTCOMES, 0))
    rows: list[tuple[str, str, str, str]] = field(default_factory=list)
    read: int = 0
    unreadable: int = 0
    templates: int = 0
    in_code: int = 0


def _canonical(pointer: str) -> str:
    """Spell ~/ and $HOME/ and /home/<me>/ one way, for the remote-prefix test."""
    home = str(Path.home())
    if pointer.startswith("$HOME/"):
        return "~/" + pointer[len("$HOME/"):]
    if pointer.startswith(home + "/"):
        return "~/" + pointer[len(home) + 1:]
    return pointer


def _resolves(pointer: str) -> bool:
    path = os.path.expanduser(os.path.expandvars(pointer))
    if any(c in path for c in "*?["):
        return bool(glob.glob(path))
    return os.path.exists(path)


def _markdown_files(paths: list[str]) -> list[Path]:
    if not paths:
        r = subprocess.run(["git", "ls-files", "*.md"], capture_output=True,
                           text=True, check=False)
        return [Path(p) for p in r.stdout.splitlines()] if r.returncode == 0 else []
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            out.extend(f for f in sorted(p.rglob("*.md"))
                       if ".git" not in f.parts and "node_modules" not in f.parts)
        else:
            out.append(p)
    return out


def _pointers(text: str, include_code: bool, tally: Tally) -> Iterator[tuple[int, str]]:
    """(line, pointer) for each distinct checkable pointer in TEXT; counts the skipped."""
    seen: set[str] = set()
    fenced = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced and not include_code:
            tally.in_code += len(POINTER.findall(line))
            continue
        for m in POINTER.finditer(line):
            pointer = m.group(0).rstrip(TRAILING)
            if pointer in seen or pointer.endswith("/~"):
                continue
            seen.add(pointer)
            if TEMPLATE.search(line[m.end():m.end() + 1]):
                tally.templates += 1
            else:
                yield lineno, pointer


def _outcome(pointer: str, remote: dict[str, str]) -> tuple[str, str]:
    """(outcome, the host it is declared to live on) for one pointer."""
    if _resolves(pointer):
        return "RESOLVES", ""
    canon = _canonical(pointer)
    host = next((h for pre, h in remote.items() if canon.startswith(pre)), "")
    return ("NOT ON THIS MACHINE", host) if host else ("BROKEN", "")


def _scan(files: list[Path], remote: dict[str, str], include_code: bool) -> Tally:
    tally = Tally()
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # reason: counted as unreadable, which the summary prints
            tally.unreadable += 1
            continue
        tally.read += 1
        for lineno, pointer in _pointers(text, include_code, tally):
            outcome, where = _outcome(pointer, remote)
            tally.counts[outcome] += 1
            tally.rows.append((outcome, f"{f}:{lineno}", pointer, where))
    return tally


def _report(tally: Tally, show_all: bool) -> list[str]:
    lines = [f"{outcome:<20} {loc}  {pointer}" + (f"   [{where}]" if where else "")
             for outcome, loc, pointer, where in tally.rows
             if show_all or outcome != "RESOLVES"]
    lines.append(f"doc-pointers: {tally.read} file(s) read, {tally.unreadable} unreadable, "
                 f"{sum(tally.counts.values())} pointer(s): "
                 + ", ".join(f"{k} {v}" for k, v in tally.counts.items())
                 + f"; {tally.templates} placeholder(s) skipped; "
                 f"{tally.in_code} in code blocks, not checked")
    return lines


def main() -> int:
    name_process("jp-doc-pointers")
    ap = argparse.ArgumentParser(description="Check home-directory pointers in markdown.")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--remote", action="append", default=[], metavar="PREFIX=HOST",
                    help="declare a prefix that lives on another machine")
    ap.add_argument("--all", action="store_true", help="print RESOLVES rows too")
    ap.add_argument("--include-code", action="store_true",
                    help="also check paths inside fenced code blocks")
    args = ap.parse_args()
    remote = {}
    for item in args.remote:
        prefix, _, host = item.partition("=")
        remote[_canonical(prefix)] = host or "declared remote"
    files = _markdown_files(args.paths)
    tally = _scan(files, remote, args.include_code)
    if not tally.read:
        print(f"doc-pointers: read no markdown ({len(files)} named, {tally.unreadable} unreadable)",
              file=sys.stderr)
        return 2
    print("\n".join(_report(tally, args.all)))
    return 1 if tally.counts["BROKEN"] else 0


if __name__ == "__main__":
    sys.exit(main())
