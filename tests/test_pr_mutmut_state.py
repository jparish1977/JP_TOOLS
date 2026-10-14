#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_pr_mutmut_state.py
ci-image/pr-mutmut-state.sh, run from throwaway checkouts into a throwaway
PR directory: a pull request's own mutmut state, kept between its pushes.

    - save keeps the run's mutants/ as the branch's entry for the state
      version fetch copied; restore in a later checkout puts it in place of
      the state's copy, keeping the state's baseline.
    - An entry for another state version is never restored, and is removed
      by a save once three hours old; one for this version stays.
    - No PR directory, nothing kept, a PR directory that cannot be written:
      said, exit 0, the checkout's state copy untouched.
    - No state copied, or a usage error: exit 2.
    - The branch's characters outside [A-Za-z0-9._-] are "_" in its entry.

    python3 tests/test_pr_mutmut_state.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _harness import ROOT, check, check_true, run

PR = ROOT / "ci-image" / "pr-mutmut-state.sh"
_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")


def _checkout(where: Path, meta: str, version: str = "v-1", baseline: str = "B") -> Path:
    """A checkout as fetch-mutmut-state.sh leaves it: mutants/ (a git repo
    tracking a.py, with mutmut's .meta holding META), the state's baseline and
    the version it was copied from."""
    mutants = where / "mutants"
    mutants.mkdir(parents=True)
    subprocess.run(["git", "-C", str(mutants), "init", "-q"], check=True, env=_ENV)
    (mutants / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(mutants), "add", "a.py"], check=True, env=_ENV)
    (mutants / "a.py.meta").write_text(meta, encoding="utf-8")
    (where / "mutmut-baseline.json").write_text(baseline, encoding="utf-8")
    (where / "mutmut-state-version").write_text(version + "\n", encoding="utf-8")
    return where


def _pr(verb: str, cwd: Path, pr: Path, branch: str = "feat/x y") -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(PR), verb, str(pr), branch], cwd=cwd, capture_output=True,
                          text=True, check=False, env=_ENV)


def _meta(co: Path) -> str:
    return (co / "mutants" / "a.py.meta").read_text(encoding="utf-8")


def test_a_saved_state_is_restored_by_the_next_push() -> None:
    if not shutil.which("git") or not shutil.which("flock"):
        print("SKIP: git or flock not found")
        return
    with tempfile.TemporaryDirectory() as td:
        pr = Path(td) / "pr"
        pr.mkdir()
        first = _checkout(Path(td) / "push1", "judged by push 1")
        p = _pr("save", first, pr)
        check("save: exit 0, saying so", (p.returncode, p.stdout),
              (0, "pr-mutmut-state: kept feat/x y's state against v-1 for its next push\n"))
        check("  as the branch's entry for the state version, its unsafe characters as _",
              sorted(e.name for e in pr.iterdir()), ["feat_x_y@v-1"])
        check_true("  and no baseline.json left in the checkout", not (first / "baseline.json").exists())
        second = _checkout(Path(td) / "push2", "the state's own", baseline="STATE")
        p = _pr("restore", second, pr)
        check("restore: exit 0, saying so", (p.returncode, p.stdout),
              (0, ("pr-mutmut-state: restored feat/x y's own state against v-1: "
                   "only what changed since its last run is judged\n")))
        check("  the kept mutants/ in place of the state's", _meta(second), "judged by push 1")
        check("  the state's baseline and version kept",
              ((second / "mutmut-baseline.json").read_text(encoding="utf-8"),
               (second / "mutmut-state-version").read_text(encoding="utf-8")), ("STATE", "v-1\n"))
        check("  and no stray file from the copy", sorted(p.name for p in second.iterdir()),
              ["mutants", "mutmut-baseline.json", "mutmut-state-version"])


def test_another_state_version_starts_from_the_state() -> None:
    if not shutil.which("git") or not shutil.which("flock"):
        print("SKIP: git or flock not found")
        return
    with tempfile.TemporaryDirectory() as td:
        pr = Path(td) / "pr"
        pr.mkdir()
        _pr("save", _checkout(Path(td) / "old", "judged against v-1"), pr)
        after_nightly = _checkout(Path(td) / "new", "the new state's", version="v-2")
        p = _pr("restore", after_nightly, pr)
        check("an entry for the state before: not restored, saying so", (p.returncode, p.stdout, _meta(after_nightly)),
              (0, "pr-mutmut-state: nothing kept for feat/x y against state v-2: this push starts from the state\n",
               "the new state's"))
        _pr("save", _checkout(Path(td) / "other", "x", version="v-1"), pr, branch="other")
        stale = pr / "feat_x_y@v-1"
        old = time.time() - 181 * 60
        os.utime(stale, (old, old))
        p = _pr("save", after_nightly, pr)
        check("a save against v-2: the v-1 entry three hours old goes, the fresh one stays, its own is made",
              (p.returncode, sorted(e.name for e in pr.iterdir())), (0, ["feat_x_y@v-2", "other@v-1"]))


def test_what_cannot_be_kept_is_said_and_left() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        co = _checkout(Path(td) / "co", "the state's")
        p = _pr("restore", co, Path(td) / "not-mounted")
        check("no PR directory: exit 0, from the state, saying so", (p.returncode, p.stdout, _meta(co)),
              (0, f"pr-mutmut-state: no {Path(td) / 'not-mounted'} mounted: this push starts from the state v-1\n",
               "the state's"))
        if os.geteuid() != 0:
            pr = Path(td) / "ro"
            pr.mkdir()
            os.chmod(pr, 0o555)
            try:
                p = _pr("save", co, pr)
            finally:
                os.chmod(pr, 0o755)
            check("a PR directory that cannot be written: exit 0, nothing kept, saying so",
                  (p.returncode, p.stdout, list(pr.iterdir())),
                  (0, f"pr-mutmut-state: {pr} is not writable: nothing kept\n", []))
        bare = Path(td) / "bare"
        bare.mkdir()
        p = _pr("restore", bare, Path(td))
        check("no state copied: exit 2, saying what comes first", (p.returncode, p.stderr),
              (2, "pr-mutmut-state: no state copied here (fetch-mutmut-state.sh first)\n"))
        usage = "usage: pr-mutmut-state.sh restore|save PR_DIR BRANCH\n"
        for args in (["keep", str(Path(td)), "b"], ["save", "", "b"], ["save", str(Path(td)), ""], []):
            p = subprocess.run(["bash", str(PR), *args], cwd=co, capture_output=True, text=True, check=False)
            check(f"usage error {args}: exit 2", (p.returncode, p.stderr), (2, usage))


if __name__ == "__main__":
    sys.exit(run(globals(), "pr-mutmut-state.sh"))
