#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_shell.py
check.py's shell arm, backed by shellcheck.

A .sh file, or an extensionless file whose #! names sh or bash, is shell.
A WARNING-level finding (or worse) fails the file; an INFO-level one is
reported and does not block.

WHAT A MISSING shellcheck MUST DO
    Fail the run as "did not run" (exit 2), naming shellcheck. Reporting
    "unavailable" and exiting 0 would be a check that did not run passing.
    That case runs everywhere, with an EMPTY PATH (check.py runs by
    sys.executable's absolute path, so it needs none; a PATH holding python's
    own directory can also hold shellcheck). The finding cases need
    shellcheck and print SKIP without it.

    python3 tests/test_check_shell.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, run

CHECK_PY = ROOT / "check.py"

# SC2164: `cd` without `|| exit` is a WARNING, so it blocks.
BLOCKING_SH = "#!/bin/sh\ncd /tmp\nls\n"
# SC2086: an unquoted $1 is INFO, so it is reported and does not block.
INFO_SH = "#!/bin/sh\necho $1\n"
CLEAN_SH = "#!/bin/sh\necho hello\n"


def check_py(*args: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    r = subprocess.run([sys.executable, str(CHECK_PY), *args], capture_output=True,
                       text=True, check=False, env=env)
    return r.returncode, r.stdout, r.stderr


def rules(out: str) -> list[str]:
    """The shellcheck rules in a check.py report, or [] when it is not one."""
    try:
        checks = json.loads(out).get("checks", [])
    except ValueError:
        # reason: not a check.py report: no rules, which fails the check that reads it
        return []
    issues: list[dict[str, object]] = next(
        (c.get("issues", []) for c in checks if c.get("tool") == "shellcheck"), [])
    return [str(i.get("rule")) for i in issues]


def _script(d: Path, name: str, text: str) -> str:
    (d / name).write_text(text, encoding="utf-8")
    return str(d / name)


def test_a_missing_shellcheck_is_a_run_that_did_not_happen() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "empty-path").mkdir()
        rc, out, err = check_py(_script(d, "s.sh", BLOCKING_SH),
                                env=dict(os.environ, PATH=str(d / "empty-path")))
        # The missing tool reports as "shellcheck (apt install shellcheck)",
        # so detection is asserted on the language.
        check_true("a .sh file is detected as shell with no shellcheck", '"language": "shell"' in out)
        check("the run exits 2, not 0", rc, 2)
        check_true("naming shellcheck", "shellcheck" in (out + err))


def shellcheck_test_levels() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        rc, out, _ = check_py(_script(d, "s.sh", BLOCKING_SH))
        check("a WARNING (SC2164) fails the file", (rc, "SC2164" in rules(out)), (1, True))
        rc, out, _ = check_py(_script(d, "i.sh", INFO_SH))
        check("an INFO (SC2086) is reported and does not block", (rc, "SC2086" in rules(out)), (0, True))
        check("a clean script passes", check_py(_script(d, "c.sh", CLEAN_SH))[0], 0)
        tool = _script(d, "tool", "#!/usr/bin/env bash\n" + BLOCKING_SH.split("\n", 1)[1])
        rc, out, _ = check_py(tool)
        check("an extensionless #!/usr/bin/env bash script is checked", (rc, "SC2164" in rules(out)),
              (1, True))


def shellcheck_test_directory_mode() -> None:
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        (repo / "bin").mkdir(parents=True)
        (repo / "ok.sh").write_text(CLEAN_SH, encoding="utf-8")
        (repo / "bin" / "run").write_text(BLOCKING_SH, encoding="utf-8")
        rc, out, _ = check_py(str(repo))
        try:
            langs = {s["language"]: s["file_count"] for s in json.loads(out)["languages"]}
        except (ValueError, KeyError):
            # reason: an unexpected report leaves langs empty, which fails the check below
            langs = {}
        check("a dirty extensionless script fails the run", rc, 1)
        check("the shell section counts both scripts", langs.get("shell"), 2)


if __name__ == "__main__":
    tests = dict(globals())
    if shutil.which("shellcheck") is None:
        print("SKIP: shellcheck not on PATH; the finding cases do not run")
    else:
        tests.update({k.removeprefix("shellcheck_"): v for k, v in globals().items()
                      if k.startswith("shellcheck_test_")})
    sys.exit(run(tests, "check.py shell"))
