"""Runners for C/C++ (cppcheck) and shell (shellcheck)."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from jpcheck.common import _plain_env, _status, _tool_missing, _tool_result, strip_ansi

# Arduino sketches use ArduinoJson's `variant | fallback` operator, which reads
# as a bitwise-or on an integer unless the analyser can see the overload. It
# cannot: cppcheck's preprocessor fails on ArduinoJson's version-namespace
# macros with "Invalid ## usage", and a failed parse yields an EMPTY report that
# looks exactly like a clean one. Without the header, cppcheck reports false
# badBitmaskCheck findings but still catches real out-of-bounds writes; with
# the header it catches nothing at all.
#
# So the headers are deliberately NOT supplied, and this one rule is suppressed
# for sketches instead. Do not "improve" this by adding -I paths: that silently
# disables the whole analysis. g++ parses the same header fine, so this is a
# cppcheck limitation, not a property of the code.
_INO_SUPPRESS = ["badBitmaskCheck"]

_CPPCHECK_SUPPRESS = [
    "missingInclude",        # Arduino core headers are not present by design
    "missingIncludeSystem",
    "toomanyconfigs",        # informational, not a finding
    "unusedFunction",        # setup()/loop() and ISRs are called by the core
]


def run_cppcheck(target: str) -> dict[str, Any]:
    """Static analysis for C/C++ and Arduino sketches.

    This is the linter slot, not the strongest check available. For firmware the
    real gate is a compile against the actual toolchain (`arduino-cli compile`),
    which resolves library overloads properly. cppcheck is what runs anywhere
    without hundreds of megabytes of board support installed.
    """
    if not shutil.which("cppcheck"):
        return _tool_missing("cppcheck", "apt install cppcheck (requirements-system.txt)")
    suppress = list(_CPPCHECK_SUPPRESS)
    lang_args = []
    if Path(target).suffix.lower() == ".ino":
        suppress += _INO_SUPPRESS
        # Required, not cosmetic: cppcheck does not know the .ino extension, and
        # without this it reports a syntaxError at the first sketch construct and
        # analyses nothing. Not forced for .c/.cpp/.h, where cppcheck
        # infers correctly and forcing C++ on a C file would be wrong.
        lang_args = ["--language=c++"]
    args = [
        "cppcheck", *lang_args, "--enable=warning,style,performance,portability",
        "--inline-suppr", "--quiet",
        "--template={file}|{line}|{column}|{severity}|{id}|{message}",
        *[f"--suppress={s}" for s in suppress],
        target,
    ]
    result = subprocess.run(args, capture_output=True, text=True, check=False, env=_plain_env())
    issues = []
    # cppcheck writes findings to stderr, not stdout.
    for line in strip_ansi(result.stderr).splitlines():
        parts = line.split("|", 5)
        if len(parts) < 6:
            continue
        fname, lineno, col, severity, rule, message = parts
        issues.append({
            "file":     fname,
            "line":     int(lineno) if lineno.isdigit() else 0,
            "col":      int(col) if col.isdigit() else 0,
            "severity": "error" if severity in ("error", "warning") else "warning",
            "rule":     rule,
            "message":  message,
            "fixable":  False,
            "fixable_unsafe": False,
        })
    return {"tool": "cppcheck", "status": _status(issues), "issues": issues}


def run_shellcheck(target: str) -> dict[str, Any]:
    """Shell scripts, by shellcheck.

    Exit codes are READ, not discarded: 0 clean, 1 findings, 2 or more means it
    could not run (a bad argument, an unreadable file), and that must not read
    as a pass. Levels map like cppcheck's: error and warning block; info and
    style are reported as warnings. tools/lab/shellcheck-sweep.py measures what
    the arm finds across a set of repos.
    """
    if not shutil.which("shellcheck"):
        return _tool_missing("shellcheck", "apt install shellcheck (requirements-system.txt)")
    result = subprocess.run(["shellcheck", "--format=json1", target],
                            capture_output=True, text=True, check=False,
                            env=_plain_env())
    issues: list[dict[str, Any]] = []
    try:
        comments = json.loads(result.stdout or "{}").get("comments", [])
    except ValueError:
        return {"tool": "shellcheck", "status": "error", "issues": [],
                "note": f"shellcheck output was not JSON (exit {result.returncode})"}
    for c in comments:
        issues.append({
            "file":     c.get("file", target),
            "line":     int(c.get("line", 0)),
            "col":      int(c.get("column", 0)),
            "severity": "error" if c.get("level") in ("error", "warning") else "warning",
            "rule":     f"SC{c.get('code', '')}",
            "message":  c.get("message", ""),
            "fixable":  False,
            "fixable_unsafe": False,
        })
    return _tool_result("shellcheck", issues, result)
