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

PARALLEL BY DEFAULT, PER MUTANT: each spec's control runs once (the runner's
--control-only, which also checks that every chosen mutant anchors); once it
passes, each of that spec's mutants runs on its own (--no-control --only
NAME). Runs from every spec share one pool of --jobs at once (default: the
CPU count), so one spec of slow mutants no longer runs them one after
another. --jobs 1 is serial. A mutant name given twice, or shared by two of
a spec's mutants, is one run. A spec that cannot be read gets its own failing
line, and the other specs still run.

<stem>.mutate.out is written as the runs come back, in spec order: the
control, then each mutant's output, so one slow mutant holds back the
finished ones after it. It is a file for reading once the run is over.

The runner mutates its tree in place and refuses a tree another run holds,
so every run, control or mutant, gets its OWN copy of ROOT, made beside ROOT
(a test that finds a sibling repository beside this one still finds it) and
removed afterwards; $MUTATE_COPY_DIR, when set, is where the copies go
instead, for a ROOT whose parent the user cannot write (a CI checkout as a
non-root user). ROOT itself is never mutated. Each run's XDG_CACHE_HOME
is inside its copy, so whatever its tools cache goes with the copy and never
touches the user's own cache. A copy holds ROOT's own files: ROOT's vendor/
and node_modules/, which runs only read, are links to ROOT's, and its .git is
not copied (below).

OWN INDEX AND HEAD PER COPY. A copy is registered as a linked worktree of
ROOT's repository, whether ROOT's .git is a directory or (in a worktree or a
submodule) a file naming a gitdir elsewhere, with a copy of ROOT's index and
HEAD detached at ROOT's commit. A plain copy of a .git file would share ROOT's
index and HEAD with every copy, so a test that runs `git add` in one wrote
ROOT's index; a copied .git directory cost a whole history per run. On ROOT's
branch a commit in a copy would move it, hence detached. A copy shares the
repository's refs and objects, as any worktree does. Its gitdir is removed
with the copy.

KEPT VERDICTS. With $MUTATE_CACHE_DIR set and strace installed, a whole spec
(not an --only run) whose every mutant was killed on target is kept there,
and a later run reuses its verdict while nothing its runs read has changed,
printing "kept"; anything else runs again (jpcheck/spec_cache.py says how).
Without strace nothing is kept, and the run says so.

A SIGTERM (a job manager's cancel) ends the run without leaving a copy or a
gitdir behind: the runners still going are sent TERM (the runner restores and
exits 143), runs not yet started do not start, every copy is removed as its
run returns, and mutate-spec exits 143.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import NoReturn

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jpcheck import spec_cache
from platformtools.procname import name_process

RUNNER = Path(__file__).resolve().parent / "mutate-on-target.py"
RUN_LINE = re.compile(r"mutate-on-target\.py\s+\S+\s+(\S+)\s+(\S+)")

# Set by SIGTERM: no new run starts. RUNNING holds the runners still going,
# so the handler can pass the TERM on to them.
STOP = threading.Event()
RUNNING: set[subprocess.Popen[str]] = set()
RUNNING_LOCK = threading.Lock()


def _on_term(*_: object) -> None:
    STOP.set()
    with RUNNING_LOCK:
        for child in RUNNING:
            child.terminate()
    sys.exit(143)


def spec_stem(spec: str, base: str) -> str:
    """SPEC's name for its label and output file: its path under BASE, the
    directory all the specs given share, '/' as '__'. Two specs with one file
    name in different directories (books/code.json, figures/code.json) stay
    apart; specs in one directory keep their file names."""
    rel = os.path.relpath(os.path.abspath(spec), base)
    return rel.removesuffix(".json").replace(os.sep, "__")


def _git(root: str, *args: str) -> str:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                          check=True).stdout.strip()


# ROOT's installed tool directories: only read, never mutated, so a copy
# links to ROOT's rather than copying them.
SHARED = ("vendor", "node_modules")


def _copy_tree(root: str, tree: str) -> None:
    """TREE as a copy of ROOT's own files. ROOT's .git is left out (own_git
    gives the copy its git state) and SHARED are links to ROOT's: those were
    most of a copy's size and time (a JP_TOOLS clone with them, 4.5 s per
    copy inside the CI image; without, 0.9 s)."""
    skip = {".git", *SHARED}
    shutil.copytree(root, tree, symlinks=True,
                    ignore=lambda src, names: [n for n in names if n in skip and src == root])
    for name in SHARED:
        if os.path.isdir(os.path.join(root, name)):
            os.symlink(os.path.join(root, name), os.path.join(tree, name))


