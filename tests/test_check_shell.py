#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_shell.py
check.py's shell arm, backed by shellcheck. #57.

THE GAP THIS PINS DOWN
    check.py had no shell language. On a repo that is mostly shell the gate
    checked nothing, and said so only in the "skipped" list. Measured across
    the fleet on 2026-09-14: 261 shell files in 18 repos, none read by any gate.

WHAT A MISSING shellcheck MUST DO
    Fail the run as "did not run" (exit 2), naming shellcheck. Reporting
    "unavailable" and exiting 0 is the #29 shape. That case runs everywhere,
    with shellcheck removed from PATH; the finding cases need shellcheck and
    skip without it (joe-MacBookAir has none; iteration8 has 0.9.0).

    python3 tests/test_check_shell.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CHECK_PY = ROOT / "check.py"

# SC2164: `cd` without `|| exit` is a WARNING, so it blocks.
BLOCKING_SH = "#!/bin/sh\ncd /tmp\nls\n"
# SC2086: an unquoted $1 is INFO, so it is reported and does not block.
INFO_SH = "#!/bin/sh\necho $1\n"
CLEAN_SH = "#!/bin/sh\necho hello\n"

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


def run(*args: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    r = subprocess.run([sys.executable, str(CHECK_PY), *args], capture_output=True,
                       text=True, check=False, env=env)
    return r.returncode, r.stdout, r.stderr


def shellcheck_result(out: str) -> dict[str, Any]:
    try:
        doc = json.loads(out)
    except ValueError:
        return {}
    for c in doc.get("checks", []):
        if c.get("tool") == "shellcheck":
            return dict(c)
    return {}


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        script = d / "s.sh"
        script.write_text(BLOCKING_SH, encoding="utf-8")

        print("shellcheck missing from PATH:")
        # An EMPTY PATH. check.py runs by sys.executable's absolute path, so it
        # needs none, and shellcheck cannot be found. The first version used
        # python's own directory, which is /usr/bin on iteration8, where
        # shellcheck also lives: the "missing" case could only pass on a box
        # without shellcheck, where it proves nothing.
        empty = d / "empty-path"
        empty.mkdir()
        env = dict(os.environ, PATH=str(empty))
        rc, out, err = run(str(script), env=env)
        # The missing tool reports as "shellcheck (apt install shellcheck)", the
        # _tool_missing convention, so detection is asserted on the language.
        check("a .sh file is detected as shell even with no shellcheck",
              '"language": "shell"' in out, out[:300])
        check("... and the run exits 2, not 0: a check that did not run is not a pass",
              rc == 2, f"rc={rc}\n{out[:300]}\n{err[:300]}")
        check("... naming shellcheck", "shellcheck" in (out + err), out[:300])

        if not shutil.which("shellcheck"):
            print("\nSKIP the finding cases: shellcheck not on PATH "
                  f"({checks - fails}/{checks} passed above)")
            return 1 if fails else 0

        print("\nWith shellcheck:")
        rc, out, _ = run(str(script))
        sc = shellcheck_result(out)
        rules = [str(i.get("rule")) for i in sc.get("issues", [])]
        check("a WARNING-level finding (SC2164) fails the file, exit 1",
              rc == 1 and "SC2164" in rules, out[:400])

        info = d / "i.sh"
        info.write_text(INFO_SH, encoding="utf-8")
        rc, out, _ = run(str(info))
        sc = shellcheck_result(out)
        rules = [str(i.get("rule")) for i in sc.get("issues", [])]
        check("an INFO-level finding (SC2086) is reported but does not block, exit 0",
              rc == 0 and "SC2086" in rules, out[:400])

        clean = d / "c.sh"
        clean.write_text(CLEAN_SH, encoding="utf-8")
        rc, out, _ = run(str(clean))
        check("a clean script passes, exit 0", rc == 0, out[:300])

        tool = d / "tool"
        tool.write_text("#!/usr/bin/env bash\n" + BLOCKING_SH.split("\n", 1)[1],
                        encoding="utf-8")
        rc, out, _ = run(str(tool))
        check("an extensionless '#!/usr/bin/env bash' script is checked, exit 1",
              rc == 1 and shellcheck_result(out).get("tool") == "shellcheck", out[:400])

        repo = d / "repo"
        (repo / "bin").mkdir(parents=True)
        (repo / "ok.sh").write_text(CLEAN_SH, encoding="utf-8")
        (repo / "bin" / "run").write_text(BLOCKING_SH, encoding="utf-8")
        rc, out, _ = run(str(repo))
        try:
            langs = {s["language"]: s["file_count"] for s in json.loads(out)["languages"]}
        except (ValueError, KeyError):
            langs = {}
        check("directory mode: a dirty extensionless script fails the run, exit 1",
              rc == 1, out[:400])
        check("... and the shell section counts both scripts", langs.get("shell") == 2,
              str(langs))

    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
