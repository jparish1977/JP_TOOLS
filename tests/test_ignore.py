#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_ignore.py
check.py leaves out what a repo's .jp-tools-ignore names, and says so (#90).

A scratch git repository holds a vendor copy under oob/ and a file of its own
under src/, each with a real unused import (ruff F401), and a .jp-tools-ignore
naming oob/. The file the pre-commit hook would pass is checked on its own, the
directory is checked with and without --tools, and a baseline is recorded.

Skips, saying so, where ruff is not installed: nothing can run then, and a
skip is not a pass.

    python3 tests/test_ignore.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REASON = "byte-exact vendor pages kept as provenance"
UNUSED = "import os\n"

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


def repo(td: str, ignore: str | None) -> Path:
    r = Path(td) / "repo"
    for rel in ("oob/page.py", "oob/deep/copy.py", "src/own.py"):
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(UNUSED, encoding="utf-8")
    if ignore is not None:
        (r / ".jp-tools-ignore").write_text(ignore, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    return r


def run(*args: str, cwd: Path) -> tuple[int, dict[str, Any], str]:
    """(exit, the first JSON document printed, all output) of check.py."""
    p = subprocess.run([sys.executable, str(ROOT / "check.py"), *args], cwd=cwd,
                       capture_output=True, text=True, check=False)
    doc: dict[str, Any] = {}
    try:
        doc = json.loads(p.stdout)
    except ValueError:
        for line in p.stdout.splitlines():
            try:
                doc = json.loads(line)
                break
            except ValueError:
                continue
    return p.returncode, doc, p.stdout + p.stderr


def files_with_issues(doc: dict[str, Any]) -> set[str]:
    return {Path(i.get("file", "")).name for c in doc.get("checks", []) for i in c.get("issues", [])}


def single_file(r: Path) -> None:
    print("the file the hook passes:")
    rc, doc, out = run(str(r / "oob/page.py"), "--tools", "ruff", cwd=r)
    check("an ignored file is skipped, exit 0, naming the entry and its reason",
          rc == 0 and doc.get("skipped") == f"ignored by .jp-tools-ignore: oob/* ({REASON})", out)
    rc, doc, out = run(str(r / "oob/deep/copy.py"), "--tools", "ruff", cwd=r)
    check("the glob's * crosses directories", rc == 0 and "skipped" in doc, out)
    rc, doc, out = run(str(r / "src/own.py"), "--tools", "ruff", cwd=r)
    check("CONTROL: a file the repo owns is still checked and fails", rc == 1 and "own.py" in files_with_issues(doc), out)


def directory(r: Path) -> None:
    print("the directory:")
    rc, doc, out = run(str(r), cwd=r)
    ignored = {e.get("file"): e for e in doc.get("ignored", [])}
    check("dir mode lists each ignored file with its entry",
          set(ignored) == {"oob/page.py", "oob/deep/copy.py"}
          and ignored.get("oob/page.py", {}).get("reason") == REASON, out[-800:])
    found = files_with_issues(doc)
    check("dir mode drops the dir-capable tools' findings in ignored files",
          "page.py" not in found and "copy.py" not in found, str(found))
    check("CONTROL: and keeps the owned file's", "own.py" in found and rc == 1, str(found))
    rc, doc, out = run(str(r), "--tools", "ruff", cwd=r)
    found = files_with_issues(doc)
    check("a directory named with --tools drops them too",
          "page.py" not in found and "own.py" in found, str(found))


def only_vendor(td: str) -> None:
    r = repo(td, f"oob/*  # {REASON}\n")
    (r / "src/own.py").write_text("print('clean')\n", encoding="utf-8")
    rc, doc, out = run(str(r), "--tools", "ruff", cwd=r)
    ruff: dict[str, Any] = next((c for c in doc.get("checks", []) if c.get("tool") == "ruff"), {})
    check("with only ignored files at fault, ruff reads pass and the run exits 0",
          rc == 0 and ruff.get("status") == "pass" and not ruff.get("issues"), out[-600:])


def refusals(td: str) -> None:
    print("the file itself:")
    r = repo(td, f"# vendor content\n\noob/*  # {REASON}\nsrc/*\n")
    rc, doc, out = run(str(r / "src/own.py"), "--tools", "ruff", cwd=r)
    check("an entry with no reason refuses, exit 2, naming its line",
          rc == 2 and ".jp-tools-ignore:4:" in doc.get("error", "") and "reason" in doc.get("error", ""), out)


def baseline(r: Path, td: str) -> None:
    dest = Path(td) / "baseline.json"
    rc, _doc, out = run(str(r), "--record-baseline", str(dest), cwd=r)
    try:
        files = json.loads(dest.read_text(encoding="utf-8")).get("files", {})
    except (OSError, ValueError):
        files = {}
    check("a recorded baseline leaves ignored files out",
          rc == 0 and "src/own.py" in files and not any(f.startswith("oob/") for f in files),
          f"{rc} {sorted(files)}\n{out[-400:]}")


def none(td: str) -> None:
    r = repo(td, None)
    rc, doc, out = run(str(r / "oob/page.py"), "--tools", "ruff", cwd=r)
    check("CONTROL: with no .jp-tools-ignore, a vendor copy is checked like any file",
          rc == 1 and "page.py" in files_with_issues(doc), out)


def main() -> int:
    if not shutil.which("ruff"):
        print("SKIP: ruff is not installed, so nothing here can run")
        return 0
    with tempfile.TemporaryDirectory() as td:
        r = repo(td, f"# vendor content\n\noob/*  # {REASON}\n")
        single_file(r)
        directory(r)
        baseline(r, td)
    for fn in (only_vendor, refusals, none):
        with tempfile.TemporaryDirectory() as td:
            fn(td)
    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
