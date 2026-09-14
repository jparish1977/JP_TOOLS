#!/usr/bin/env python3
"""
JP_TOOLS/fix-dashes.py
No NEW em-dashes: find them in the lines a commit adds, and fix those lines only.

Joe's rule (2026-04-17, in CLAUDE.md): no em-dashes, anywhere. Checking by
hand kept costing time: dynatext-tools held #34 for one, the section 10 draft
carried 42. Joe, 2026-09-14: "appeasing the fickle joe is only worth it if this
does the trcik, if it doesnt and it keeps wasting time then ill just drop the
damned ban". So the check is mechanical and the fix is one command.

ONLY ADDED LINES ARE TOUCHED. A dated record, a session report say, is
history and is not retrofitted, and text a commit did not write is not that
commit's business. The same shape as check.py's smells: new fails, existing is
left alone.

usage:
    fix-dashes.py --check   list em-dashes in the lines staged for commit;
                            exit 1 if there are any
    fix-dashes.py           rewrite those lines in the working tree ("--" for
                            each em-dash) and re-stage them

The pre-commit hook runs --check. JP_TOOLS_ALLOW_EMDASH=1 on a commit that
genuinely needs one lets it through, and the hook says so every time.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

DASH = chr(0x2014)  # built from its code point, so this file does not carry one
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def name_process() -> None:
    try:
        import ctypes
        ctypes.CDLL(None).prctl(15, b"fix-dashes", 0, 0, 0)
    except (OSError, AttributeError):
        pass


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


def added_dashes(diff: str) -> dict[str, list[int]]:
    """{path: [line numbers in the new file]} for ADDED lines holding an em-dash."""
    found: dict[str, list[int]] = {}
    path: str | None = None
    line = 0
    for row in diff.splitlines():
        if row.startswith("+++ "):
            path = row[6:] if row.startswith("+++ b/") else None
        elif (m := HUNK.match(row)) is not None:
            line = int(m.group(1))
        elif row.startswith("+") and path is not None:
            if DASH in row:
                found.setdefault(path, []).append(line)
            line += 1
    return found


def replace(text: str) -> str:
    return text.replace(f" {DASH} ", " -- ").replace(DASH, "--")


def fix_file(path: str, lines: list[int]) -> bool:
    """Rewrite the given lines of one file and re-stage it. False if refused."""
    if git("diff", "--quiet", "--", path).returncode != 0:
        print(f"  {path}: also has unstaged changes, so fix lines {lines} by hand")
        return False
    p = Path(path)
    rows = p.read_text(encoding="utf-8").splitlines(keepends=True)
    for n in lines:
        rows[n - 1] = replace(rows[n - 1])
    p.write_text("".join(rows), encoding="utf-8")
    git("add", "--", path)
    print(f"  {path}: fixed line(s) {lines}, re-staged")
    return True


def main() -> int:
    name_process()
    if {"-h", "--help"} & set(sys.argv[1:]):
        print(__doc__)
        return 0
    top = git("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        print("fix-dashes: not in a git repository")
        return 2
    os.chdir(top.stdout.strip())
    found = added_dashes(git("diff", "--cached", "-U0", "--no-color", "--no-ext-diff").stdout)
    if "--check" in sys.argv[1:]:
        for path, lines in sorted(found.items()):
            print("\n".join(f"  {path}:{n}" for n in lines))
        return 1 if found else 0
    if not found:
        print("fix-dashes: no em-dashes in the lines staged for commit")
        return 0
    results = [fix_file(path, lines) for path, lines in sorted(found.items())]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
