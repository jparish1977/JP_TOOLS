#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_fix.py
fix.py: that it runs, which fixers it picks, that ruff's fixed count is
measured, on a real ruff when installed, and that a fixer that did not run
exits 2.

    python tests/test_fix.py
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from _harness import check, check_true, load_script, run
from fake_tools import fake_tools
from jpexit import NOT_RUN, OK

FIX = load_script("fix.py")


def run_split(*args: str) -> tuple[int, dict[str, Any], str]:
    """(exit status, the JSON report on stdout, stderr) for fix.py ARGS."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = FIX.main(list(args))
    return code, json.loads(out.getvalue()), err.getvalue()


def test_language_detection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for name in ("a.py", "b.py", "c.php", "d.scss"):
            (d / name).write_text("")
        check("dir, most files", FIX._detect_lang(str(d)), "python")
        check("css file", FIX._detect_lang(str(d / "d.scss")), "css")
        check("unknown file", FIX._detect_lang(str(d / "nope.txt")), "unknown")


def test_unknown_language_runs_no_fixer() -> None:
    """No fixer applies, so none failed to run."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "notes.txt").write_text("x\n")
        code, report, err = run_split(str(Path(tmp) / "notes.txt"))
        check("exit", code, OK)
        check("no results", report["results"], [])
        check("nothing on stderr", err, "")


def test_a_fixer_not_installed_is_not_run() -> None:
    """A fixer that did not run is not a pass: NOT_RUN, named on stderr,
    and stdout is still the JSON report."""
    with fake_tools() as (work, _):
        f = work / "m.py"
        f.write_text("import os\n")
        code, report, err = run_split(str(f))
    check("not installed: status", report["results"][0]["status"], "unavailable")
    check("not installed: exit", code, NOT_RUN)
    check_true("not installed: stderr names ruff", "did not run ruff (unavailable)" in err)


def ruff_test_fixed_is_measured() -> None:
    """Also the control for the exit codes: a fixer that ran is OK."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "m.py"
        src = "import os\nimport sys\n\nprint(sys.argv)\n"
        f.write_text(src)
        code, report, _ = run_split(str(f), "--dry-run")
        check("dry run fixes nothing", report["results"][0]["fixed"], 0)
        check("dry run writes nothing", f.read_text(), src)
        check("a dry run that ran exits 0", code, OK)
        code, report, err = run_split(str(f))
        check("one fixed", report["results"][0]["fixed"], 1)
        check_true("the unused import is gone", "import os" not in f.read_text())
        check("a fix that ran exits 0", code, OK)
        check("a fix that ran says nothing on stderr", err, "")


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
            _, report, _ = run_split(str(f))
        finally:
            if saved is None:
                del os.environ["JP_TOOLS_ROOT"]
            else:
                os.environ["JP_TOOLS_ROOT"] = saved
        check("one fixed through the extended config", report["results"][0]["fixed"], 1)


def ruff_test_ruff_that_cannot_run_is_an_error() -> None:
    """ruff that cannot load its config exits 2 with nothing on stdout. That is
    an error with ruff's reason, never "fixed" with a count of 0, the file is
    left as it was, and fix.py exits 2 naming it. The control is
    ruff_test_fixed_is_measured: the same source with no project config fixes
    one and exits 0."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "ruff.toml").write_text('extend = "no-such-dir/ruff.toml"\n')
        f = Path(tmp) / "m.py"
        src = "import os\nimport sys\n\nprint(sys.argv)\n"
        f.write_text(src)
        for mode in ((), ("--dry-run",)):
            code, report, err = run_split(str(f), *mode)
            result = report["results"][0]
            label = " ".join(mode) or "fix"
            check(f"{label}: status", result["status"], "error")
            check_true(f"{label}: no fixed count", "fixed" not in result)
            check_true(f"{label}: the note says ruff exited 2",
                       result.get("note", "").startswith("exit 2: "))
            check(f"{label}: the file is untouched", f.read_text(), src)
            check(f"{label}: exit", code, NOT_RUN)
            check_true(f"{label}: stderr names ruff and why",
                       "did not run ruff (error): exit 2: " in err)


if __name__ == "__main__":
    tests = dict(globals())
    if shutil.which("ruff") is None:
        print("SKIP: ruff not installed; the fixer-selection checks run alone")
    else:
        tests.update({k.removeprefix("ruff_"): v for k, v in globals().items()
                      if k.startswith("ruff_test_")})
    sys.exit(run(tests, "fix"))