def own_git(root: str, tree: str) -> str | None:
    """When ROOT is a git checkout (its .git a directory, or a file in a
    worktree), make TREE, a copy of ROOT without .git, a linked worktree of
    ROOT's repository (see OWN INDEX AND HEAD PER COPY above), and return its
    gitdir for removal; else None."""
    if not os.path.exists(os.path.join(root, ".git")):
        return None
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    index = _git(root, "rev-parse", "--path-format=absolute", "--git-path", "index")
    sha = _git(root, "rev-parse", "--verify", "HEAD")
    os.makedirs(os.path.join(common, "worktrees"), exist_ok=True)
    gitdir = tempfile.mkdtemp(prefix="mutate-", dir=os.path.join(common, "worktrees"))
    for name, text in (("commondir", common), ("gitdir", os.path.join(tree, ".git")),
                       ("HEAD", sha)):
        with open(os.path.join(gitdir, name), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    if os.path.exists(index):
        shutil.copyfile(index, os.path.join(gitdir, "index"))
    with open(os.path.join(tree, ".git"), "w", encoding="utf-8") as fh:
        fh.write(f"gitdir: {gitdir}\n")
    return gitdir


Reads = tuple[set[str], set[str]]


def run_in_copy(root: str, stem: str, args: list[str], traced: bool = False) -> tuple[int, str, Reads | None]:
    """The runner on a private copy of ROOT: (rc, its output, and when TRACED
    and strace is here, the paths its run read: jpcheck.spec_cache)."""
    if STOP.is_set():
        return 143, "mutate-spec: stopped (SIGTERM) before this run started\n", None
    work = tempfile.mkdtemp(prefix=f".mutate-{stem}-",
                            dir=os.environ.get("MUTATE_COPY_DIR") or os.path.dirname(root))
    tree = os.path.join(work, os.path.basename(root))
    gitdir = None
    trace = os.path.join(work, "strace.out")
    prefix = spec_cache.strace_prefix(trace) if traced else None
    try:
        _copy_tree(root, tree)
        gitdir = own_git(root, tree)
        # Caches inside the copy: what a test or its tools cache (pwsh's
        # startup profile, say) is removed with it, and runs at once, or one
        # killed mid-write, cannot corrupt the user's own. The kept verdicts
        # are this run's own business: a test that runs mutate-spec itself
        # would otherwise trace its runs and keep or reuse verdicts in the
        # cache this run is filling.
        env = {k: v for k, v in os.environ.items() if k != "MUTATE_CACHE_DIR"}
        env["XDG_CACHE_HOME"] = os.path.join(work, ".cache")
        p = subprocess.Popen([*(prefix or []), sys.executable, str(RUNNER), tree, *args],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        with RUNNING_LOCK:
            RUNNING.add(p)
            if STOP.is_set():
                p.terminate()
        try:
            out, _ = p.communicate()
        finally:
            with RUNNING_LOCK:
                RUNNING.discard(p)
        reads = None
        if prefix:
            text = Path(trace).read_text(encoding="utf-8", errors="replace")
            reads = spec_cache.sort_paths(spec_cache.paths_in(text), tree, root, (work, tempfile.gettempdir()))
        return p.returncode, out, reads
    except (OSError, subprocess.CalledProcessError) as e:
        return 2, f"mutate-spec: could not make a private copy of {root}: {e}\n", None
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if gitdir:
            shutil.rmtree(gitdir, ignore_errors=True)


def run_spec(root: str, spec: str, outdir: str, base: str, only: list[str] | None,
             pool: concurrent.futures.Executor) -> tuple[str, int, str]:
    """One spec, all its mutants or just ONLY's: its control once, then each
    mutant in its own copy, all through POOL. (stem, rc, the last line)."""
    stem = spec_stem(spec, base)
    # A spec that cannot be read is this spec's failure, reported on its own
    # line; raised, it would stop main before the other specs' lines.
    try:
        with open(spec, encoding="utf-8") as fh:
            m = RUN_LINE.search(json.load(fh).get("about", ""))
        every = [n for n, _ in names(spec)]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        return stem, 1, f"{type(e).__name__}: {e}"
    if not m:
        return stem, 1, "NO RUN LINE in about"
    run = [m.group(1).rstrip(".,;"), m.group(2).rstrip(".,;"), os.path.abspath(spec)]
    # Each name once, in order: --only X runs every mutant named X, so a name
    # given twice, or two mutants of one name, must still be one run.
    chosen = list(dict.fromkeys(only or every))
    ran = sum(n in chosen for n in every)
    out = os.path.join(outdir, stem + ".mutate.out")
    # KEPT VERDICTS (jpcheck/spec_cache.py): a whole spec, never an --only run.
    cache = None if only else os.environ.get("MUTATE_CACHE_DIR")
    want = spec_cache.key(os.path.abspath(spec), os.path.abspath(__file__), str(RUNNER)) if cache else {}
    if cache and (verdict := spec_cache.kept(cache, stem, root, want)):
        last = f"{verdict}, kept: nothing its runs read has changed"
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(last + "\n")
        return stem, 0, last
    rc, text, reads = pool.submit(run_in_copy, root, stem,
                                  [*run, "--control-only", *(a for n in chosen for a in ("--only", n))],
                                  bool(cache)).result()
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    if rc != 0:
        return stem, rc, (text.strip().splitlines() or ["(no output)"])[-1]
    runs = [pool.submit(run_in_copy, root, stem, [*run, "--no-control", "--only", n], bool(cache)) for n in chosen]
    rcs, every_read = [], [reads]
    with open(out, "a", encoding="utf-8") as fh:
        for fut in runs:
            mrc, text, mreads = fut.result()
            rcs.append(mrc)
            every_read.append(mreads)
            fh.write(text)
        of = f"{ran} of {len(every)} mutant(s) (--only)" if only else f"{ran} mutant(s)"
        bad = sum(r != 0 for r in rcs)
        last = f"mutate-spec: {of}, one control, " + (
            "every one killed on target" if not bad else f"{bad} wrong")
        fh.write(last + "\n")
    _keep(cache, stem, root, want, every_read, last if not bad else None)
    return stem, max(rcs, default=0), last


def _keep(cache: str | None, stem: str, root: str, want: dict[str, str],
          every_read: list[Reads | None], verdict: str | None) -> None:
    """Keep a spec's all-killed VERDICT with what every one of its runs read;
    nothing when a run was not traced (no strace) or the verdict is not a pass."""
    if cache and verdict and all(r is not None for r in every_read):
        repo = set().union(*(r[0] for r in every_read if r))
        ext = set().union(*(r[1] for r in every_read if r))
        try:
            spec_cache.keep(cache, stem, root, want, repo, ext, verdict)
        except OSError as e:  # reason: a verdict not kept only means the spec runs next time; the run's verdict stands
            sys.stderr.write(f"mutate-spec: {stem}: verdict not kept: {e}\n")


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
    signal.signal(signal.SIGTERM, _on_term)
    root, specs, jobs = os.path.abspath(args.root), args.specs, max(1, args.jobs)
    base = os.path.commonpath([os.path.dirname(os.path.abspath(sp)) for sp in specs])
    if args.list:
        for sp in specs:
            for name, target in names(sp):
                print(f"{spec_stem(sp, base)}: {name}\t-> {target}")
        return 0
    runs = chosen(specs, args.only)
    if os.environ.get("MUTATE_CACHE_DIR") and not spec_cache.strace_prefix(""):
        sys.stderr.write("mutate-spec: MUTATE_CACHE_DIR is set but strace is not installed: "
                         "every spec runs, and no verdict is kept\n")
    outdir = os.environ.get("MUTATE_OUT_DIR", ".")
    bad = 0
    # Each spec is a waiting thread of its own; only POOL's runs count against --jobs.
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool, \
            concurrent.futures.ThreadPoolExecutor(max_workers=len(runs)) as specs_at_once:
        futures = [specs_at_once.submit(run_spec, root, sp, outdir, base, only, pool)
                   for sp, only in runs.items()]
        for fut in concurrent.futures.as_completed(futures):
            stem, rc, last = fut.result()
            print(f"{stem}: rc={rc} {last[:140]}", flush=True)
            bad += rc != 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
