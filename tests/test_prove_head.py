#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_prove_head.py
prove-head.sh, in a throwaway git tree holding copies of it and run-suites.sh,
with stand-ins for composer and mutate-spec.py whose outcome each case sets.
The invariants it exists to keep:

    - composer install failing stops it with exit 1, before any suite runs.
    - A failing suite, a suite that only skipped, or a spec not killed on
      target each make it exit 1; an off-target spec's output is printed.
    - Exit 0 only when composer, every suite and every spec passed.
    - The old path, tools/lab/i8-proof.sh, forwards to it.

    python3 tests/test_prove_head.py
"""

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, run

_MADE: list[Path] = []
atexit.register(lambda: [shutil.rmtree(p, ignore_errors=True) for p in _MADE])

FAKE_COMPOSER = '#!/bin/sh\nexit "${FAKE_COMPOSER_RC:-0}"\n'
FAKE_MUTATE_SPEC = """\
import os, pathlib, sys
out = pathlib.Path(os.environ["MUTATE_OUT_DIR"])
(out / "spec-a.mutate.out").write_text(
    "mutant-x: survived\\n" + os.environ.get("FAKE_SPEC_LAST", "every one killed on target") + "\\n")
sys.exit(int(os.environ.get("FAKE_SPEC_RC", "0")))
"""


def _tree(suites: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    _MADE.append(root)
    for name in ("prove-head.sh", "run-suites.sh"):
        shutil.copy(ROOT / name, root / name)
    (root / "tools" / "lab").mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "lab" / "i8-proof.sh", root / "tools" / "lab" / "i8-proof.sh")
    (root / "mutate-spec.py").write_text(FAKE_MUTATE_SPEC, encoding="utf-8")
    (root / "tests" / "mutants").mkdir(parents=True)
    (root / "tests" / "mutants" / "spec-a.json").write_text("{}", encoding="utf-8")
    for name, body in suites.items():
        (root / "tests" / name).write_text(body, encoding="utf-8")
    (root / "bin").mkdir()
    (root / "bin" / "composer").write_text(FAKE_COMPOSER, encoding="utf-8")
    (root / "bin" / "composer").chmod(0o755)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "tree"], check=True)
    return root


PASS = {"test_ok.py": 'print("fine")\n'}


def _prove(root: Path, script: str = "prove-head.sh", **env: str) -> tuple[int, str]:
    full = dict(os.environ, JOBS="2", PATH=f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}", **env)
    p = subprocess.run(["bash", script], cwd=root, capture_output=True, text=True,
                       env=full, check=False)
    return p.returncode, p.stdout + p.stderr


def test_everything_passing_is_the_only_exit_0() -> None:
    rc, out = _prove(_tree(PASS))
    check("everything passed, so exit 0", rc, 0)
    check_true("  says so on its last line", out.rstrip().endswith("suites rc=0 specs rc=0"))


def test_composer_failing_stops_it() -> None:
    rc, out = _prove(_tree(PASS), FAKE_COMPOSER_RC="1")
    check("composer fails: exit 1", rc, 1)
    check_true("  no suite ran", "suites:" not in out)
    check_true("  and it says why", "composer install failed" in out)


def test_a_failing_suite_fails_it() -> None:
    rc, out = _prove(_tree({**PASS, "test_bad.py": "import sys\nsys.exit(1)\n"}))
    check("a failing suite: exit 1", rc, 1)
    check_true("  suites rc=1 reported", "suites rc=1 specs rc=0" in out)


def test_a_suite_that_only_skipped_fails_it() -> None:
    rc, out = _prove(_tree({**PASS, "test_skip.py": 'print("SKIP: no tool")\n'}))
    check("a skip: exit 1", rc, 1)
    check_true("  suites rc=2 reported", "suites rc=2 specs rc=0" in out)


def test_a_spec_off_target_fails_it_and_shows_why() -> None:
    rc, out = _prove(_tree(PASS), FAKE_SPEC_RC="1", FAKE_SPEC_LAST="1 survived")
    check("a spec off target: exit 1", rc, 1)
    check_true("  its output printed", "--- spec-a.mutate.out ---" in out and "mutant-x: survived" in out)


def test_the_old_lab_path_forwards() -> None:
    root = _tree({**PASS, "test_skip.py": 'print("SKIP: no tool")\n'})
    check("tools/lab/i8-proof.sh gives the same verdict",
          _prove(root, "tools/lab/i8-proof.sh")[0], 1)
    check("  and passes where it passes", _prove(_tree(PASS), "tools/lab/i8-proof.sh")[0], 0)


if __name__ == "__main__":
    sys.exit(run(globals(), "prove-head.sh"))
