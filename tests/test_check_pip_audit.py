#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_pip_audit.py
pip-audit audits the project's declared dependencies, or nothing.

No pytest, no dependencies, same as the rest of tests/.

WHAT THIS PINS DOWN
    With no requirements file, pip-audit was run with no target and audited
    the environment it ran in, which is the gate's own: a PHP project with one
    lab .py file failed on the gate venv's pip, reported as "(installed
    packages)", and the result depended on which box ran the gate.

    Now: a requirements file is audited with -r; else a pyproject.toml with a
    [project] table, as `pip-audit DIR`; else the check is a skip naming why
    (no manifest, a pyproject holding only tool config, a setup.py pip-audit
    cannot read, a single file that is not a requirements file). A fake
    pip-audit on PATH records what it was asked to audit.

    python3 tests/test_check_pip_audit.py
"""

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from _harness import check, check_true, run
from fake_tools import fake_tools
from jpcheck.python_tools import run_pip_audit

CLEAN = '{"dependencies": []}'
ONE_VULN = json.dumps({"dependencies": [
    {"name": "requests", "version": "2.19.0",
     "vulns": [{"id": "PYSEC-1", "description": "a flaw", "fix_versions": ["2.20.0"]}]}]})


def audit(files: dict[str, str], output: str = CLEAN, target: str = "") -> tuple[dict[str, Any], list[list[str]], Path]:
    """run_pip_audit on a project holding FILES, whose pip-audit prints OUTPUT:
    the result, every argv pip-audit got, and the project directory."""
    fake = f"sys.stdout.write({output!r})\n"
    with tempfile.TemporaryDirectory() as td, fake_tools(named={"pip-audit": fake}) as (_work, log):
        root = Path(td)
        for name, text in files.items():
            (root / name).write_text(text, encoding="utf-8")
        result = run_pip_audit(str(root / target) if target else str(root))
        calls = [json.loads(line)[1:] for line in log.read_text().splitlines()]
        return result, calls, root


def test_nothing_declared_is_a_skip_and_audits_nothing() -> None:
    for what, files in [
        ("no manifest at all", {"tool.py": "print(1)\n"}),
        ("a pyproject holding only tool config", {"pyproject.toml": "[tool.ruff]\nline-length = 100\n"}),
        ("a setup.py", {"setup.py": "from setuptools import setup\nsetup()\n"}),
    ]:
        result, calls, _ = audit(files)
        check(f"{what}: skip, and pip-audit never ran", (result["status"], calls), ("skip", []))
    result, _, _ = audit({"setup.py": "x\n"})
    check_true("the setup.py note says pip-audit cannot read one",
               "setup.py" in result.get("note", "") and "cannot" in result.get("note", ""))


def test_a_single_file_that_is_not_requirements_is_a_skip() -> None:
    result, calls, _ = audit({"tool.py": "print(1)\n"}, target="tool.py")
    check("a .py file target: skip, and pip-audit never ran", (result["status"], calls), ("skip", []))


def test_a_requirements_file_is_audited_with_r() -> None:
    result, calls, root = audit({"requirements.txt": "requests==2.19.0\n"})
    check("requirements.txt: pip-audit -r it", calls,
          [["--format", "json", "-r", str(root / "requirements.txt")]])
    check("and a clean audit passes", result["status"], "pass")
    result, calls, root = audit({"requirements.txt": "x\n",
                                 "pyproject.toml": "[project]\nname = 'd'\n"})
    check("a requirements file is preferred to a pyproject", calls,
          [["--format", "json", "-r", str(root / "requirements.txt")]])


def test_every_requirements_file_is_audited_and_names_its_findings() -> None:
    # Only the first file found was audited, so a repo's dev pins never were.
    # The fake reports a vulnerability for the dev file alone.
    fake = ('dev = any(a.endswith("requirements-dev.txt") for a in sys.argv)\n'
            f'sys.stdout.write({ONE_VULN!r} if dev else {CLEAN!r})\n')
    with tempfile.TemporaryDirectory() as td, fake_tools(named={"pip-audit": fake}) as (_work, log):
        root = Path(td)
        for name in ("requirements.txt", "requirements-dev.txt"):
            (root / name).write_text("x\n", encoding="utf-8")
        result = run_pip_audit(td)
        calls = [json.loads(line)[1:] for line in log.read_text().splitlines()]
    check("both requirements files are audited, one run each", calls,
          [["--format", "json", "-r", str(root / "requirements.txt")],
           ["--format", "json", "-r", str(root / "requirements-dev.txt")]])
    check("the dev file's finding names the dev file",
          (result["status"], [i["file"] for i in result["issues"]]),
          ("fail", [str(root / "requirements-dev.txt")]))


def test_a_pyproject_with_a_project_table_is_audited_as_the_directory() -> None:
    files = {"pyproject.toml": "[project]\nname = 'd'\ndependencies = ['requests==2.19.0']\n"}
    result, calls, root = audit(files, output=ONE_VULN)
    check("pyproject with [project]: pip-audit DIR", calls, [["--format", "json", str(root)]])
    check("its findings are errors named for the pyproject",
          (result["status"], [(i["file"], i["rule"], i["severity"]) for i in result["issues"]]),
          ("fail", [(str(root / "pyproject.toml"), "PYSEC-1", "error")]))


def test_pip_audit_failing_is_an_error_saying_why() -> None:
    fake_stderr = 'sys.stderr.write("ERROR: pyproject file does not parse\\n")\n'
    with tempfile.TemporaryDirectory() as td, fake_tools(named={"pip-audit": fake_stderr}) as _:
        (Path(td) / "requirements.txt").write_text("x\n", encoding="utf-8")
        result = run_pip_audit(td)
    check("no JSON from pip-audit: an error result", result["status"], "error")
    check_true("whose note carries pip-audit's own reason",
               "does not parse" in result.get("note", ""))


if __name__ == "__main__":
    sys.exit(run(globals(), "pip-audit"))
