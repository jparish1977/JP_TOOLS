#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_phpstan.py
Tests check.py's phpstan runner against a real phpstan.

No pytest, no dependencies, same as the rest of tests/.

WHAT THIS PINS DOWN
    phpstan keys its results BY FILENAME and its per-message objects carry no
    "file" field, so a runner that iterates the values alone throws away the
    only copy of the path, and every finding reports the target directory as
    its location. Nothing in the summary looks wrong, because the count is
    right.

    Also asserts the configured level is the one METHODOLOGY section 2.5
    states, 8: a lower level reports a fraction of the findings present.

Skips rather than fails when the environment cannot support it:
  - no php                     -> skip
  - no vendor/bin/phpstan      -> skip (run: composer install in JP_TOOLS)

    python tests/test_check_phpstan.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

import jpcheck_loader

ROOT = jpcheck_loader.ROOT
check = jpcheck_loader.load("php_tools")

# Two findings at level 8, neither of them at level 5: an unspecified iterable
# value type, and a property with no declared type.
PHP_AT_LEVEL_8 = """<?php
class Thing
{
    private $items;

    public function take(array $rows): void
    {
        $this->items = $rows;
    }
}
"""

fails = 0
checks = 0


def check_(what: str, ok: bool, detail: str = "") -> None:
    global fails, checks
    checks += 1
    if not ok:
        fails += 1
    print(f"  [{' ok ' if ok else 'FAIL'}] {what}")
    if not ok and detail:
        print(f"         {detail}")


def main() -> int:
    if not shutil.which("php"):
        print("SKIP: php not found")
        return 0
    if not (ROOT / "vendor" / "bin" / "phpstan").exists():
        print("SKIP: vendor/bin/phpstan not found, run composer install")
        return 0

    print("Configured level:")
    cfg = (ROOT / "configs" / "phpstan.neon").read_text(encoding="utf-8")
    check_("configs/phpstan.neon is at level 8, as METHODOLOGY 2.5 states",
           "level: 8" in cfg,
           "; ".join(ln.strip() for ln in cfg.splitlines() if "level" in ln))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        php_file = tmp / "Thing.php"
        php_file.write_text(PHP_AT_LEVEL_8, encoding="utf-8")

        result = check.run_phpstan(str(tmp))

        print("\nAgainst a directory holding one failing file:")
        check_("phpstan ran", result["status"] != "unavailable",
               str(result.get("note", "")))
        issues = result["issues"]
        check_("it found something to report", len(issues) > 0)

        # Each finding names its own file, not the directory passed in.
        named_dir = [i for i in issues if i["file"].rstrip("/") == str(tmp)]
        check_("no finding is attributed to the target directory",
               not named_dir,
               f"{len(named_dir)} of {len(issues)} named {tmp}")
        check_("every finding names the php file it is in",
               all(i["file"].endswith("Thing.php") for i in issues),
               str({i["file"] for i in issues}))
        check_("every finding carries a real line number",
               all(isinstance(i["line"], int) and i["line"] > 0 for i in issues),
               str([i["line"] for i in issues]))

    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
