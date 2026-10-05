#!/usr/bin/env python3
"""Mutation control: each mutant must fail the ONE check aimed at it.

Usage:
    python mutate-on-target.py TREE TARGET TEST SPEC.json

TREE is a checkout whose TARGET (a path inside it) is mutated in place and
restored whatever happens; TEST (also inside TREE) is run from TREE after each
mutant. SPEC is JSON:

    {"mutants": [{"name": "...", "edits": [["old", "new"], ...],
                  "target": "the check label that must read FAIL"}, ...]}

Every `old` must occur exactly once in TARGET, or nothing runs.

A MUTANT COUNTS ONLY ON TARGET: the test exits non-zero AND a line starting
FAIL (or [FAIL]) carries the target label, or the label's own line reads FAIL
right after it. A mutant killed by some other check, or by a crash, has not
shown that its own check can see anything, so it is reported OFF TARGET and
counts as wrong. The unmutated control must pass first.

NO BYTECODE. A .pyc is trusted when the source's size and whole-second mtime
match, so a same-length mutant restored inside the same second would keep
running. Children write none, and TARGET's cache is removed after every write.

ONE RUN PER TREE. The original is read once, at the start, and written back at
the end, so a run that started while another run's mutant was in TARGET would
take that mutant for the original and restore it for good. A run holds an
exclusive lock on TREE for its whole life and refuses when another holds it:
give each concurrent run its own copy of the tree (mutate-spec.py does).

A KILLED RUN IS NOT FORGOTTEN. While a mutant can be in TARGET, its original
bytes are kept outside the tree, under $XDG_CACHE_HOME/mutate-on-target, keyed
by TARGET's resolved path. A run that finds one refuses, naming it: its TARGET
may still hold a mutant. Restore TARGET from it, delete it, and run again.

BYTES, VERIFIED. TARGET is read and written as bytes, so line endings and
encoding survive, and the final restore is read back and compared with the
original.

A .py TEST runs under this interpreter; any other runs by its own #! line when
executable, else under sh. Each test run gets its own TMPDIR, removed after,
and its own session, so the timeout ($MUTATE_TIMEOUT seconds, default 600)
kills every process it started. POSIX only: the lock is flock(2).

Exit status: 0 the control passes and every mutant is killed on target; 1 one
is not; 2 could not look (a spec that does not anchor, a tree another run
holds, an original left by a killed run, a control that fails); 3 the final
restore did not read back byte for byte.
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from platformtools.procname import name_process


@dataclass(frozen=True)
class Run:
    """One invocation: where TARGET and TEST are, and where the original is kept."""

    tree: str
    target_rel: str
    test_rel: str
    target: str
    cache: str
    saved: str
    test_cmd: list[str]
    timeout: int


def refuse(msg: str) -> NoReturn:
    sys.stderr.write(f"mutate: {msg}\n")
    sys.exit(2)


def make_run(tree: str, target_rel: str, test_rel: str) -> Run:
    target = os.path.join(tree, target_rel)
    test = os.path.join(tree, test_rel)
    saved_dir = os.path.join(os.environ.get("XDG_CACHE_HOME")
                             or os.path.join(os.path.expanduser("~"), ".cache"),
                             "mutate-on-target")
    key = hashlib.sha256(os.path.realpath(target).encode()).hexdigest()[:16]
    test_cmd = ([sys.executable, test] if test.endswith(".py")
                else [test] if os.access(test, os.X_OK) else ["sh", test])
    return Run(tree, target_rel, test_rel, target, importlib.util.cache_from_source(target),
               os.path.join(saved_dir, key + ".orig"), test_cmd,
               int(os.environ.get("MUTATE_TIMEOUT", "600")))


def lock_tree(tree: str) -> int:
    """An exclusive lock on the open directory, held until exit: it leaves
    nothing in the tree and dies with the process however it ends."""
    fd = os.open(tree, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        refuse(f"another mutate-on-target run holds {tree}; give each concurrent "
               "run its own copy of the tree")
    return fd


def load_mutants(r: Run, spec: str, original: bytes) -> list[dict[str, Any]]:
    """The spec's mutants, refused unless every edit anchors exactly once."""
    with open(spec, encoding="utf-8") as fh:
        mutants: list[dict[str, Any]] = json.load(fh)["mutants"]
    for m in mutants:
        for old, _new in m["edits"]:
            n = original.count(old.encode())
            if n != 1:
                refuse(f"{m['name']}: {old!r} occurs {n} times in {r.target_rel}, "
                       "not once; could not look")
    return mutants


