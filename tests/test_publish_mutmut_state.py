#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_publish_mutmut_state.py
ci-image/publish-mutmut-state.sh, run from a throwaway checkout into a
throwaway state directory. The rules it exists to keep:

    - mutants/ and baseline.json go into STATE/current, whole: the old state
      is replaced, and no next/ or old/ is left behind.
    - mutants/'s .git link and anything a mutant wrote that git does not
      track stay out; mutmut's own .meta, .spans and mutmut-stats.json go in.
    - Nothing in the published state is group- or world-writable.
    - With nothing to publish, or a STATE that cannot be written: exit 2,
      and the old state is left as it was.

    python3 tests/test_publish_mutmut_state.py
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, run

SCRIPT = ROOT / "ci-image" / "publish-mutmut-state.sh"
_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")


def _checkout(where: Path) -> Path:
    """A checkout holding mutants/ (a git repo tracking a.py, with mutmut's
    files and a stray a mutant wrote) and baseline.json."""
    mutants = where / "mutants"
    mutants.mkdir(parents=True)
    subprocess.run(["git", "-C", str(mutants), "init", "-q"], check=True, env=_ENV)
    (mutants / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(mutants), "add", "a.py"], check=True, env=_ENV)
    for name in ("a.py.meta", "a.py.spans", "mutmut-stats.json", "stray.txt"):
        (mutants / name).write_text("{}", encoding="utf-8")
    (where / "baseline.json").write_text('{"m.x_f": {"killed": 1}}', encoding="utf-8")
    os.chmod(mutants / "a.py", 0o666)
    return where


def _publish(cwd: Path, state: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), str(state)], cwd=cwd, capture_output=True,
                          text=True, check=False, env=_ENV)


def _files(top: Path) -> list[str]:
    return sorted(str(p.relative_to(top)) for p in top.rglob("*") if p.is_file())


def test_publishes_the_state_whole() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        here = _checkout(Path(td) / "co")
        state = Path(td) / "state"
        (state / "current").mkdir(parents=True)
        (state / "current" / "old-file").write_text("old", encoding="utf-8")
        (state / "old").mkdir()  # left by a run that died between its two renames
        (state / "old" / "x").write_text("x", encoding="utf-8")
        p = _publish(here, state)
        check("exit 0", p.returncode, 0)
        check("current holds mutmut's files and the baseline, no stray, no .git",
              _files(state / "current"),
              ["baseline.json", "mutants/a.py", "mutants/a.py.meta", "mutants/a.py.spans",
               "mutants/mutmut-stats.json"])
        check("  and only current is left", sorted(p.name for p in state.iterdir()), ["current"])
        check_true("  and nothing in it is group- or world-writable",
                   all(not (q.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
                       for q in (state / "current").rglob("*")))
        check_true("  and it says what it left out", "left out, written by mutants: 1 path(s)" in p.stdout)


def test_refuses_and_leaves_the_old_state() -> None:
    with tempfile.TemporaryDirectory() as td:
        empty = Path(td) / "empty"
        empty.mkdir()
        state = Path(td) / "state"
        (state / "current").mkdir(parents=True)
        (state / "current" / "old-file").write_text("old", encoding="utf-8")
        p = _publish(empty, state)
        check("nothing to publish: exit 2", p.returncode, 2)
        check("  and the old state stays", _files(state / "current"), ["old-file"])
        if os.geteuid() != 0:
            here = _checkout(Path(td) / "co")
            os.chmod(state, 0o555)
            try:
                p = _publish(here, state)
            finally:
                os.chmod(state, 0o755)
            check("a state that cannot be written: exit 2", p.returncode, 2)
            check("  and the old state stays", _files(state / "current"), ["old-file"])


if __name__ == "__main__":
    sys.exit(run(globals(), "publish-mutmut-state.sh"))
