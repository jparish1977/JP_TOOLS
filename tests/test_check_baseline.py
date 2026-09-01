#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_baseline.py
Drives `check.py --record-baseline` and `check.py --baseline` against real
throwaway git repos and real ruff runs. No pytest, no dependencies -- the
toolbox has none and this is not going to add the first.

THE BUG THIS PINS DOWN
    A gate with no baseline compares every staged file against ZERO, so on an
    inherited repo the on-ramp is still a wall: you meet the bar in one move or
    you do not commit. METHODOLOGY, "Adopting this in a codebase you inherited",
    step 2, said to record one and compare against it. Nothing did.

    Worse, the recording half was ALSO wrong, and in a way that reads as a tool
    problem rather than a method problem. Step 2 said to commit "the output of a
    full run", which everyone reads as the whole repo, while step 1 had already
    made the gate check staged files one at a time. Measured on batocera-watch
    2026-08-15: 2472 errors recorded against 2870 measured, +398 across seven
    files whose code had not changed in a week.

    The signature is what makes it diagnosable. Only the tools that resolve
    ACROSS files moved -- mypy and phpstan. ruff, phpcs and rector judge a file
    in isolation and were byte-identical on every single file. Scope, not drift.

    So mode is recorded, and a mismatch is refused rather than compared. A
    ratchet that reports scope as regression is worse than no ratchet: it
    accuses you of breaking seven files you never opened.

Skips rather than fails when the environment cannot support it:
  - no git   -> skip
  - no ruff  -> skip (nothing can produce a countable finding)

    python tests/test_check_baseline.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT  = Path(__file__).resolve().parent.parent
CHECK = ROOT / "check.py"

DIRTY = "import os\nimport sys\nx = 1\n"       # ruff: unused imports
WORSE = "import os\nimport sys\nimport json\nx = 1\n"
CLEAN = "x = 1\n"

fails = 0
checks = 0


def check(what: str, ok: bool, detail: str = "") -> None:
    global fails, checks
    checks += 1
    if not ok:
        fails += 1
    print(f"  [{' ok ' if ok else 'FAIL'}] {what}")
    if not ok and detail:
        print(f"         {detail}")


def run(*args: str, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), *args],
                          capture_output=True, text=True, check=False, cwd=cwd)


def make_repo(tmp: str) -> str:
    repo = Path(tmp) / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=str(repo), check=True)
    (repo / "dirty.py").write_text(DIRTY, encoding="utf-8")
    (repo / "clean.py").write_text(CLEAN, encoding="utf-8")
    return str(repo)


