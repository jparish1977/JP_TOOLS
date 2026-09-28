#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_count_callers.py
count-callers.py: which lines it finds, how it classifies them, and which
directories it leaves out.

    python tests/test_count_callers.py
"""

import json
import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, load_script, run
from fake_tools import run_main

COUNT = load_script("count-callers.py")


def test_classify() -> None:
    for line, kind in (("function go(a) {", "DEF"), ("const go = () => 1;", "DEF"),
                       ("def go(x):", "DEF"), ('"go": function (x) {', "DEF"),
                       ("// go() is old", "COMMENT"), ("x.go(1);", "CALL"),
                       ("list = [go, stop];", "REF")):
        check(line, COUNT.classify(line, "go"), kind)


def test_excludes_below_the_root_only() -> None:
    """A project that itself sits under a directory named `build` is still searched."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "build" / "proj"
        (root / "src").mkdir(parents=True)
        (root / "node_modules" / "lib").mkdir(parents=True)
        (root / "src" / "a.js").write_text("function go() {}\ngo();\n")
        (root / "node_modules" / "lib" / "b.js").write_text("go();\n")
        (root / "src" / "c.py").write_text("go()\n")
        code, out = run_main(COUNT.main, "go", str(root), "--type", "js", "--json")
        check("exit", code, 0)
        check("hits", [(h["file"], h["line"], h["kind"]) for h in json.loads(out)],
              [("src/a.js", 1, "DEF"), ("src/a.js", 2, "CALL")])


def test_whole_words_and_report() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.js").write_text("go();\ngoing();\nrun(go);\n")
        code, out = run_main(COUNT.main, "go", tmp)
        check("exit", code, 0)
        check_true("counts", "Found: 0 definitions, 1 calls, 1 references, 0 comments" in out)
        check_true("listed", "a.js:3  run(go);" in out)


def test_not_a_directory() -> None:
    check("exit", run_main(COUNT.main, "go", "/nonexistent-dir")[0], 1)


if __name__ == "__main__":
    sys.exit(run(globals(), "count-callers"))
