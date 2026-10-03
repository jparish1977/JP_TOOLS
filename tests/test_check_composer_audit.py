#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_composer_audit.py
Tests check.py's composer audit runner against each shape composer prints.

No pytest, no dependencies, same as the rest of tests/.

WHAT THIS PINS DOWN
    With no advisories, composer prints "advisories": [] -- PHP's json_encode
    of an empty array is a list, not an object. With advisories it is an
    object keyed by package. The runner must read the empty list as a pass,
    read the object as findings, and report any other shape as an error
    result rather than a traceback.

    "abandoned" has the same two shapes, and maps a package to its suggested
    replacement or null. An abandoned package is a warning: reported, and
    check.py still exits 0 when it is the only finding.

    A fake composer on PATH stands in for the real one, so every shape is
    tested on every box whatever composer and advisory database it has.

    python3 tests/test_check_composer_audit.py
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from _harness import ROOT, check, check_true, run
from fake_tools import fake_tools
from jpcheck.php_tools import _composer_issues, run_composer_audit

EMPTY = '{\n    "advisories": [],\n    "abandoned": []\n}\n'
NO_KEY = '{"abandoned": []}'
ONE_ADVISORY = json.dumps({"advisories": {"vendor/pkg": [
    {"advisoryId": "PKSA-1", "title": "XSS in render", "cve": "CVE-2024-0001"}]}})
# A non-sequential PHP array: the package's advisories as an object keyed by index.
KEYED_ADVISORY = json.dumps({"advisories": {"vendor/pkg": {
    "1": {"advisoryId": "PKSA-2", "title": "SQL injection", "cve": None}}}})
BAD_SHAPES = {
    "a top-level list":                   "[]",
    "advisories as a string":             '{"advisories": "none"}',
    "a package's advisories as a string": '{"advisories": {"a/b": "x"}}',
    "an advisory that is not an object":  '{"advisories": {"a/b": ["x"]}}',
    "not JSON":                           "Composer could not find a composer.lock\n",
    "abandoned as a string":              '{"advisories": [], "abandoned": "none"}',
    "a replacement that is not a name":   '{"advisories": [], "abandoned": {"a/b": 1}}',
}
ABANDONED = json.dumps({"advisories": [],
                        "abandoned": {"old/pkg": "new/pkg", "dead/pkg": None}})


def audit(stdout: str) -> dict[str, Any]:
    """run_composer_audit on a project whose composer prints STDOUT."""
    with fake_tools(composer=f"sys.stdout.write({stdout!r})\n") as (work, _log):
        (Path(work) / "composer.json").write_text("{}", encoding="utf-8")
        return run_composer_audit(str(work))


def gate_exit(stdout: str) -> int:
    """check.py's exit code auditing a project whose composer prints STDOUT."""
    with fake_tools(composer=f"sys.stdout.write({stdout!r})\n") as (work, _log):
        (Path(work) / "composer.json").write_text("{}", encoding="utf-8")
        return subprocess.run([sys.executable, str(ROOT / "check.py"), str(work),
                               "--tools", "composer-audit"],
                              capture_output=True, text=True, check=False).returncode


def test_parse() -> None:
    check("an empty advisories list is no findings", _composer_issues(EMPTY), [])
    check("no advisories key is no findings", _composer_issues(NO_KEY), [])
    one = _composer_issues(ONE_ADVISORY)
    check("one advisory, its rule and message",
          [(i["rule"], i["message"]) for i in one],
          [("CVE-2024-0001", "vendor/pkg: XSS in render")])
    keyed = _composer_issues(KEYED_ADVISORY)
    check("advisories keyed by index, named by id with no CVE",
          [(i["rule"], i["message"]) for i in keyed],
          [("PKSA-2", "vendor/pkg: SQL injection")])
    abandoned = _composer_issues(ABANDONED)
    check("each abandoned package is a warning, naming any replacement",
          [(i["severity"], i["rule"], i["message"]) for i in abandoned],
          [("warning", "abandoned", "old/pkg is abandoned; use new/pkg instead"),
           ("warning", "abandoned", "dead/pkg is abandoned")])


def test_abandoned_warns_without_failing_the_gate() -> None:
    check("an abandoned package alone: check.py exits 0", gate_exit(ABANDONED), 0)
    check("control, an advisory: check.py exits 1", gate_exit(ONE_ADVISORY), 1)


def test_runner_clean_and_found() -> None:
    r = audit(EMPTY)
    check("no advisories passes", (r["status"], r["issues"]), ("pass", []))
    r = audit(ONE_ADVISORY)
    check("an advisory fails with one finding", (r["status"], len(r["issues"])), ("fail", 1))


def test_runner_bad_shapes() -> None:
    for what, out in BAD_SHAPES.items():
        r = audit(out)
        check(f"{what}: an error result, not a traceback", r["status"], "error")
        check_true(f"{what}: the note says the JSON was not as expected",
                   "not the JSON expected" in str(r.get("note", "")))


if __name__ == "__main__":
    sys.exit(run(globals(), "composer audit"))
