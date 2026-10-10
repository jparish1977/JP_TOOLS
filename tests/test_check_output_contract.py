#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_output_contract.py
docs/check-output.md is the contract other repositories read check.py's JSON
by. This holds it to the code, so a status or an exit rule cannot change
without the page changing in the same pull request.

WHAT IS CHECKED
    - The page's status table, class by class, equals _VERDICTS, _RAN and
      _NOT_RUN in jpcheck/common.py.
    - Every status literal written in check.py and jpcheck/ is in that table.
    - The page's exit-code table names jpexit's codes.
    - The exit-code rules, through jpcheck.registry._exit_code: warn and skip
      exit 0, every not-run status exits 2, so does a status the page does not
      list, and findings win over not-run.
    - The three documents from real runs: a missing path ({"error"}, exit 2),
      a README with --skip-unsupported ({"target", "skipped"}, exit 0), and a
      file with findings, whose every issue carries a severity.
      The last needs ruff; SKIP, said, without it.
    - Every check carries tool, status and issues, a check that did not run
      included, and a directory run lists a tool once per language.

    python3 tests/test_check_output_contract.py
"""

import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, run
from jpcheck.common import _NOT_RUN, _RAN, _VERDICTS
from jpcheck.registry import _exit_code
from jpexit import FINDINGS, NOT_LOWERED, NOT_RUN, OK

DOC = ROOT / "docs" / "check-output.md"
CLASS_ROW = re.compile(r"^\| `([a-z_]+)` \| (verdict|ran|not run) \|", re.M)
EXIT_ROW = re.compile(r"^\| (\d) \| `([A-Z_]+)` \|", re.M)
STATUS_LITERAL = re.compile(r"""["']status["']\]?\s*[:=]\s*["']([a-z_]+)["']""")


def documented() -> dict[str, list[str]]:
    """{class: [status, ...]} from the page's status table, in table order."""
    classes: dict[str, list[str]] = {"verdict": [], "ran": [], "not run": []}
    for status, cls in CLASS_ROW.findall(DOC.read_text(encoding="utf-8")):
        classes[cls].append(status)
    return classes


def test_the_status_table_is_the_code() -> None:
    doc = documented()
    check("the page's verdicts, mildest first, are _VERDICTS", tuple(doc["verdict"]), _VERDICTS)
    check("the page's ran statuses are _RAN", (*doc["verdict"], *doc["ran"]), _RAN)
    check("the page's not-run statuses are _NOT_RUN", tuple(doc["not run"]), _NOT_RUN)


def test_every_status_the_code_writes_is_documented() -> None:
    known = set(_RAN) | set(_NOT_RUN)
    sources = [ROOT / "check.py", *sorted((ROOT / "jpcheck").glob("*.py"))]
    written = {(p.name, s) for p in sources for s in STATUS_LITERAL.findall(p.read_text(encoding="utf-8"))}
    check_true("the scan finds the statuses the code writes (control: error, skip, unavailable)",
               {"error", "skip", "unavailable"} <= {s for _, s in written})
    check("every status literal in check.py and jpcheck/ is on the page",
          sorted(f"{name}: {s}" for name, s in written if s not in known), [])


def test_the_exit_table_is_jpexit() -> None:
    rows = {int(code): name for code, name in EXIT_ROW.findall(DOC.read_text(encoding="utf-8"))}
    check("the page's exit codes are jpexit's", rows,
          {OK: "OK", FINDINGS: "FINDINGS", NOT_RUN: "NOT_RUN", NOT_LOWERED: "NOT_LOWERED"})


def exit_for(statuses: list[str], errors: int = 0) -> int:
    checks = [{"tool": f"t{i}", "status": s, "issues": []} for i, s in enumerate(statuses)]
    with contextlib.redirect_stderr(io.StringIO()):
        return _exit_code({"errors": errors}, checks)


def test_the_exit_rules() -> None:
    check("pass, warn and skip: exit", exit_for(["pass", "warn", "skip"]), OK)
    for status in _NOT_RUN:
        check(f"{status}: exit", exit_for(["pass", status]), NOT_RUN)
    check("a status the page does not list: exit", exit_for(["pass", "partial"]), NOT_RUN)
    check("findings and a check that did not run: findings win, exit",
          exit_for(["fail", "unavailable"], errors=1), FINDINGS)


def check_py(*args: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(ROOT / "check.py"), *args],
                       capture_output=True, text=True, check=False, timeout=300)
    return r.returncode, r.stdout


def test_the_documents() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        code, out = check_py(str(d / "missing.py"))
        check("a missing path: exit, and one key, error", (code, sorted(json.loads(out))), (NOT_RUN, ["error"]))
        (d / "README.md").write_text("# notes\n", encoding="utf-8")
        code, out = check_py(str(d / "README.md"), "--skip-unsupported")
        check("a README with --skip-unsupported: exit, and its document",
              (code, json.loads(out)), (OK, {"target": str((d / "README.md").resolve()),
                                             "skipped": "no language detected"}))
        if shutil.which("ruff") is None:
            print("SKIP: a file with findings: ruff is not installed")
            return
        (d / "dirty.py").write_text("import os\n", encoding="utf-8")
        code, out = check_py(str(d / "dirty.py"))
        doc = json.loads(out)
        issues = [i for c in doc["checks"] for i in c["issues"]]
        check("a file with findings: exit, and the document's keys",
              (code, sorted(doc)), (FINDINGS, ["checks", "language", "summary", "target"]))
        check_true("a file with findings: it has issues to look at", bool(issues))
        check("a file with findings: every issue's severity is error or warning",
              sorted({i.get("severity", "(none)") for i in issues} - {"error", "warning"}), [])


def test_every_check_names_its_tool() -> None:
    """tool, status and issues on every check, whether or not it ran; a
    directory run lists a tool once per language it serves."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "a.py").write_text("x = 1\n", encoding="utf-8")
        (d / "b.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        _, out = check_py(str(d))
        doc = json.loads(out)
        missing = [c.get("tool", "?") for c in doc["checks"] if not {"tool", "status", "issues"} <= set(c)]
        check("a directory run: every check has tool, status and issues", missing, [])
        check("a directory run: smells once per language it serves (python, shell)",
              [c["tool"] for c in doc["checks"]].count("smells"), 2)
        _, out = check_py(str(d / "a.py"), "--tools", "no-such-tool")
        only = json.loads(out)["checks"]
        check("a check that did not run still names its tool, with a note",
              [(c["tool"], c["status"], "note" in c) for c in only], [("no-such-tool", "unknown", True)])


if __name__ == "__main__":
    sys.exit(run(globals(), "check output contract"))
