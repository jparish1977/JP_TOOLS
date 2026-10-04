#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutate_on_target.py
mutate-on-target.py leaves its tree as it found it, and mutate-spec.py runs
specs at once without sharing one.

No pytest, no dependencies, same as the rest of tests/.

WHAT IS CHECKED
    - A run restores TARGET byte for byte, CRLF kept, and leaves no saved
      original behind.
    - A second run on a tree another run holds refuses, exit 2, and touches
      nothing.
    - An original left by a killed run makes the next run refuse, exit 2,
      naming it, and touches neither file.
    - A SIGTERM while a mutant is in place still restores TARGET.
    - A spec that does not anchor refuses, exit 2.
    - Two specs given to mutate-spec.py run at the same time, each in its own
      copy, leaving ROOT untouched and no copy behind.
    Not checked: exit 3, a restore that does not read back; nothing short of
    a second writer racing the restore produces it.

    MUTATE_RUNNER=path runs these against another copy of the runner, with
    mutate-spec.py beside it.

    python3 tests/test_mutate_on_target.py
"""

import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from _harness import ROOT, check, check_true, run

RUNNER = os.environ.get("MUTATE_RUNNER") or str(ROOT / "mutate-on-target.py")
ORIGINAL = b"VALUE = 1\r\nOTHER = 0\r\n"
TEST = """import sys, time
text = open("t.py", "rb").read()
if b"VALUE = 3" in text:
    time.sleep(60)
if b"VALUE = 1" in text:
    print("  ok    value is one")
    sys.exit(0)