def main() -> int:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return 0
    if not shutil.which("ruff"):
        print("SKIP: ruff not found -- nothing can produce a countable finding")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        base = str(Path(repo) / "base.json")

        print("recording")
        r = run(".", "--record-baseline", base, cwd=repo)
        check("record exits 0", r.returncode == 0, r.stderr)
        doc = json.loads(Path(base).read_text(encoding="utf-8"))

        # The whole point. A baseline that does not say how it was measured
        # cannot be compared safely, because the two modes disagree by
        # hundreds of findings on identical code.
        check("records mode=per-file", doc.get("mode") == "per-file",
              f"got {doc.get('mode')!r}")
        check("records the tool versions it used",
              isinstance(doc.get("tools"), dict) and "ruff" in doc["tools"])
        check("records per-tool status, so a tool that did not run stays visible",
              isinstance(doc.get("status"), dict))
        check("keys files relative to the repo root, not absolutely",
              "dirty.py" in doc.get("files", {}),
              f"got keys {list(doc.get('files', {}))}")
        check("a file with no findings is simply absent",
              "clean.py" not in doc.get("files", {}))

        print("the three acceptance cases")
        # 1. An edit that leaves the count unchanged must commit.
        (Path(repo) / "dirty.py").write_text(DIRTY + "z = 3\n", encoding="utf-8")
        r = run("dirty.py", "--baseline", base, cwd=repo)
        check("unchanged count exits 0", r.returncode == 0, r.stderr)

        # 2. An edit that raises it is refused, naming the file and BOTH numbers.
        (Path(repo) / "dirty.py").write_text(WORSE, encoding="utf-8")
        r = run("dirty.py", "--baseline", base, cwd=repo)
        check("a raised count exits 1", r.returncode == 1)
        check("the refusal names the file", "dirty.py" in r.stderr, r.stderr)
        check("the refusal names both numbers", "3 -> 4" in r.stderr, r.stderr)

        # 3. An edit that lowers it commits, and the gain is visible.
        (Path(repo) / "dirty.py").write_text(CLEAN, encoding="utf-8")
        r = run("dirty.py", "--baseline", base, cwd=repo)
        check("a lowered count exits 0", r.returncode == 0, r.stderr)
        check("the gain is reported, not silently accepted",
              "-3" in r.stdout, r.stdout)

        print("mode enforcement")
        (Path(repo) / "dirty.py").write_text(DIRTY, encoding="utf-8")
        r = run(".", "--baseline", base, cwd=repo)
        check("a whole-repo run against a per-file baseline REFUSES",
              r.returncode == 2, f"exit {r.returncode}")
        check("and says which two modes disagree",
              "per-file" in r.stderr and "whole-repo" in r.stderr, r.stderr)

        print("a tool that cannot be compared is never counted as zero")
        doc = json.loads(Path(base).read_text(encoding="utf-8"))
        doc["tools"]["ruff"] = "0.0.1-not-a-real-version"
        Path(base).write_text(json.dumps(doc), encoding="utf-8")
        (Path(repo) / "dirty.py").write_text(WORSE, encoding="utf-8")
        r = run("dirty.py", "--baseline", base, cwd=repo)
        # Genuinely worse, but under a different ruff -- so the honest answer is
        # "cannot tell", not "regression" and not "pass".
        check("a version mismatch excludes that tool instead of failing",
              r.returncode == 0, f"exit {r.returncode}")
        check("and says so out loud rather than silently passing",
              "cannot compare ruff" in r.stderr, r.stderr)

        # Reported by batocera-watch reviewing PR #32. Recording rebuilds the
        # whole document, so two per-file invocations at one path kept only the
        # second -- and BOTH printed "recorded 1 file(s)". The loss surfaced a
        # step later as the first file failing for having no recorded count,
        # which reads as an unrelated bug. Pinning the refusal AND the
        # untouched file, because a guard that refuses after corrupting the
        # destination has not helped anyone.
        print("recording a second file must not silently discard the first")
        narrow = str(Path(repo) / "narrow.json")
        r = run("dirty.py", "--record-baseline", narrow, cwd=repo)
        check("recording one file exits 0", r.returncode == 0, r.stderr)
        r = run("clean.py", "--record-baseline", narrow, cwd=repo)
        check("recording a second file over it REFUSES", r.returncode == 2,
              f"exit {r.returncode}: {r.stdout}{r.stderr}")
        check("  and names the file that would be lost",
              "dirty.py" in r.stderr, r.stderr)
        check("  and points at the whole-directory path that works",
              "--record-baseline" in r.stderr and "<dir>" in r.stderr, r.stderr)
        doc = json.loads(Path(narrow).read_text(encoding="utf-8"))
        check("  and leaves the existing baseline intact",
              "dirty.py" in doc.get("files", {}), sorted(doc.get("files", {})))

        print("the loss is allowed when it is asked for, and said out loud")
        r = run("clean.py", "--record-baseline", narrow, "--force-baseline",
                cwd=repo)
        check("--force-baseline writes it", r.returncode == 0, r.stderr)
        check("  and reports what it discarded rather than just what it wrote",
              "discarding" in r.stdout, r.stdout)
        doc = json.loads(Path(narrow).read_text(encoding="utf-8"))
        check("  and the discard really happened",
              "dirty.py" not in doc.get("files", {}), sorted(doc.get("files", {})))

        print("widening an existing baseline is not a discard and is not refused")
        r = run(".", "--record-baseline", narrow, cwd=repo)
        check("recording the whole tree over a narrower baseline exits 0",
              r.returncode == 0, r.stderr)
        check("  and says what it replaced, which the old message never did",
              "replaced a baseline of" in r.stdout, r.stdout)

        # Raised by batocera-watch re-reviewing the guard above. "Cannot parse"
        # was folded into "does not exist", so a corrupt baseline -- a
        # truncated write, conflict markers, a half-synced copy -- was
        # overwritten at exit 0 with nothing said. It is the one case where the
        # prior numbers cannot be reconstructed, which makes it the worst one
        # to discard quietly.
        print("an unparseable baseline is not an absent one")
        broken = str(Path(repo) / "broken.json")
        Path(broken).write_text("{ this is not json", encoding="utf-8")
        r = run("dirty.py", "--record-baseline", broken, cwd=repo)
        check("recording over an unparseable baseline REFUSES",
              r.returncode == 2, f"exit {r.returncode}: {r.stdout}{r.stderr}")
        check("  and says it cannot tell what would be lost",
              "cannot be read as a baseline" in r.stderr, r.stderr)
        check("  and leaves the unreadable file untouched for a human",
              Path(broken).read_text(encoding="utf-8") == "{ this is not json",
              Path(broken).read_text(encoding="utf-8")[:60])
        r = run("dirty.py", "--record-baseline", broken, "--force-baseline",
                cwd=repo)
        check("  --force-baseline still overwrites it deliberately",
              r.returncode == 0, r.stderr)
        check("  and the overwrite really happened",
              "dirty.py" in json.loads(Path(broken).read_text(encoding="utf-8"))
              .get("files", {}))

        print("valid JSON that is not a baseline is also refused")
        alien = str(Path(repo) / "alien.json")
        Path(alien).write_text('{"something": "else"}', encoding="utf-8")
        r = run("dirty.py", "--record-baseline", alien, cwd=repo)
        check("a parseable non-baseline is refused too", r.returncode == 2,
              f"exit {r.returncode}: {r.stdout}{r.stderr}")

        print("a repo with no baseline behaves exactly as before")
        (Path(repo) / "dirty.py").write_text(DIRTY, encoding="utf-8")
        r = run("dirty.py", cwd=repo)
        check("dirty file with no --baseline still exits 1", r.returncode == 1)
        r = run("clean.py", cwd=repo)
        check("clean file with no --baseline still exits 0", r.returncode == 0,
              r.stderr)

    print()
    print(f"{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
