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

    The PHP install takes only the dev packages composer.json's
    extra.jp-tools-gate names. The others are libraries JP_TOOLS' own scripts
    use, held where an adopting repo's runner cannot read them, and installing
    them stopped every adopter's PHP job before it checked anything. The
    template's own install step runs here against a fake composer: it removes
    exactly the non-gate packages, then installs. The gate list covers every
    PHP tool check.py runs and names only declared packages.

    python3 tests/test_ci_template.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from _harness import ROOT, check, check_true, run
from fake_tools import fake_tools

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


def _guard_findings(all_steps: list[dict[str, Any]]) -> list[str]:
    """actions/checkout fetches from the forge it runs on, so off GitHub the default
    names a repo on the wrong forge: a step must stop the job when it would be used."""
    for s in all_steps:
        cond, body = str(s.get("if", "")), str(s.get("run", ""))
        if ("!vars.JP_TOOLS_REPOSITORY" in cond and "github.server_url != 'https://github.com'"
                in cond and "exit 1" in body):
            return []
    return ["no step stops the job when JP_TOOLS_REPOSITORY is unset off GitHub"]


def findings(text: str) -> list[str]:
    """What is wrong with a template's text; empty when nothing is."""
    all_steps = steps(text)
    uses = [str(s["uses"]) for s in all_steps if "uses" in s]
    return (_checkout_findings(all_steps) + _guard_findings(all_steps) + _run_findings(all_steps)
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
    check_true("control: the old template has no off-GitHub guard",
               "no step stops the job when JP_TOOLS_REPOSITORY is unset off GitHub" in wrong)
    with tempfile.TemporaryDirectory() as td:
        old = Path(td) / "ci-check.yml"
        old.write_text(r.stdout, encoding="utf-8")
        rc, out = actionlint(old)
    check_true("control: actionlint reports the old template's SC2086", rc == 1 and "SC2086" in out)


# What check.py's PHP runners and measures call from vendor: phpstan, phpcs and
# rector (jpcheck/php_tools.py), php-parser (jpcheck/measure.py).
GATE_PACKAGES = {"phpstan/phpstan", "squizlabs/php_codesniffer", "rector/rector",
                 "nikic/php-parser"}


def install_script(lang: str = "php") -> str:
    """The template's tool install for LANG, as the runner's shell would get it."""
    step = next(s for s in steps(TEMPLATE.read_text(encoding="utf-8"))
                if s.get("name") == "Install tool dependencies")
    return str(step["run"]).replace("${{ matrix.lang }}", lang)


def test_shell_install_takes_shfmt_at_its_pin() -> None:
    # The smells check measures shell through shfmt's parser; without it the
    # shell job reported smells "unavailable" and exited 2 on every repo with
    # a .sh file, whatever its code.
    pin = next(ln.strip() for ln in (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
               .splitlines() if ln.startswith("shfmt-py=="))
    fakes = {"pip": "", "shellcheck": "", "shfmt": "",
             "python": "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"}
    with fake_tools(keep_path=True, named=fakes) as (work, log):
        jp_tools = Path(work).parent / "JP_TOOLS"
        jp_tools.mkdir()
        shutil.copy(ROOT / "requirements-dev.txt", jp_tools / "requirements-dev.txt")
        r = subprocess.run(["bash", "-e", "-c", install_script("shell")], cwd=work,
                           capture_output=True, text=True, check=False)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
    check("the shell install step succeeds", r.returncode, 0)
    check("pip installs exactly requirements-dev.txt's shfmt-py pin",
          [c[1:] for c in calls if c[0] == "pip"], [["install", pin]])
    check_true("and shfmt is on PATH after it", any(c[0] == "shfmt" for c in calls))


def composer_calls(manifest: str | None = None,
                   project_manifest: bool = False) -> tuple[int, list[list[str]]]:
    """The install step's exit code and the composer commands it runs, on
    JP_TOOLS' composer.json or the MANIFEST text given, in a project with a
    composer.json of its own when PROJECT_MANIFEST."""
    fakes = {"composer": "",
             "python": "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"}
    with fake_tools(keep_path=True, named=fakes) as (work, log):
        jp_tools = Path(work).parent / "JP_TOOLS"
        jp_tools.mkdir()
        (jp_tools / "composer.json").write_text(
            manifest if manifest is not None
            else (ROOT / "composer.json").read_text(encoding="utf-8"), encoding="utf-8")
        if project_manifest:
            (Path(work) / "composer.json").write_text("{}", encoding="utf-8")
        r = subprocess.run(["bash", "-e", "-c", install_script()], cwd=work,
                           capture_output=True, text=True, check=False)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
    return r.returncode, [c[1:] for c in calls if c[0] == "composer"]


def test_php_install_takes_only_the_gate_packages() -> None:
    manifest = json.loads((ROOT / "composer.json").read_text(encoding="utf-8"))
    gate = set(manifest["extra"]["jp-tools-gate"])
    dev = set(manifest["require-dev"])
    check("the gate list covers every PHP tool check.py runs", GATE_PACKAGES - gate, set())
    check("the gate list names only declared dev packages", gate - dev, set())
    rc, calls = composer_calls()
    check("the step succeeds", rc, 0)
    removed = [c for c in calls if c[1:2] == ["remove"]]
    check("one remove, of exactly the non-gate dev packages",
          [sorted(c[c.index("--no-interaction") + 1:]) for c in removed], [sorted(dev - gate)])
    check_true("the remove only edits composer.json", all("--no-update" in c for c in removed))
    check("then install", [c[1] for c in calls], ["remove", "install"])
    check("a project with no composer.json: nothing of its own is installed",
          [c for c in calls if not c[0].startswith("--working-dir")], [])


def test_php_install_installs_the_project_too() -> None:
    # Without the project's own dependencies phpstan cannot load what its code
    # uses: every test read as extending an unknown PHPUnit class.
    rc, calls = composer_calls(project_manifest=True)
    check("the step succeeds", rc, 0)
    check("JP_TOOLS' remove and install, then the project's install",
          [(c[0].startswith("--working-dir"), c[1] if c[0].startswith("--") else c[0])
           for c in calls],
          [(True, "remove"), (True, "install"), (False, "install")])


def test_php_install_stops_on_a_manifest_it_cannot_read() -> None:
    # No gate list: the step stops where it reads it, before any composer runs,
    # rather than installing everything and failing on the private libraries.
    rc, calls = composer_calls('{"require-dev": {"a/b": "1"}}')
    check("a manifest with no gate list: the step fails, and runs no composer",
          (rc != 0, calls), (True, []))


if __name__ == "__main__":
    sys.exit(run(globals(), "CI template"))
