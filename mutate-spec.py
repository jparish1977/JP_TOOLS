#!/usr/bin/env python3
"""Run mutate-on-target.py for specs, taking TARGET and TEST from each spec's about.

Usage:
    python mutate-spec.py [--jobs N] [--only NAME ...] [--list] ROOT SPEC [SPEC ...]

--list prints every spec's mutants, name and target, and runs nothing.
--only NAME (repeatable, the name exactly) runs just the specs that have a
mutant of that name, each with only its own named mutants; a NAME no spec has
is refused, never run as nothing.

A spec's `about` carries its own run line (`mutate-on-target.py TREE TARGET
TEST SPEC`), so the caller does not restate it and get it wrong. Prints one
line per spec as it finishes, the runner's verdict and exit code, and exits 1
if any spec is not all killed on target. The runner's full output goes to
<stem>.mutate.out in $MUTATE_OUT_DIR (default the current directory), where
the stem is the spec's path under the directory all the given specs share,
'/' as '__' (pb/books/code.json is pb__books__code); specs in one directory
keep their file names.

PARALLEL BY DEFAULT: every spec at once, up to --jobs (default: the CPU
count). The runner mutates its tree in place and refuses a tree another run
holds, so each spec runs in its OWN copy of ROOT, made beside ROOT (a test
that finds a sibling repository beside this one still finds it) and removed
afterwards. ROOT itself is never mutated. --jobs 1 is serial.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from platformtools.procname import name_process

RUNNER = Path(__file__).resolve().parent / "mutate-on-target.py"
RUN_LINE = re.compile(r"mutate-on-target\.py\s+\S+\s+(\S+)\s+(\S+)")


def spec_stem(spec: str, base: str) -> str:
    """SPEC's name for its label and output file: its path under BASE, the
    directory all the specs given share, '/' as '__'. Two specs with one file
    name in different directories (books/code.json, figures/code.json) stay
    apart; specs in one directory keep their file names."""
    rel = os.path.relpath(os.path.abspath(spec), base)
    return rel.removesuffix(".json").replace(os.sep, "__")


def run_spec(root: str, spec: str, outdir: str, base: str,
             only: list[str] | None = None) -> tuple[str, int, str]:
    """One spec in a private copy of ROOT, all its mutants or just ONLY's:
    (stem, rc, the runner's last line)."""
    stem = spec_stem(spec, base)
    with open(spec, encoding="utf-8") as fh:
        m = RUN_LINE.search(json.load(fh).get("about", ""))
    if not m:
        return stem, 1, "NO RUN LINE in about"
    target, test = m.group(1).rstrip(".,;"), m.group(2).rstrip(".,;")
    work = tempfile.mkdtemp(prefix=f".mutate-{stem}-", dir=os.path.dirname(root))
    tree = os.path.join(work, os.path.basename(root))
    out = os.path.join(outdir, stem + ".mutate.out")
    try:
        shutil.copytree(root, tree, symlinks=True)
        with open(out, "w", encoding="utf-8") as fh:
            chosen = [a for name in only or [] for a in ("--only", name)]
            rc = subprocess.run([sys.executable, str(RUNNER), tree, target, test,
                                 os.path.abspath(spec), *chosen],
                                stdout=fh, stderr=subprocess.STDOUT, check=False).returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)
    with open(out, encoding="utf-8") as fh:
        last = (fh.read().strip().splitlines() or ["(no output)"])[-1]
    return stem, rc, last


def names(spec: str) -> list[tuple[str, str]]:
    """(name, target) of each of SPEC's mutants."""
    with open(spec, encoding="utf-8") as fh:
        return [(m["name"], m["target"]) for m in json.load(fh)["mutants"]]


def chosen(specs: list[str], only: list[str]) -> dict[str, list[str] | None]:
    """{spec: the ONLY names it has, or None for all}, leaving out a spec with
    none of them. A name no spec has stops the run: run as nothing, it would
    read as all killed."""
    if not only:
        return dict.fromkeys(specs)
    have = {sp: [n for n, _ in names(sp)] for sp in specs}
    missing = [n for n in only if not any(n in h for h in have.values())]
    if missing:
        sys.stderr.write(f"mutate-spec: --only {missing!r}: no mutant in the given specs has "
                         "that name; --list shows them\n")
        sys.exit(2)
    return {sp: [n for n in only if n in h] for sp, h in have.items() if any(n in h for n in only)}


class _Parser(argparse.ArgumentParser):
    """argparse, but a usage error exits 2 with the usage."""

    def error(self, message: str) -> NoReturn:
        sys.stderr.write(f"mutate-spec: {message}\n{__doc__}\n")
        sys.exit(2)


def main(argv: list[str]) -> int:
    p = _Parser(add_help=False)
    p.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    p.add_argument("--only", action="append", default=[], metavar="NAME")
    p.add_argument("--list", action="store_true")
    p.add_argument("root")
    p.add_argument("specs", nargs="+")
    args = p.parse_args(argv)
    name_process("mutate-spec")
    root, specs, jobs = os.path.abspath(args.root), args.specs, max(1, args.jobs)
    base = os.path.commonpath([os.path.dirname(os.path.abspath(sp)) for sp in specs])
    if args.list:
        for sp in specs:
            for name, target in names(sp):
                print(f"{spec_stem(sp, base)}: {name}\t-> {target}")
        return 0
    runs = chosen(specs, args.only)
    outdir = os.environ.get("MUTATE_OUT_DIR", ".")
    bad = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(jobs, len(runs))) as pool:
        futures = [pool.submit(run_spec, root, sp, outdir, base, only) for sp, only in runs.items()]
        for fut in concurrent.futures.as_completed(futures):
            stem, rc, last = fut.result()
            print(f"{stem}: rc={rc} {last[:140]}", flush=True)
            bad += rc != 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
