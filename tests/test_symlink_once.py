#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_symlink_once.py
A file reached through symlinks is measured once (#99).

A repo-mode baseline used to follow every symlink to a file and key each
link's findings to the file it resolves to, so a file with N links was
recorded N+1 times too loose and the per-file hook compared its real count
against that. Found by sunblade2000: vm/sbxrun-reader.sh and four links,
shellcheck 15 in the baseline against 3 on the file alone.

A scratch git repository holds src/real.py with two unused imports (ruff
F401), two links to it inside the tree, one link to a file outside the tree,
and one broken link.

Skips, saying so, where ruff is not installed: nothing can run then, and a
skip is not a pass.

    python3 tests/test_symlink_once.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
UNUSED = "import os\nimport sys\n"
INSIDE = "(symlink inside the tree, measured at its target)"
OUTSIDE = "(symlink out of the tree, not this repo's file)"
BROKEN = "(broken symlink)"

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


def repo(td: str) -> Path:
    r = Path(td) / "repo"
    (r / "src").mkdir(parents=True)
    (r / "src/real.py").write_text(UNUSED, encoding="utf-8")
    os.symlink("real.py", r / "src/link1.py")
    os.symlink("real.py", r / "src/link2.py")
    outside = Path(td) / "elsewhere.py"
    outside.write_text(UNUSED, encoding="utf-8")
    os.symlink(outside, r / "src/away.py")
    os.symlink("missing.py", r / "src/dangling.py")
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    return r


def run(*args: str, cwd: Path) -> tuple[int, dict[str, Any], str]:
    """(exit, the JSON document printed, all output) of check.py."""
    p = subprocess.run([sys.executable, str(ROOT / "check.py"), *args], cwd=cwd,
                       capture_output=True, text=True, check=False)
    try:
        doc = json.loads(p.stdout)
    except ValueError:
        doc = {}
    return p.returncode, doc, p.stdout + p.stderr


def ruff_errors(doc: dict[str, Any]) -> int:
    return sum(len(c.get("issues", [])) for c in doc.get("checks", []) if c.get("tool") == "ruff")


def baseline(r: Path, td: str) -> None:
    print("the recorded baseline:")
    dest = Path(td) / "baseline.json"
    rc, _doc, out = run(str(r), "--record-baseline", str(dest), cwd=r)
    try:
        files = json.loads(dest.read_text(encoding="utf-8")).get("files", {})
    except (OSError, ValueError):
        files = {}
    _rc, single, _out = run(str(r / "src/real.py"), "--tools", "ruff", cwd=r)
    once = ruff_errors(single)
    recorded = files.get("src/real.py", {}).get("ruff", {}).get("errors")
    check("a file with two links inside the tree is recorded at its own count, not three times it",
          rc == 0 and once == 2 and recorded == once, f"recorded {recorded}, alone {once}\n{out[-400:]}")
    # Under any key: a path outside the repo is recorded as the link's own
    # absolute path, because it cannot be made relative to the repo root.
    check("the file a link points to out of the tree is not recorded",
          not any("away" in f or "elsewhere" in f or f.startswith(("/", "..")) for f in files),
          str(sorted(files)))


def directory(r: Path) -> None:
    print("the directory:")
    _rc, doc, _out = run(str(r), cwd=r)
    said = {e.get("extension"): e.get("file_count") for e in doc.get("skipped", [])}
    check("dir mode says it skipped the two links inside the tree", said.get(INSIDE) == 2, str(said))
    check("and the one out of the tree", said.get(OUTSIDE) == 1, str(said))
    check("and the broken one", said.get(BROKEN) == 1, str(said))


def single_link(r: Path) -> None:
    print("the hook's path:")
    rc, doc, out = run(str(r / "src/link1.py"), "--tools", "ruff", cwd=r)
    check("CONTROL: a link named on its own is still checked, at its target's findings",
          rc == 1 and ruff_errors(doc) == 2, out[-400:])


def main() -> int:
    if not shutil.which("ruff"):
        print("SKIP: ruff is not installed, so nothing here can run")
        return 0
    with tempfile.TemporaryDirectory() as td:
        r = repo(td)
        baseline(r, td)
        directory(r)
        single_link(r)
    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
