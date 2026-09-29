#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_ci_template.py
The workflows are valid, and the CI template init-ci.py installs takes JP_TOOLS
from wherever the adopting forge says and needs no action a Forgejo runner lacks.

WHY
    The template cloned JP_TOOLS from a fixed GitHub URL, so a repo on another
    forge gated against the GitHub mirror however far it lagged (two weeks, at
    the time). It set PHP up with shivammathur/setup-php, which a Forgejo
    runner cannot resolve, so its PHP job could not start there. And nothing
    linted either workflow file: actionlint, run for this change, found an
    unquoted variable in the template that had been there all along.

WHAT IS CHECKED
    - actionlint (requirements-dev.txt) passes on .github/workflows/check.yml
      and templates/ci-check.yml. It is required, so its absence FAILS here.
    - Parsed with PyYAML: JP_TOOLS is checked out from vars.JP_TOOLS_REPOSITORY
      with the public GitHub copy as the default and secrets.JP_TOOLS_TOKEN as
      the token, then moved beside the checkout; no step clones JP_TOOLS from a
      fixed URL; every `uses:` is an actions/* action.
    - The template's text names no host or owner internal to one forge.
    Control: master's template before this change fails all of it.

    python3 tests/test_ci_template.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from _harness import ROOT, check, check_true, run

TEMPLATE = ROOT / "templates" / "ci-check.yml"
WORKFLOWS = (ROOT / ".github" / "workflows" / "check.yml", TEMPLATE)
REPOSITORY = "${{ vars.JP_TOOLS_REPOSITORY || 'jparish1977/JP_TOOLS' }}"
TOKEN = "${{ secrets.JP_TOOLS_TOKEN || github.token }}"
INTERNAL = ("iteration8", "ts.net", "joe/", ":3000", ":2222")
BEFORE = "9f948e2"      # master before the template took its source from a variable


def actionlint(path: Path) -> tuple[int, str]:
    exe = shutil.which("actionlint")
    if exe is None:
        return -1, "actionlint is not installed (pip install -r requirements-dev.txt)"
    r = subprocess.run([exe, str(path)], capture_output=True, text=True, check=False)
    return r.returncode, (r.stdout + r.stderr).strip()


def steps(text: str) -> list[dict[str, Any]]:
    doc = yaml.safe_load(text)
    return [s for job in doc.get("jobs", {}).values() for s in job.get("steps", [])]


def _checkout_findings(all_steps: list[dict[str, Any]]) -> list[str]:
    checkouts = [s for s in all_steps if s.get("with", {}).get("repository") == REPOSITORY]
    if not checkouts:
        return ["no checkout of vars.JP_TOOLS_REPOSITORY with the GitHub default"]
    if checkouts[0]["with"].get("token") != TOKEN:
        return ["the JP_TOOLS checkout does not use JP_TOOLS_TOKEN"]
    return []


def _run_findings(all_steps: list[dict[str, Any]]) -> list[str]:
    runs = [str(s.get("run", "")) for s in all_steps]
    wrong = []
    if not any("mv _jp_tools ../JP_TOOLS" in r for r in runs):
        wrong.append("JP_TOOLS is not moved beside the checkout")
    if any("git clone" in r and "JP_TOOLS" in r for r in runs):
        wrong.append("a fixed JP_TOOLS clone URL")
    return wrong


def findings(text: str) -> list[str]:
    """What is wrong with a template's text; empty when nothing is."""
    all_steps = steps(text)
    uses = [str(s["uses"]) for s in all_steps if "uses" in s]
    return (_checkout_findings(all_steps) + _run_findings(all_steps)
            + [f"{u}: not an actions/* action" for u in uses if not u.startswith("actions/")]
            + [f"names {name!r}" for name in INTERNAL if name in text])


def test_actionlint_passes_on_both_workflows() -> None:
    for wf in WORKFLOWS:
        rc, out = actionlint(wf)
        check(f"actionlint: {wf.relative_to(ROOT)}", (rc, out), (0, ""))


def test_the_template_is_forge_neutral() -> None:
    check("the template has nothing wrong", findings(TEMPLATE.read_text(encoding="utf-8")), [])


def test_control_master_before_the_change_fails() -> None:
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{BEFORE}:templates/ci-check.yml"],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        print(f"SKIP: {BEFORE} is not in this clone's history; the control cannot run")
        return
    wrong = findings(r.stdout)
    check_true("control: the old template's fixed GitHub clone is caught",
               "a fixed JP_TOOLS clone URL" in wrong)
    check_true("control: the old template's setup-php is caught",
               any(w.startswith("shivammathur/setup-php") for w in wrong))
    with tempfile.TemporaryDirectory() as td:
        old = Path(td) / "ci-check.yml"
        old.write_text(r.stdout, encoding="utf-8")
        rc, out = actionlint(old)
    check_true("control: actionlint reports the old template's SC2086", rc == 1 and "SC2086" in out)


if __name__ == "__main__":
    sys.exit(run(globals(), "CI template"))
