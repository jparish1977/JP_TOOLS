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

from _harness import ROOT, check, check_true, run
from fake_tools import fake_tools
from jpcheck.files import _SKIP_DIRS, _collect_files, source_paths
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


def test_a_symlinked_directory_is_not_handed_over() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tree(root, ["src/A.php", "vendor/x/V.php"])
        (root / "src" / "lib").symlink_to(root / "vendor")
        check("src/ holding a link to vendor/: split, the link left out",
              rel(root, source_paths(str(root / "src"), (".php",))), ["src/A.php"])


def test_an_unreadable_directory_is_an_error_result() -> None:
    if os.geteuid() == 0:
        print("SKIP: root reads every directory, so none can be made unreadable")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tree(root, ["src/A.php", "locked/L.php"])
        (root / "locked").chmod(0)
        try:
            results = [r(td) for r in (run_phpstan, run_phpcs, run_rector)]
        finally:
            (root / "locked").chmod(0o755)
    ran = [r for r in results if r["status"] != "unavailable"]
    if not ran:
        print("SKIP: the PHP tools are not installed in JP_TOOLS")
        return
    check("an unreadable directory: an error result naming it, not a traceback",
          [(r["status"], "locked" in r.get("note", "")) for r in ran], [("error", True)] * len(ran))


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
        made = Path(f"{log}.php")
        calls = ([json.loads(line) for line in made.read_text().splitlines()]
                 if made.exists() else [])
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


# Written out here, not read from jpcheck.files._SKIP_DIRS: a test that reads
# the list it checks agrees with it whatever it says. #56 was a second
# assignment of that constant silently dropping .venv, venv, dist, build and
# .pytest_cache from the walk. The code's set is compared against this one,
# so a name added there, which would skip real source, fails too.
SKIPPED = ["node_modules", "vendor", "__pycache__", ".git", ".venv", "venv", "dist",
           "build", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".tox", "site-packages"]


def test_the_gate_skips_only_the_listed_directories() -> None:
    """The walk below shows each listed name is skipped; this shows nothing
    else is. Without it a name added to the code skips that directory's
    source silently, and README goes on listing thirteen."""
    check("the code skips exactly the listed directories", sorted(_SKIP_DIRS), sorted(SKIPPED))


def test_the_gate_walk_skips_every_listed_directory() -> None:
    """_collect_files, the gate's own walk, enters none of them, at the top or
    nested, and still collects the project's files beside them."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for d in SKIPPED:
            for at in (root / d, root / "pkg" / d):
                at.mkdir(parents=True, exist_ok=True)
                (at / "inside.py").write_text("x = 1\n", encoding="utf-8")
                (at / "inside.js").write_text("let x = 1;\n", encoding="utf-8")
        (root / "app.py").write_text("x = 1\n", encoding="utf-8")
        (root / "pkg" / "mod.js").write_text("let y = 2;\n", encoding="utf-8")
        groups, _ = _collect_files(td)
        found = sorted(os.path.relpath(f, root) for fs in groups.values() for f in fs)
        check("CONTROL: the project's own files are collected", found,
              ["app.py", os.path.join("pkg", "mod.js")])
        entered = sorted({Path(os.path.relpath(f, root)).parts[-2] for fs in groups.values()
                          for f in fs if "inside" in f})
        check("no skipped directory is entered", entered, [])


def test_the_readme_names_every_skipped_directory() -> None:
    """README's 'Skipped directories' line is the list an adopter reads; #56's
    bug went unnoticed partly because it named directories the code dropped."""
    line = next(ln for ln in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
                if ln.startswith("Skipped directories:"))
    named = sorted(w.strip("`, ") for w in line.split(":", 1)[1].split() if w.strip("`, "))
    check("README lists exactly the skipped directories", named, sorted(SKIPPED))


if __name__ == "__main__":
    sys.exit(run(globals(), "skip dirs"))
