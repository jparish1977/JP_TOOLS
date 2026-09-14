#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_lab_exempt.py
A lab file under its METHODOLOGY 2.9 header is listed, not counted. #68.

THE CONTRADICTION THIS PINS DOWN
    2.9 says a tools/lab instrument has no bar at entry: it owes a header
    (ANSWERS, TESTED AGAINST, NOT TESTED AGAINST), not zero findings. The gate
    held lab files to zero anyway, so editing one with an old finding blocked
    the commit. projectbook has 56 such files.

    The rule is projectbook's ratchet's, with one hole closed: "TESTED AGAINST"
    is a substring of "NOT TESTED AGAINST", so a header missing the
    tested-against field still passed a plain `in` test. That case is here.

Skips without ruff, which is what produces the finding.

    python3 tests/test_check_lab_exempt.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK_PY = ROOT / "check.py"

HEADER = '"""A probe.\n\nANSWERS: x.\nTESTED AGAINST: y.\nNOT TESTED AGAINST: z.\n"""\n'
HOLED = '"""A probe.\n\nANSWERS: x.\nNOT TESTED AGAINST: z.\n"""\n'
DIRTY = "import os\n"                       # F401

fails = 0
checks = 0


def check(what: str, ok: bool, detail: str = "") -> None:
    global fails, checks
    checks += 1
    if not ok:
        fails += 1
    print(f"  [{' ok ' if ok else 'FAIL'}] {what}")
    if not ok and detail:
        for line in detail.strip().splitlines()[:12]:
            print(f"         {line}")


def run(target: Path) -> tuple[int, dict[str, object]]:
    r = subprocess.run([sys.executable, str(CHECK_PY), str(target)],
                       capture_output=True, text=True, check=False)
    try:
        return r.returncode, json.loads(r.stdout)
    except ValueError:
        return r.returncode, {"raw": r.stdout + r.stderr}


def main() -> int:
    if not shutil.which("ruff"):
        print("SKIP: ruff not found, check.py cannot produce a finding")
        return 0

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        (repo / ".git").mkdir(parents=True)
        lab = repo / "tools" / "lab"
        lab.mkdir(parents=True)

        f = lab / "probe.py"
        f.write_text(HEADER + DIRTY, encoding="utf-8")
        rc, doc = run(f)
        summary = doc.get("summary", {})
        check("a lab file under its 2.9 header passes despite a finding, exit 0",
              rc == 0, json.dumps(doc)[:400])
        check("... its finding is still LISTED, marked exempt",
              isinstance(summary, dict) and summary.get("exempt", 0) >= 1
              and summary.get("errors") == 0, json.dumps(summary))

        f.write_text(HOLED + DIRTY, encoding="utf-8")
        rc, doc = run(f)
        check("a header missing TESTED AGAINST (only NOT TESTED AGAINST) is NOT exempt, exit 1",
              rc == 1, json.dumps(doc)[:400])

        f.write_text(DIRTY, encoding="utf-8")
        rc, doc = run(f)
        check("a lab file with no header is held to zero, exit 1", rc == 1,
              json.dumps(doc)[:400])

        outside = repo / "tools" / "not_lab.py"
        outside.write_text(HEADER + DIRTY, encoding="utf-8")
        rc, doc = run(outside)
        check("the header alone does not exempt a file outside lab/, exit 1", rc == 1,
              json.dumps(doc)[:400])

        # A repo that lives under a directory NAMED lab is not therefore a lab.
        nested = Path(td) / "lab" / "other"
        (nested / ".git").mkdir(parents=True)
        g = nested / "x.py"
        g.write_text(HEADER + DIRTY, encoding="utf-8")
        rc, doc = run(g)
        check("a repo under a directory called lab is not exempt, exit 1", rc == 1,
              json.dumps(doc)[:400])

        f.write_text(HEADER + DIRTY, encoding="utf-8")
        outside.unlink()
        rc, doc = run(repo)
        summary = doc.get("summary", {})
        check("directory mode: a tree whose only finding is an exempt lab file exits 0",
              rc == 0 and isinstance(summary, dict) and summary.get("exempt", 0) >= 1,
              json.dumps(summary))

    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
