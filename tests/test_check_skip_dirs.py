#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_skip_dirs.py
A tool that walks a directory itself is not handed vendor/ to walk.

No pytest, no dependencies, same as the rest of tests/.

WHAT THIS PINS DOWN
    phpstan, phpcs and rector were handed the directory under check whole,
    and analysed everything in it: on a PHP project with its dependencies
    installed, 1,622 vendor/ files against 53 of its own, 21,638 findings
    nearly all in vendor/, and a run of most of an hour. The gate's own file
    collection skipped vendor/ all along; these tools never saw that list.

    source_paths gives each tool the fewest paths that cover the project's
    files without entering a skipped directory, at any depth. phpstan is also
    given the project's own vendor/autoload.php, so the classes the code uses
    from its dependencies still resolve once vendor/ is not analysed.

    A fake php on PATH records what each runner would hand its tool.

    python3 tests/test_check_skip_dirs.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, run
from fake_tools import fake_tools
from jpcheck.files import source_paths
from jpcheck.php_tools import run_phpcs, run_phpstan, run_rector


def tree(root: Path, files: list[str]) -> None:
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text("<?php\n", encoding="utf-8")


def rel(root: Path, paths: list[str]) -> list[str]:
    return sorted(os.path.relpath(p, root) for p in paths)


def test_source_paths() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tree(root, ["src/A.php", "src/Sub/B.php", "tests/T.php", "bin.php", "README.md"])
        check("nothing skipped: the directory whole", rel(root, source_paths(td, (".php",))), ["."])
        tree(root, ["vendor/lib/X.php"])
        check("vendor/ at the top: its siblings, and only matching top-level files",
              rel(root, source_paths(td, (".php",))), ["bin.php", "src", "tests"])
        tree(root, ["src/Sub/node_modules/m.php"])
        check("a skipped directory deeper down: split down to it",
              rel(root, source_paths(td, (".php",))),
              ["bin.php", "src/A.php", "src/Sub/B.php", "tests"])
        f = str(root / "src" / "A.php")
        check("a file is itself", source_paths(f, (".php",)), [f])


def test_php_runners_skip_vendor() -> None:
    record = ('import json, os\n'
              'open(os.environ["FAKE_LOG"] + ".php", "a").write(json.dumps(sys.argv[1:]) + "\\n")\n'
              'sys.stdout.write("{}")\n')
    with fake_tools(keep_path=True, php=record) as (work, log):
        root = Path(work)
        tree(root, ["src/A.php", "vendor/lib/X.php"])
        (root / "vendor" / "autoload.php").write_text("<?php\n", encoding="utf-8")
        (root / ".git").mkdir()
        for runner in (run_phpstan, run_phpcs, run_rector):
            runner(str(root))
        calls = [json.loads(line) for line in Path(f"{log}.php").read_text().splitlines()]
    if len(calls) != 3:
        print(f"SKIP: the PHP tools are not installed in JP_TOOLS ({len(calls)} of 3 ran)")
        return
    for tool, argv in zip(("phpstan", "phpcs", "rector"), calls, strict=True):
        handed = [a for a in argv if a.startswith(str(root))]
        check_true(f"{tool}: nothing under vendor/ is handed over as a path to analyse",
                   not [a for a in handed if "/vendor/" in a and not a.endswith("autoload.php")])
        check_true(f"{tool}: nor the directory holding vendor/, whole", str(root) not in handed)
        check_true(f"{tool}: src is", str(root / "src") in handed)
    check_true("phpstan is given the project's autoloader",
               "--autoload-file" in calls[0]
               and calls[0][calls[0].index("--autoload-file") + 1] == str(root / "vendor" / "autoload.php"))


if __name__ == "__main__":
    sys.exit(run(globals(), "skip dirs"))
