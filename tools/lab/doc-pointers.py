#!/usr/bin/env python3
"""Do the home-directory paths written into our docs still resolve?

ANSWERS JP_TOOLS #53: nothing verified a filesystem pointer in our own docs,
  and five of nine in METHODOLOGY.md §11 did not resolve on 2026-09-08. A
  stale pointer fails no build and reads as authoritative. CLAUDE.md had
  already corrected `~/mandelbrotexplorer/` two days earlier, after a session
  followed it and concluded the tree was m18-only, and the correction never
  reached METHODOLOGY because nothing checks a link.

THREE OUTCOMES PER POINTER, NEVER TWO (METHODOLOGY §2.8):
  RESOLVES             the path exists on this machine
  NOT ON THIS MACHINE  absent here, under a prefix DECLARED to live elsewhere
  BROKEN               absent here, and nothing says it lives anywhere else
  With two buckets, every m18-only path reads as broken, someone deletes a
  good pointer, and the tool manufactures the defect it exists to find. The
  remote list is declared, never guessed. Its one default,
  `~/portfolio_notes/` on jap-m18, comes from CLAUDE.md ("On the m18 only");
  add more with --remote PREFIX=HOST.

TESTED AGAINST: METHODOLOGY.md §11 on origin/master, on joe-MacBookAir,
  against #53's own table measured on this box. See the commit that added
  this file for the result.

NOT TESTED AGAINST:
  - Repo-relative paths, which are NOT checked at all. In a JP_TOOLS doc a
    relative path often names a file in ANOTHER repo, so flagging those would
    be the false BROKEN this tool must not produce.
  - A pointer quoted on purpose as a wrong path, to correct it ("this said
    ~/mandelbrotexplorer/, which exists on no box"). That reads as BROKEN.
    It is the right call for a pointer and the wrong one for a quotation, and
    only a person can tell them apart.
  - Windows paths (`~\\JP_TOOLS`, `C:\\...`), and machines where HOME is not
    /home/<user>.
  - Placeholders (`<name>`, `{x}`, `OLDNAME`): the first two are skipped and
    counted; an uppercase word inside a path is indistinguishable from a real
    name and is checked like one.

IT MISLED ITS AUTHOR ONCE: the first whole-repo run flagged README's usage
  examples (`~/backup1`, `~/data`, `~/roms/psx`) as BROKEN. Those are example
  arguments inside fenced code blocks, not pointers. Paths inside fences are
  now SKIPPED by default and counted in the summary; --include-code checks
  them as well. An example in a fence that really is a pointer goes unchecked
  by default, and the count is what says so.

usage:
    doc-pointers.py [PATH ...]           markdown files or directories
                                         (default: this repo's tracked *.md)
    doc-pointers.py --remote '~/portfolio_notes/=jap-m18' FILE
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
from pathlib import Path

# Declared, with its source. Never inferred from a path being absent here.
DEFAULT_REMOTE = {"~/portfolio_notes/": "jap-m18"}

# A pointer: ~/..., $HOME/..., or /home/<user>/..., up to a character that
# cannot be part of a path in prose or markdown.
POINTER = re.compile(r"(?:~|\$HOME|/home/[A-Za-z0-9._-]+)/[^\s`'\"()\[\]<>{},;|]*")
TEMPLATE = re.compile(r"[<>{}]")
TRAILING = ".:,;!?"


def _name_process(name: bytes) -> None:
    """btop shows this instead of python3 (CLAUDE.md, every script names itself)."""
    try:
        import ctypes
        ctypes.CDLL(None).prctl(15, name, 0, 0, 0)
    except (OSError, AttributeError):
        # reason: no libc or no prctl (named types): the process runs unnamed, which is never fatal
        pass


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


def main() -> int:
    _name_process(b"jp-doc-pointers")
    ap = argparse.ArgumentParser(description="Check home-directory pointers in markdown.")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--remote", action="append", default=[], metavar="PREFIX=HOST",
                    help="declare a prefix that lives on another machine")
    ap.add_argument("--all", action="store_true", help="print RESOLVES rows too")
    ap.add_argument("--include-code", action="store_true",
                    help="also check paths inside fenced code blocks")
    args = ap.parse_args()

    remote = dict(DEFAULT_REMOTE)
    for item in args.remote:
        prefix, _, host = item.partition("=")
        remote[_canonical(prefix)] = host or "declared remote"

    files = _markdown_files(args.paths)
    rows: list[tuple[str, str, str, str]] = []
    counts = {"RESOLVES": 0, "NOT ON THIS MACHINE": 0, "BROKEN": 0}
    read, unreadable, templates, in_code = 0, 0, 0, 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            unreadable += 1
            continue
        read += 1
        seen: set[str] = set()
        fenced = False
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
                continue
            if fenced and not args.include_code:
                in_code += len(POINTER.findall(line))
                continue
            for m in POINTER.finditer(line):
                pointer = m.group(0).rstrip(TRAILING)
                if pointer in seen or pointer.endswith("/~"):
                    continue
                seen.add(pointer)
                if TEMPLATE.search(line[m.end():m.end() + 1]):
                    templates += 1
                    continue
                canon = _canonical(pointer)
                if _resolves(pointer):
                    outcome, where = "RESOLVES", ""
                else:
                    host = next((h for pre, h in remote.items() if canon.startswith(pre)), "")
                    outcome, where = ("NOT ON THIS MACHINE", host) if host else ("BROKEN", "")
                counts[outcome] += 1
                rows.append((outcome, f"{f}:{lineno}", pointer, where))

    if not read:
        print(f"doc-pointers: read no markdown ({len(files)} named, {unreadable} unreadable)",
              file=sys.stderr)
        return 2
    for outcome, loc, pointer, where in rows:
        if outcome == "RESOLVES" and not args.all:
            continue
        print(f"{outcome:<20} {loc}  {pointer}" + (f"   [{where}]" if where else ""))
    print(f"doc-pointers: {read} file(s) read, {unreadable} unreadable, "
          f"{sum(counts.values())} pointer(s): "
          + ", ".join(f"{k} {v}" for k, v in counts.items())
          + f"; {templates} placeholder(s) skipped; {in_code} in code blocks, not checked")
    return 1 if counts["BROKEN"] else 0


if __name__ == "__main__":
    sys.exit(main())
