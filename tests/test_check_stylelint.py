#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_stylelint.py
check.py's stylelint arm reports stylelint's findings on real CSS, over files
written here.

jp_stylelint.mjs prints only the fields check.py reads: each result's source,
and each warning's line, column, severity, rule and text. stylelint 17's full
result objects hold a parser that refers back to itself, so printing them whole
throws, and every CSS file then reads as a check that did not run.

Skips, saying so, where node or JP_TOOLS' own stylelint is not installed:
nothing can run then, and a skip is not a pass.

    python3 tests/test_check_stylelint.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAD = "a {\n  color: #fff;\n  colr: red;\n}\n"
GOOD = "a {\n  color: #fff;\n}\n"

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


def stylelint(target: Path) -> tuple[str, list[str], str]:
    """(status, sorted rule names, the check's JSON for detail) of the stylelint arm."""
    r = subprocess.run([sys.executable, str(ROOT / "check.py"), str(target), "--tools", "stylelint"],
                       capture_output=True, text=True, check=False)
    try:
        doc = json.loads(r.stdout.splitlines()[0])
        arm = doc["checks"][0]
    except (ValueError, IndexError, KeyError):
        return "unreadable", [], r.stdout + r.stderr
    rules = sorted(str(i.get("rule", "")) for i in arm.get("issues", []))
    return str(arm.get("status")), rules, json.dumps(arm)[:600]


def main() -> int:
    if not shutil.which("node") or not (ROOT / "node_modules" / "stylelint").is_dir():
        print("SKIP: node or JP_TOOLS' own stylelint is not installed (run npm install in JP_TOOLS)")
        return 0
    print("stylelint on real CSS:")
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "bad.css"
        bad.write_text(BAD, encoding="utf-8")
        good = Path(td) / "good.css"
        good.write_text(GOOD, encoding="utf-8")
        status, rules, raw = stylelint(bad)
        check("a CSS file with an unknown property fails, naming the rule",
              status == "fail" and "property-no-unknown" in rules, raw)
        status, rules, raw = stylelint(good)
        check("CONTROL: a clean CSS file passes", status == "pass" and not rules, raw)
    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
