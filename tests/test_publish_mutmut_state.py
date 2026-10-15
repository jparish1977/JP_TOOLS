#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_publish_mutmut_state.py
ci-image/publish-mutmut-state.sh and fetch-mutmut-state.sh, run from
throwaway checkouts into a throwaway state directory. The rules they exist to
keep, with jobs running side by side:

    - A publish is a new version directory; STATE/current is a symlink to it,
      replaced in one rename. The version before stays; older ones go.
    - A publisher waits for another holding STATE/.lock: two never interleave.
    - mutants/'s .git link and anything a mutant wrote that git does not
      track stay out, whatever its name (spaces, brackets); mutmut's own
      .meta, .spans and mutmut-stats.json go in, and so does mutmut-gate's
      mutmut-gate.json. fetch says which version it copied.
    - Nothing in a published version is group- or world-writable.
    - A current left as a plain directory (the layout before versions) is
      moved aside and replaced.
    - Nothing to publish, or a STATE that cannot be written: exit 2, the
      state untouched. fetch copies current's version whole, or exits 2.
    - A state judged at an ancestor of current's commit publishes nothing,
      exit 0, saying so; a newer one, the same commit, one whose commit is
      unknown, and one whose checkout lacks current's commit (a rewritten
      history) publish, so the state can never be stuck.

    python3 tests/test_publish_mutmut_state.py
