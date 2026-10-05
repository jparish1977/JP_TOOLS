#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_fix.py
fix.py: that it runs, which fixers it picks, and that ruff's fixed count is
measured, on a real ruff when installed.

    python tests/test_fix.py
"""

import json
import os
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


def ruff_test_extend_through_jp_tools_root() -> None:
    """A project ruff.toml extending JP_TOOLS through JP_TOOLS_ROOT, with no
    JP_TOOLS beside it (a git worktree's layout), still loads under fix.py,
    which sets the variable to its own checkout over whatever the caller has."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "ruff.toml").write_text(
            'extend = "${JP_TOOLS_ROOT:-../JP_TOOLS}/configs/ruff.toml"\n')
        f = Path(tmp) / "m.py"
        f.write_text("import os\nimport sys\n\nprint(sys.argv)\n")
        saved = os.environ.get("JP_TOOLS_ROOT")
        os.environ["JP_TOOLS_ROOT"] = str(Path(tmp) / "no-such-jp-tools")
        try:
            _, out = run_main(FIX.main, str(f))
        finally:
            if saved is None:
                del os.environ["JP_TOOLS_ROOT"]
            else:
                os.environ["JP_TOOLS_ROOT"] = saved
        check("one fixed through the extended config", json.loads(out)["results"][0]["fixed"], 1)


def ruff_test_ruff_that_cannot_run_is_an_error() -> None:
    """ruff that cannot load its config exits 2 with nothing on stdout. That is
    an error with ruff's reason, never "fixed" with a count of 0, and the file
    is left as it was. The control is ruff_test_fixed_is_measured: the same
    source with no project config fixes one."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "ruff.toml").write_text('extend = "no-such-dir/ruff.toml"\n')
        f = Path(tmp) / "m.py"
        src = "import os\nimport sys\n\nprint(sys.argv)\n"
        f.write_text(src)
        for mode in ((), ("--dry-run",)):
            _, out = run_main(FIX.main, str(f), *mode)
            result = json.loads(out)["results"][0]
            label = " ".join(mode) or "fix"
            check(f"{label}: status", result["status"], "error")
            check_true(f"{label}: no fixed count", "fixed" not in result)
            check_true(f"{label}: the note says ruff exited 2",
                       result.get("note", "").startswith("exit 2: "))
            check(f"{label}: the file is untouched", f.read_text(), src)


if __name__ == "__main__":
    tests = dict(globals())
    if shutil.which("ruff") is None:
        print("SKIP: ruff not installed; the fixer-selection checks run alone")
    else:
        tests.update({k.removeprefix("ruff_"): v for k, v in globals().items()
                      if k.startswith("ruff_test_")})
    sys.exit(run(tests, "fix"))
