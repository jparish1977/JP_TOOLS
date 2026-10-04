#!/usr/bin/env python3
"""Run mutate-on-target.py for specs, taking TARGET and TEST from each spec's about.

Usage:
    python mutate-spec.py [--jobs N] ROOT SPEC [SPEC ...]

A spec's `about` carries its own run line (`mutate-on-target.py TREE TARGET
TEST SPEC`), so the caller does not restate it and get it wrong. Prints one
line per spec as it finishes, the runner's verdict and exit code, and exits 1
if any spec is not all killed on target. The runner's full output goes to
<spec stem>.mutate.out in $MUTATE_OUT_DIR (default the current directory).

PARALLEL BY DEFAULT: every spec at once, up to --jobs (default: the CPU
count). The runner mutates its tree in place and refuses a tree another run
holds, so each spec runs in its OWN copy of ROOT, made beside ROOT (a test
that finds a sibling repository beside this one still finds it) and removed
afterwards. ROOT itself is never mutated. --jobs 1 is serial.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER = Path(__file__).resolve().parent / "mutate-on-target.py"
RUN_LINE = re.compile(r"mutate-on-target\.py\s+\S+\s+(\S+)\s+(\S+)")


def name_process() -> None:
    with contextlib.suppress(OSError, AttributeError):  # reason: no libc or no prctl; unnamed is never fatal
        ctypes.CDLL(None).prctl(15, b"mutate-spec", 0, 0, 0)


def run_spec(root: str, spec: str, outdir: str) -> tuple[str, int, str]:
    """One spec in a private copy of ROOT: (stem, rc, the runner's last line)."""
    stem = os.path.basename(spec).removesuffix(".json")
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
            rc = subprocess.run([sys.executable, str(RUNNER), tree, target, test,
                                 os.path.abspath(spec)],
                                stdout=fh, stderr=subprocess.STDOUT, check=False).returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)
    with open(out, encoding="utf-8") as fh:
        last = (fh.read().strip().splitlines() or ["(no output)"])[-1]
    return stem, rc, last


def main(argv: list[str]) -> int:
    jobs = os.cpu_count() or 1
    if argv[:1] == ["--jobs"] and len(argv) > 1:
        jobs, argv = max(1, int(argv[1])), argv[2:]
    if len(argv) < 2:
        sys.exit(__doc__)
    name_process()
    root, specs = os.path.abspath(argv[0]), argv[1:]
    outdir = os.environ.get("MUTATE_OUT_DIR", ".")
    bad = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(jobs, len(specs))) as pool:
        futures = [pool.submit(run_spec, root, sp, outdir) for sp in specs]
        for fut in concurrent.futures.as_completed(futures):
            stem, rc, last = fut.result()
            print(f"{stem}: rc={rc} {last[:140]}", flush=True)
            bad += rc != 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