"""

import fcntl
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _harness import ROOT, check, check_true, run

PUBLISH = ROOT / "ci-image" / "publish-mutmut-state.sh"
FETCH = ROOT / "ci-image" / "fetch-mutmut-state.sh"
_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
STRAYS = ("stray.txt", "we ird [1].txt")


def _checkout(where: Path, baseline: str = "1") -> Path:
    """A checkout holding mutants/ (a git repo tracking a.py, with mutmut's
    files and strays a mutant wrote) and baseline.json."""
    mutants = where / "mutants"
    mutants.mkdir(parents=True)
    subprocess.run(["git", "-C", str(mutants), "init", "-q"], check=True, env=_ENV)
    (mutants / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(mutants), "add", "a.py"], check=True, env=_ENV)
    for name in ("a.py.meta", "a.py.spans", "mutmut-stats.json", "mutmut-gate.json", *STRAYS):
        (mutants / name).write_text("{}", encoding="utf-8")
    (where / "baseline.json").write_text(baseline, encoding="utf-8")
    os.chmod(mutants / "a.py", 0o666)
    return where


def _publish(cwd: Path, state: Path) -> subprocess.CompletedProcess[str]:
    """Under umask 0, as a CI job runs it."""
    return subprocess.run(["bash", "-c", 'umask 0; exec bash "$0" "$1"', str(PUBLISH), str(state)],
                          cwd=cwd, capture_output=True, text=True, check=False, env=_ENV)


def _files(top: Path) -> list[str]:
    return sorted(str(p.relative_to(top)) for p in top.rglob("*") if p.is_file())


def _versions(state: Path) -> list[str]:
    return sorted(p.name for p in state.iterdir() if p.name.startswith("v-"))


def test_publishes_a_version_and_points_current_at_it() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "state"
        state.mkdir()
        p = _publish(_checkout(Path(td) / "co1"), state)
        check("exit 0", p.returncode, 0)
        current = state / "current"
        check_true("current is a symlink to the version", current.is_symlink()
                   and os.readlink(current) == _versions(state)[0])
        check("it holds mutmut's files, the gate's and the baseline, no stray, no .git",
              _files(current.resolve()),
              ["baseline.json", "mutants/a.py", "mutants/a.py.meta", "mutants/a.py.spans",
               "mutants/mutmut-gate.json", "mutants/mutmut-stats.json"])
        check_true("  nothing in it group- or world-writable",
                   all(not (q.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
                       for q in current.resolve().rglob("*")))
        check_true("  nor the lock file", not ((state / ".lock").stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH)))
        check_true("  and it says what it left out", "left out, written by mutants: 2 path(s)" in p.stdout)
        for n in (2, 3):
            time.sleep(1.1)  # versions are named to the second
            _publish(_checkout(Path(td) / f"co{n}", baseline=str(n)), state)
        check("after three: current and the one before are kept", len(_versions(state)), 2)
        check("  and current holds the last", (current / "baseline.json").read_text(encoding="utf-8"), "3")


def test_a_plain_directory_current_is_replaced() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "state"
        (state / "current").mkdir(parents=True)
        (state / "current" / "old-file").write_text("old", encoding="utf-8")
        check("exit 0", _publish(_checkout(Path(td) / "co"), state).returncode, 0)
        check_true("current is now a symlink", (state / "current").is_symlink())
        check("  and the old layout is kept as the version before",
              _versions(state)[0], "v-0-before-versions")


def test_a_publisher_waits_for_the_lock() -> None:
    if not shutil.which("git") or not shutil.which("flock"):
        print("SKIP: git or flock not found")
        return
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "state"
        state.mkdir()
        here = _checkout(Path(td) / "co")
        with open(state / ".lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            p = subprocess.Popen(["bash", str(PUBLISH), str(state)], cwd=here, env=_ENV,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.5)
            check("while another holds the lock, nothing is published",
                  (p.poll(), (state / "current").exists()), (None, False))
            fcntl.flock(held, fcntl.LOCK_UN)
        check("released: it publishes", p.wait(timeout=30), 0)
        check_true("  current now there", (state / "current").is_symlink())


def test_a_copy_and_a_publish_wait_for_each_other() -> None:
    if not shutil.which("git") or not shutil.which("flock"):
        print("SKIP: git or flock not found")
        return
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "state"
        state.mkdir()
        here = _checkout(Path(td) / "co")
        _publish(here, state)
        os.chmod(state / ".lock", 0o444)  # as a read-only mount shows it
        out = Path(td) / "out"
        out.mkdir()
        with open(state / ".lock") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            p = subprocess.Popen(["bash", str(FETCH), str(state)], cwd=out, env=_ENV,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.5)
            check("while a publish holds the lock, a copy waits",
                  (p.poll(), (out / "mutants").exists()), (None, False))
            fcntl.flock(held, fcntl.LOCK_UN)
        check("released: the copy finishes", p.wait(timeout=30), 0)
        os.chmod(state / ".lock", 0o644)
        with open(state / ".lock") as held:
            fcntl.flock(held, fcntl.LOCK_SH)
            p = subprocess.Popen(["bash", str(PUBLISH), str(state)], cwd=here, env=_ENV,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.5)
            check("while a copy holds the lock shared, a publish waits",
                  (p.poll(), len(_versions(state))), (None, 1))
            fcntl.flock(held, fcntl.LOCK_UN)
        check("released: it publishes", p.wait(timeout=30), 0)


def test_refusals_leave_the_state() -> None:
    with tempfile.TemporaryDirectory() as td:
        empty = Path(td) / "empty"
        empty.mkdir()
        state = Path(td) / "state"
        state.mkdir()
        check("nothing to publish: exit 2", _publish(empty, state).returncode, 2)
        check("  and the state is untouched", sorted(p.name for p in state.iterdir()), [])
        if os.geteuid() != 0 and shutil.which("git"):
            here = _checkout(Path(td) / "co")
            os.chmod(state, 0o555)
            try:
                p = _publish(here, state)
            finally:
                os.chmod(state, 0o755)
            check("a state that cannot be written: exit 2", p.returncode, 2)


def _repo_with_two_commits(where: Path) -> tuple[str, str]:
    """WHERE as a git repo with commits A then B; their ids."""
    where.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(where), "init", "-q"], check=True, env=_ENV)
    ids = []
    for n in ("a", "b"):
        (where / "f").write_text(n, encoding="utf-8")
        subprocess.run(["git", "-C", str(where), "add", "f"], check=True, env=_ENV)
        subprocess.run(["git", "-C", str(where), "commit", "-q", "--no-verify", "-m", n], check=True, env=_ENV)
        ids.append(subprocess.run(["git", "-C", str(where), "rev-parse", "HEAD"], capture_output=True,
                                  text=True, check=True, env=_ENV).stdout.strip())
    return ids[0], ids[1]


def _judged(checkout: Path, commit: str, baseline: str) -> Path:
    """CHECKOUT's state, judged at COMMIT, with BASELINE as its baseline."""
    (checkout / "mutants" / "mutmut-gate.json").write_text(f'{{"commit": "{commit}"}}', encoding="utf-8")
    (checkout / "baseline.json").write_text(baseline, encoding="utf-8")
    return checkout