print("  FAIL  value is one")
sys.exit(1)
"""
TMP = tempfile.mkdtemp(prefix="t-mut-on-target-")


def fixture(name: str, new: str = "VALUE = 2") -> tuple[str, str, dict[str, str]]:
    """A tree with t.py (CRLF) and test.py, a one-mutant spec, and an env
    whose cache is private to this fixture."""
    tree = os.path.join(TMP, name)
    os.makedirs(tree)
    with open(os.path.join(tree, "t.py"), "wb") as fh:
        fh.write(ORIGINAL)
    with open(os.path.join(tree, "test.py"), "w", encoding="utf-8") as fh:
        fh.write(TEST)
    spec = os.path.join(TMP, name + ".json")
    with open(spec, "w", encoding="utf-8") as fh:
        json.dump({"mutants": [{"name": "value", "edits": [["VALUE = 1", new]],
                                "target": "value is one"}]}, fh)
    env = dict(os.environ, XDG_CACHE_HOME=os.path.join(TMP, name + "-cache"))
    return tree, spec, env


def saved_path(tree: str, env: dict[str, str]) -> str:
    key = hashlib.sha256(os.path.realpath(os.path.join(tree, "t.py")).encode()).hexdigest()[:16]
    return os.path.join(env["XDG_CACHE_HOME"], "mutate-on-target", key + ".orig")


def target_bytes(tree: str) -> bytes:
    with open(os.path.join(tree, "t.py"), "rb") as fh:
        return fh.read()


def runner(tree: str, spec: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, RUNNER, tree, "t.py", "test.py", spec],
                          capture_output=True, text=True, env=env, check=False, timeout=120)


def test_a_clean_run() -> None:
    tree, spec, env = fixture("clean")
    r = runner(tree, spec, env)
    check("a clean run: exit 0, the mutant killed on target",
          (r.returncode, "every one killed on target" in r.stdout), (0, True))
    check("a clean run: TARGET restored byte for byte, CRLF kept", target_bytes(tree), ORIGINAL)
    check_true("a clean run: no saved original left behind",
               not os.path.exists(saved_path(tree, env)))


def test_a_held_tree() -> None:
    tree, spec, env = fixture("held")
    fd = os.open(tree, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        r = runner(tree, spec, env)
    finally:
        os.close(fd)
    check("a held tree: refused, exit 2, saying another run holds it",
          (r.returncode, "another mutate-on-target run holds" in r.stderr), (2, True))
    check("a held tree: TARGET untouched", target_bytes(tree), ORIGINAL)


def test_a_killed_run() -> None:
    tree, spec, env = fixture("killed")
    mutated = ORIGINAL.replace(b"VALUE = 1", b"VALUE = 2")
    with open(os.path.join(tree, "t.py"), "wb") as fh:
        fh.write(mutated)
    saved = saved_path(tree, env)
    os.makedirs(os.path.dirname(saved))
    with open(saved, "wb") as fh:
        fh.write(ORIGINAL)
    r = runner(tree, spec, env)
    check("a killed run's original: refused, exit 2, naming it",
          (r.returncode, saved in r.stderr and "killed" in r.stderr), (2, True))
    check("a killed run's original: neither file is touched",
          (target_bytes(tree), os.path.exists(saved)), (mutated, True))


def test_a_sigterm() -> None:
    tree, spec, env = fixture("term", new="VALUE = 3")
    p = subprocess.Popen([sys.executable, RUNNER, tree, "t.py", "test.py", spec],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    deadline = time.time() + 60
    while b"VALUE = 3" not in target_bytes(tree) and time.time() < deadline:
        time.sleep(0.1)
    in_place = b"VALUE = 3" in target_bytes(tree)
    time.sleep(0.5)
    p.send_signal(signal.SIGTERM)
    try:
        p.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
    check_true("a SIGTERM: the mutant was in place when the signal went", in_place)
    check("a SIGTERM: TARGET restored byte for byte", target_bytes(tree), ORIGINAL)
    check_true("a SIGTERM: no saved original left behind",
               not os.path.exists(saved_path(tree, env)))


def test_no_anchor() -> None:
    tree, spec, env = fixture("anchor")
    with open(spec, "w", encoding="utf-8") as fh:
        json.dump({"mutants": [{"name": "gone", "edits": [["NOT THERE", "x"]],
                                "target": "value is one"}]}, fh)
    r = runner(tree, spec, env)
    check("a spec that does not anchor: refused, exit 2, 'occurs 0 times'",
          (r.returncode, "occurs 0 times" in r.stderr), (2, True))


def test_specs_at_once() -> None:
    tree, _, env = fixture("spec-tree")
    specs = []
    for name, new in (("one", "VALUE = 2"), ("two", "VALUE = 3")):
        sp = os.path.join(TMP, f"spec-{name}.json")
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump({"about": "Run: mutate-on-target.py TREE t.py test.py SPEC.",
                       "mutants": [{"name": name, "edits": [["VALUE = 1", new]],
                                    "target": "value is one"}]}, fh)
        specs.append(sp)
    # Every mutant makes the test sleep 3 s, so two specs take 6 s or more one
    # after the other and about 3 s at once.
    with open(os.path.join(tree, "test.py"), "w", encoding="utf-8") as fh:
        fh.write(TEST.replace('if b"VALUE = 3" in text:\n    time.sleep(60)',
                              'if b"VALUE = 1" not in text:\n    time.sleep(3)'))
    outdir = os.path.join(TMP, "spec-out")
    os.makedirs(outdir)
    t0 = time.time()
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(RUNNER), "mutate-spec.py"),
                        tree, *specs], capture_output=True, text=True,
                       env=dict(env, MUTATE_OUT_DIR=outdir), check=False, timeout=120)
    took = time.time() - t0
    check("mutate-spec: both specs killed on target, exit 0",
          (r.returncode, r.stdout.count("every one killed on target")), (0, 2))
    check_true(f"mutate-spec: they ran at once, under the 6 s two in a row need ({took:.1f} s)",
               took < 5.5)
    check("mutate-spec: ROOT itself untouched, byte for byte", target_bytes(tree), ORIGINAL)
    check("mutate-spec: no private copy left beside ROOT",
          [d for d in os.listdir(TMP) if d.startswith(".mutate-")], [])


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        rc = run(globals(), "mutate-on-target")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(rc)
