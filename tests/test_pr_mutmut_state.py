#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_pr_mutmut_state.py
ci-image/pr-mutmut-state.sh, run from throwaway checkouts into a throwaway
PR directory: a pull request's own mutmut state, kept between its pushes.

    - save keeps the run's mutants/ as the branch's entry; restore in a
      later checkout puts it in place of the state's copy, keeping the
      state's baseline and version.
    - After the nightly publishes a new state version, the branch's entry is
      still restored, under the new baseline.
    - A save removes entries no push has saved for 14 days; newer ones stay.
    - No PR directory, nothing kept, a PR directory that cannot be written:
      said, exit 0, the checkout's state copy untouched.
    - No state copied, or a usage error: exit 2.
    - The branch's characters outside [A-Za-z0-9._-] are "_" in its entry,
      then its name's hash, so a/b and a_b keep apart; a plain name is kept.
    - A kept state that cannot be read leaves the state's copy in place.

    python3 tests/test_pr_mutmut_state.py
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _harness import ROOT, check, check_true, run

PR = ROOT / "ci-image" / "pr-mutmut-state.sh"
# The entry name of the branch "feat/x y": flattened, then its name's hash.
FEAT = "feat_x_y-" + hashlib.sha256(b"feat/x y").hexdigest()[:12]
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
              (0, "pr-mutmut-state: kept feat/x y's state for its next push\n"))
        check("  as the branch's entry, its unsafe characters as _ and its name's hash",
              sorted(e.name for e in pr.iterdir()), [FEAT])
        check_true("  and no baseline.json left in the checkout", not (first / "baseline.json").exists())
        second = _checkout(Path(td) / "push2", "the state's own", baseline="STATE")
        p = _pr("restore", second, pr)
        check("restore: exit 0, saying so", (p.returncode, p.stdout),
              (0, ("pr-mutmut-state: restored feat/x y's own state, baseline from the state v-1: "
                   "only what changed since its last run is judged\n")))
        check("  the kept mutants/ in place of the state's", _meta(second), "judged by push 1")
        check("  the state's baseline and version kept",
              ((second / "mutmut-baseline.json").read_text(encoding="utf-8"),
               (second / "mutmut-state-version").read_text(encoding="utf-8")), ("STATE", "v-1\n"))
        check("  and no stray file from the copy", sorted(p.name for p in second.iterdir()),
              ["mutants", "mutmut-baseline.json", "mutmut-state-version"])


def test_a_new_state_version_still_restores_the_branch_s_own() -> None:
    if not shutil.which("git") or not shutil.which("flock"):
        print("SKIP: git or flock not found")
        return
    with tempfile.TemporaryDirectory() as td:
        pr = Path(td) / "pr"
        pr.mkdir()
        _pr("save", _checkout(Path(td) / "old", "judged under v-1"), pr)
        after_nightly = _checkout(Path(td) / "new", "the new state's", version="v-2", baseline="NEW")
        p = _pr("restore", after_nightly, pr)
        check("after the nightly: the branch's own verdicts, saying so",
              (p.returncode, p.stdout, _meta(after_nightly)),
              (0, ("pr-mutmut-state: restored feat/x y's own state, baseline from the state v-2: "
                   "only what changed since its last run is judged\n"), "judged under v-1"))
        check("  the new state's baseline and version kept",
              ((after_nightly / "mutmut-baseline.json").read_text(encoding="utf-8"),
               (after_nightly / "mutmut-state-version").read_text(encoding="utf-8")), ("NEW", "v-2\n"))
        _pr("save", _checkout(Path(td) / "other", "x"), pr, branch="other")
        _pr("save", _checkout(Path(td) / "gone", "x"), pr, branch="gone")
        for name, days in (("other", 13), ("gone", 15)):
            old = time.time() - days * 86400
            os.utime(pr / name, (old, old))
        p = _pr("save", _checkout(Path(td) / "run2", "judged under v-2", version="v-2"), pr)
        check("a save: the entry 15 days unsaved goes, the one 13 days stays, its own is kept",
              (p.returncode, p.stdout, sorted(e.name for e in pr.iterdir())),
              (0, "pr-mutmut-state: kept feat/x y's state for its next push\n", [FEAT, "other"]))
        again = _checkout(Path(td) / "again", "the state's", version="v-2")
        _pr("restore", again, pr)
        check("  and what it kept is this run's", _meta(again), "judged under v-2")


def test_branches_the_flattening_makes_alike_keep_apart() -> None:
    if not shutil.which("git") or not shutil.which("flock"):
        print("SKIP: git or flock not found")
        return
    with tempfile.TemporaryDirectory() as td:
        pr = Path(td) / "pr"
        pr.mkdir()
        _pr("save", _checkout(Path(td) / "slash", "a/b's"), pr, branch="a/b")
        _pr("save", _checkout(Path(td) / "under", "a_b's"), pr, branch="a_b")
        check("a/b and a_b: two entries, the flattened one with its name's hash",
              sorted(e.name for e in pr.iterdir()),
              sorted(["a_b", "a_b-" + hashlib.sha256(b"a/b").hexdigest()[:12]]))
        back = _checkout(Path(td) / "back", "the state's")
        _pr("restore", back, pr, branch="a/b")
        check("  and each restores its own", _meta(back), "a/b's")


def test_a_kept_state_that_cannot_be_read_leaves_the_state_s() -> None:
    if not shutil.which("git") or not shutil.which("flock"):
        print("SKIP: git or flock not found")
        return
    with tempfile.TemporaryDirectory() as td:
        pr = Path(td) / "pr"
        pr.mkdir()
        _pr("save", _checkout(Path(td) / "push1", "judged by push 1"), pr)
        current = (pr / FEAT / "current").resolve()
        shutil.rmtree(current / "mutants")  # an entry broken after it was kept
        second = _checkout(Path(td) / "push2", "the state's own")
        p = _pr("restore", second, pr)
        check("restore: exit 0, the state's copy kept, saying so", (p.returncode, p.stdout, _meta(second)),
              (0, "pr-mutmut-state: could not read feat/x y's kept state: this push starts from the state v-1\n",
               "the state's own"))
        check("  and no stray file from the copy", sorted(p.name for p in second.iterdir()),
              ["mutants", "mutmut-baseline.json", "mutmut-state-version"])


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