def test_an_older_state_never_replaces_a_newer_one() -> None:
    """Two publishes from close pushes can finish out of order: one judged at
    an older commit than current's publishes nothing."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        co = Path(td) / "co"
        a, b = _repo_with_two_commits(co)
        _checkout(co)
        state = Path(td) / "state"
        state.mkdir()
        check("a state judged at B, the newer: published",
              (_publish(_judged(co, b, "B"), state).returncode, len(_versions(state))), (0, 1))
        p = _publish(_judged(co, a, "A"), state)
        check("then one judged at A, its ancestor: exit 0, nothing published, current still B",
              (p.returncode, (state / "current" / "baseline.json").read_text(encoding="utf-8"), len(_versions(state))),
              (0, "B", 1))
        check_true("  and it says why", f"current was judged at {b}, newer than this state's {a}: nothing published"
                   in p.stdout)
        unknown = "0" * 40
        (state / "current" / "mutants" / "mutmut-gate.json").chmod(0o644)
        (state / "current" / "mutants" / "mutmut-gate.json").write_text(f'{{"commit": "{unknown}"}}',
                                                                       encoding="utf-8")
        p = _publish(_judged(co, a, "A after a rewrite"), state)
        check("current judged at a commit this checkout does not have (a rewritten history): published, even"
              " an ancestor's state, so the state is never stuck",
              (p.returncode, (state / "current" / "baseline.json").read_text(encoding="utf-8")),
              (0, "A after a rewrite"))
    with tempfile.TemporaryDirectory() as td:
        co = Path(td) / "co"
        a, b = _repo_with_two_commits(co)
        _checkout(co)
        state = Path(td) / "state"
        state.mkdir()
        _publish(_judged(co, a, "A"), state)
        time.sleep(1.1)  # versions are named to the second
        _publish(_judged(co, b, "B"), state)
        check("current judged at A, a state judged at B, its descendant: published",
              (state / "current" / "baseline.json").read_text(encoding="utf-8"), "B")
        time.sleep(1.1)
        _publish(_judged(co, b, "B again"), state)
        check("  the same commit again: published", (state / "current" / "baseline.json").read_text(encoding="utf-8"),
              "B again")
        time.sleep(1.1)
        (co / "mutants" / "mutmut-gate.json").write_text("{}", encoding="utf-8")
        (co / "baseline.json").write_text("no commit", encoding="utf-8")
        _publish(co, state)
        check("  a state whose commit is unknown: published", (state / "current" / "baseline.json").read_text(
            encoding="utf-8"), "no commit")


def test_fetch_copies_the_current_version() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "state"
        state.mkdir()
        out = Path(td) / "out"
        out.mkdir()
        fetch = ["bash", str(FETCH), str(state)]
        check("no state: exit 2",
              subprocess.run(fetch, cwd=out, capture_output=True, check=False).returncode, 2)
        _publish(_checkout(Path(td) / "co", baseline="7"), state)
        p = subprocess.run(fetch, cwd=out, capture_output=True, text=True, check=False)
        check("a state: exit 0", p.returncode, 0)
        check("  the baseline, as mutmut-baseline.json",
              (out / "mutmut-baseline.json").read_text(encoding="utf-8"), "7")
        check_true("  and mutants/", (out / "mutants" / "a.py.meta").is_file())
        check("  and which version it copied", (out / "mutmut-state-version").read_text(encoding="utf-8"),
              _versions(state)[0] + "\n")


if __name__ == "__main__":
    sys.exit(run(globals(), "publish-mutmut-state.sh"))