def write(r: Run, data: bytes) -> None:
    with open(r.target, "wb") as fh:
        fh.write(data)
    if os.path.exists(r.cache):
        os.remove(r.cache)


def run_test(r: Run) -> tuple[int, str]:
    """The test's exit code and output; -1 and TIMEOUT when it ran out of time."""
    tmp = tempfile.mkdtemp(prefix="mut-on-target-")
    try:
        p = subprocess.Popen(r.test_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, cwd=r.tree, start_new_session=True,
                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=tmp))
        try:
            out, err = p.communicate(timeout=r.timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
            return -1, "TIMEOUT"
        except BaseException:
            # reason: an exit mid-test takes the test's whole group with it
            os.killpg(p.pid, signal.SIGKILL)
            raise
        return p.returncode, out + err
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def on_target(out: str, label: str) -> bool:
    """FAIL first with the label on the line, or the label first and FAIL
    straight after it."""
    for ln in out.splitlines():
        s = ln.lstrip()
        if s.startswith(("FAIL", "[FAIL]")) and label in s:
            return True
        if s.startswith(label) and s[len(label):].lstrip().startswith("FAIL"):
            return True
    return False


def verdict(rc: int, out: str, label: str) -> tuple[str, bool]:
    """What one mutant's run showed, and whether that counts as killed."""
    if rc != 0 and on_target(out, label):
        return f"killed on target (rc {rc})", True
    if rc != 0:
        return f"OFF TARGET: rc {rc}, '{label}' did not FAIL", False
    return "SURVIVED", False


def mutate_all(r: Run, original: bytes, mutants: list[dict[str, Any]]) -> int:
    """Run the control, then each mutant; the number not killed on target."""
    write(r, original)
    rc, out = run_test(r)
    last = out.strip().splitlines()[-1] if out.strip() else "no output"
    print(f"  {'control (unmutated)':<34} "
          f"{'passes, as it must' if rc == 0 else 'FAILED: control broken'} ({last})")
    if rc != 0:
        refuse("the control does not pass; no mutant can mean anything")
    bad = 0
    for m in mutants:
        data = original
        for old, new in m["edits"]:
            data = data.replace(old.encode(), new.encode())
        write(r, data)
        said, killed = verdict(*run_test(r), m["target"])
        bad += not killed
        print(f"  {m['name']:<34} {said}")
    return bad


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        sys.exit(__doc__)
    name_process("mut-on-target")
    r = make_run(*argv[:3])
    lock_tree(r.tree)
    if os.path.exists(r.saved):
        refuse(f"{r.saved} holds the ORIGINAL of {r.target}, left by a run that was "
               "killed while a mutant was in place, so the target may still be "
               "mutated. Restore it from that file, delete the file, run again")
    with open(r.target, "rb") as fh:
        original = fh.read()
    mutants = load_mutants(r, argv[3], original)
    print(f"target {r.target_rel}, test {r.test_rel}, {len(mutants)} mutant(s)")
    # A SIGTERM becomes an exit, so the restore below still runs; a SIGKILL
    # leaves the original behind for the next run to refuse on.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    os.makedirs(os.path.dirname(r.saved), exist_ok=True)
    with open(r.saved, "wb") as fh:
        fh.write(original)
    try:
        bad = mutate_all(r, original, mutants)
    finally:
        write(r, original)
        with open(r.target, "rb") as fh:
            restored = fh.read() == original
        # Kept when the restore does not read back, so the original survives.
        if restored:
            os.remove(r.saved)
    if not restored:
        sys.stderr.write(f"mutate: {r.target} did NOT read back as the original after "
                         f"the restore; the original is in {r.saved}\n")
        return 3
    print(f"mutate-on-target: {len(mutants)} mutant(s), "
          f"{'every one killed on target' if not bad else f'{bad} wrong'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
