#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_native_shellcheck.py
jpcheck.native_tools.run_shellcheck, in this process, against a fake shellcheck
whose output and exit code each case sets.

WHAT IS CHECKED
    - No shellcheck on PATH: the check did not run, saying how to install it.
    - It is run as `shellcheck --format=json1 TARGET`, in an environment with
      colour forcing removed (FORCE_COLOR gone, NO_COLOR=1, TERM=dumb), and
      with exactly subprocess.run's keyword arguments, check=False included.
    - Each comment becomes an issue: error and warning levels block, info and
      style warn; SC and the code as the rule; a field shellcheck leaves out
      reads as the target, 0, or "", never a crash.
    - Exit 0 with no comments is a pass, exit 1 with comments a fail, and exit
      2 or more an error whose note quotes stderr, never a pass; output that
      is not JSON is an error naming the exit code.
    Run in this process, so mutation testing sees what it reaches: the shell
    suites drive check.py as a child process, which it cannot.

    python3 tests/test_native_shellcheck.py
"""

import json
import os
import shutil
import subprocess
import sys
from typing import Any
from unittest import mock

from _harness import check, run
from fake_tools import calls, env, fake_tools
from jpcheck import native_tools
from jpcheck.common import _plain_env

# The fake: record argv and the colour variables it was given, then answer as
# the case says.
FAKE = """\
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({k: os.environ.get(k) for k in ("FORCE_COLOR", "NO_COLOR", "TERM")}) + "\\n")
sys.stdout.write(os.environ.get("FAKE_STDOUT", ""))
sys.stderr.write(os.environ.get("FAKE_STDERR", ""))
sys.exit(int(os.environ.get("FAKE_RC", "0")))
"""


def shellcheck(stdout: str, rc: int = 0, stderr: str = "") -> tuple[dict[str, Any], list[Any]]:
    """run_shellcheck("s.sh") against the fake: (its result, what the fake logged)."""
    with fake_tools(shellcheck=FAKE) as (_, log), \
            env(FAKE_STDOUT=stdout, FAKE_STDERR=stderr, FAKE_RC=str(rc), FORCE_COLOR="1"):
        result = native_tools.run_shellcheck("s.sh")
        logged = calls(log)
    return result, logged


def comments(*found: dict[str, Any]) -> str:
    return json.dumps({"comments": list(found)})


def issue(file: str, line: int, col: int, severity: str, rule: str, message: str) -> dict[str, Any]:
    return {"file": file, "line": line, "col": col, "severity": severity, "rule": rule,
            "message": message, "fixable": False, "fixable_unsafe": False}


def test_no_shellcheck_is_a_check_that_did_not_run() -> None:
    with fake_tools() as _:
        check("no shellcheck on PATH", native_tools.run_shellcheck("s.sh"),
              {"tool": "shellcheck", "status": "unavailable", "issues": [],
               "note": "'shellcheck' not found: apt install shellcheck (requirements-system.txt)"})


def test_how_it_is_run() -> None:
    _, logged = shellcheck("{}")
    check("argv, then the colour variables it was given", logged,
          [["shellcheck", "--format=json1", "s.sh"], {"FORCE_COLOR": None, "NO_COLOR": "1", "TERM": "dumb"}])


def test_the_exact_call() -> None:
    """subprocess.run's exact keyword arguments, check=False included: the
    fake shellcheck cannot tell check=False from check=None or no check at
    all, so the call is recorded rather than run."""
    ran: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[str]":
        ran.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    with mock.patch.object(shutil, "which", lambda name: f"/fake/{name}"), \
            mock.patch.object(subprocess, "run", fake_run), env(FORCE_COLOR="1"):
        native_tools.run_shellcheck("s.sh")
        plain = _plain_env()
    check("one call: the command, then exactly these keyword arguments", ran,
          [(["shellcheck", "--format=json1", "s.sh"],
            {"capture_output": True, "text": True, "check": False, "env": plain})])


def test_each_comment_is_an_issue() -> None:
    found = comments(
        {"file": "s.sh", "line": 3, "column": 5, "level": "error", "code": 1009, "message": "parse"},
        {"file": "s.sh", "line": 4, "column": 1, "level": "warning", "code": 2034, "message": "unused"},
        {"file": "t.sh", "line": 7, "column": 2, "level": "info", "code": 2086, "message": "quote"},
        {"file": "s.sh", "line": 9, "column": 9, "level": "style", "code": 2001, "message": "sed"})
    result, _ = shellcheck(found, rc=1)
    check("exit 1 with comments: a fail, every comment an issue, levels mapped", result,
          {"tool": "shellcheck", "status": "fail", "issues": [
              issue("s.sh", 3, 5, "error", "SC1009", "parse"),
              issue("s.sh", 4, 1, "error", "SC2034", "unused"),
              issue("t.sh", 7, 2, "warning", "SC2086", "quote"),
              issue("s.sh", 9, 9, "warning", "SC2001", "sed")]})


def test_a_comment_missing_its_fields() -> None:
    result, _ = shellcheck(comments({}), rc=1)
    check("every field missing: the target, 0, a bare SC, an empty message, and it warns",
          result["issues"], [issue("s.sh", 0, 0, "warning", "SC", "")])


def test_a_clean_run_and_one_that_could_not_run() -> None:
    check("exit 0, no comments: a pass", shellcheck("{}")[0],
          {"tool": "shellcheck", "status": "pass", "issues": []})
    check("exit 0, no output at all: a pass", shellcheck("")[0],
          {"tool": "shellcheck", "status": "pass", "issues": []})
    check("exit 2: an error whose note quotes stderr's error line",
          shellcheck("{}", rc=2, stderr="s.sh: openBinaryFile: does not exist\nerror: cannot read s.sh\n")[0],
          {"tool": "shellcheck", "status": "error", "issues": [], "note": "exit 2: error: cannot read s.sh"})
    check("output that is not JSON: an error naming the exit code", shellcheck("not json", rc=3)[0],
          {"tool": "shellcheck", "status": "error", "issues": [],
           "note": "shellcheck output was not JSON (exit 3)"})


if __name__ == "__main__":
    os.environ.pop("FORCE_COLOR", None)
    sys.exit(run(globals(), "run_shellcheck"))
