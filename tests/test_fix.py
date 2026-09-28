#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_fix.py
fix.py: that it runs, which fixers it picks, and that ruff's fixed count is
measured, on a real ruff when installed.

    python tests/test_fix.py
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, load_script, run
from fake_tools import run_main

FIX = load_script("fix.py")


def test_language_detection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for name in ("a.py", "b.py", "c.php", "d.scss"):
            (d / name).write_text("")
        check("dir, most files", FIX._detect_lang(str(d)), "python")
        check("css file", FIX._detect_lang(str(d / "d.scss")), "css")
        check("unknown file", FIX._detect_lang(str(d / "nope.txt")), "unknown")


def test_unknown_language_runs_no_fixer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "notes.txt").write_text("x\n")
        code, out = run_main(FIX.main, str(Path(tmp) / "notes.txt"))
        check("exit", code, 0)
        check("no results", json.loads(out)["results"], [])


def ruff_test_fixed_is_measured() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "m.py"
        src = "import os\nimport sys\n\nprint(sys.argv)\n"
        f.write_text(src)
        _, out = run_main(FIX.main, str(f), "--dry-run")
        check("dry run fixes nothing", json.loads(out)["results"][0]["fixed"], 0)
        check("dry run writes nothing", f.read_text(), src)
        _, out = run_main(FIX.main, str(f))
        result = json.loads(out)["results"][0]
        check("one fixed", result["fixed"], 1)
        check_true("the unused import is gone", "import os" not in f.read_text())


if __name__ == "__main__":
    tests = dict(globals())
    if shutil.which("ruff") is None:
        print("SKIP: ruff not installed; the fixer-selection checks run alone")
    else:
        tests.update({k.removeprefix("ruff_"): v for k, v in globals().items()
                      if k.startswith("ruff_test_")})
    sys.exit(run(tests, "fix"))
