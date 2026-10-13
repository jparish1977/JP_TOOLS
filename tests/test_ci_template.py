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
      the token, at JP_TOOLS/ beside the project's checkout at project/, both
      inside the workspace, with every step run from project/ (a job's user
      cannot write the workspace's parent); no step clones JP_TOOLS from a
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

    After the install, check-pins.py reads each language's tools back against
    its manifest, in the template's jobs (not shell's) and in JP_TOOLS' own CI.

    python3 tests/test_ci_template.py
"""

import json
import os
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
# The controls read the template at a past master commit. A history rewrite
# gives every commit a new id, so these must be remapped after one; a commit
# that is not in the clone fails the control rather than skipping it.
BEFORE = "7e00ba9"      # master before the template took its source from a variable
BEFORE_TESTS_JOB = "93fc0d4"  # master before #140 added the tests job
BEFORE_LAYOUT = "5da564d"     # master while JP_TOOLS was moved to the workspace's parent


def template_at(commit: str) -> str | None:
    """The template's text at COMMIT, or None, recorded as a failure, when the
    clone does not have it."""
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{commit}:templates/ci-check.yml"],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        check(f"control: {commit} is in this clone's history", r.stderr.strip(), "")
        return None
    return r.stdout


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
    if any(c["with"].get("token") != TOKEN for c in checkouts):
        return ["a JP_TOOLS checkout does not use JP_TOOLS_TOKEN"]
    return []


def _run_findings(all_steps: list[dict[str, Any]]) -> list[str]:
    runs = [str(s.get("run", "")) for s in all_steps]
    return ["a fixed JP_TOOLS clone URL"] if any("git clone" in r and "JP_TOOLS" in r for r in runs) else []


def _layout_findings(text: str) -> list[str]:
    """A job runs as a user who can write the workspace and not its parent, so
    in every job that takes JP_TOOLS, both checkouts are inside the workspace,
    the project at project/ and JP_TOOLS at JP_TOOLS/, and steps run from
    project/, where ../JP_TOOLS resolves."""
    wrong = []
    for name, job in yaml.safe_load(text).get("jobs", {}).items():
        job_steps = job.get("steps", [])
        checkouts = [s.get("with", {}) for s in job_steps if str(s.get("uses", "")).startswith("actions/checkout")]
        if not any(c.get("repository") == REPOSITORY for c in checkouts):
            continue
        if sorted(str(c.get("path")) for c in checkouts) != ["JP_TOOLS", "project"]:
            wrong.append(f"{name}: the project is not checked out at project/ and JP_TOOLS at JP_TOOLS/")
        if job.get("defaults", {}).get("run", {}).get("working-directory") != "project":
            wrong.append(f"{name}: steps do not run from project/")
        if any("../JP_TOOLS" in str(s.get("run", "")) and "mv " in str(s.get("run", "")) for s in job_steps):
            wrong.append(f"{name}: a step moves JP_TOOLS out of the workspace")
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
            + _layout_findings(text)
            + [f"{u}: not an actions/* action" for u in uses if not u.startswith("actions/")]
            + [f"names {name!r}" for name in INTERNAL if name in text])


def test_actionlint_passes_on_both_workflows() -> None:
    for wf in WORKFLOWS:
        rc, out = actionlint(wf)
        check(f"actionlint: {wf.relative_to(ROOT)}", (rc, out), (0, ""))


def test_the_template_is_forge_neutral() -> None:
    check("the template has nothing wrong", findings(TEMPLATE.read_text(encoding="utf-8")), [])


def test_control_master_before_the_change_fails() -> None:
    old_text = template_at(BEFORE)
    if old_text is None:
        return
    wrong = findings(old_text)
    check_true("control: the old template's fixed GitHub clone is caught",
               "a fixed JP_TOOLS clone URL" in wrong)
    check_true("control: the old template's setup-php is caught",
               any(w.startswith("shivammathur/setup-php") for w in wrong))
    check_true("control: the old template has no off-GitHub guard",
               "no step stops the job when JP_TOOLS_REPOSITORY is unset off GitHub" in wrong)
    with tempfile.TemporaryDirectory() as td:
        old = Path(td) / "ci-check.yml"
        old.write_text(old_text, encoding="utf-8")
        rc, out = actionlint(old)
    check_true("control: actionlint reports the old template's SC2086", rc == 1 and "SC2086" in out)
    old_text = template_at(BEFORE_LAYOUT)
    if old_text is not None:
        check("control: the template that moved JP_TOOLS to the workspace's parent is caught, in both jobs",
              _layout_findings(old_text),
              ["check: the project is not checked out at project/ and JP_TOOLS at JP_TOOLS/",
               "check: steps do not run from project/",
               "check: a step moves JP_TOOLS out of the workspace",
               "tests: the project is not checked out at project/ and JP_TOOLS at JP_TOOLS/",
               "tests: steps do not run from project/",
               "tests: a step moves JP_TOOLS out of the workspace"])


# What check.py's PHP runners and measures call from vendor: phpstan, phpcs and
# rector (jpcheck/php_tools.py), php-parser (jpcheck/measure.py).
GATE_PACKAGES = {"phpstan/phpstan", "squizlabs/php_codesniffer", "rector/rector",
                 "nikic/php-parser"}


def install_script(lang: str = "php") -> str:
    """The template's tool install for LANG, as the runner's shell would get it."""
    step = next(s for s in steps(TEMPLATE.read_text(encoding="utf-8"))
                if s.get("name") == "Install tool dependencies")
    return str(step["run"]).replace("${{ matrix.lang }}", lang)


def shell_install(requirements: str) -> tuple[int, list[list[str]]]:
    """The shell install step's exit code and the tools it ran, with a
    requirements-dev.txt holding REQUIREMENTS."""
    fakes = {"pip": "", "shellcheck": "", "shfmt": "",
             "python": "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"}
    with fake_tools(keep_path=True, named=fakes) as (work, log):
        jp_tools = Path(work).parent / "JP_TOOLS"
        jp_tools.mkdir()
        (jp_tools / "requirements-dev.txt").write_text(requirements, encoding="utf-8")
        r = subprocess.run(["bash", "-e", "-c", install_script("shell")], cwd=work,
                           capture_output=True, text=True, check=False)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
    return r.returncode, calls


def test_shell_install_takes_shfmt_at_its_pin() -> None:
    # The smells check measures shell through shfmt's parser; without it the
    # shell job reported smells "unavailable" and exited 2 on every repo with
    # a .sh file, whatever its code.
    text = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    pin = next(ln.strip() for ln in text.splitlines() if ln.startswith("shfmt-py=="))
    rc, calls = shell_install(text)
    check("the shell install step succeeds", rc, 0)
    check("pip installs exactly requirements-dev.txt's shfmt-py pin",
          [c[1:] for c in calls if c[0] == "pip"], [["install", pin]])
    check_true("and shfmt is on PATH after it", any(c[0] == "shfmt" for c in calls))


def test_shell_install_drops_a_comment_on_the_pin() -> None:
    # pip -r accepts an inline comment on a requirement; pip install refuses
    # the same text as one argument, so the comment is not passed on.
    rc, calls = shell_install("ruff==1\nshfmt-py==4.2.0  # the shell parser\n")
    check("a commented pin: pip is given the requirement alone",
          (rc, [c[1:] for c in calls if c[0] == "pip"]), (0, [["install", "shfmt-py==4.2.0"]]))


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


PIN_STEP = "Tools installed at their pins"


def test_the_installs_are_read_back_against_their_pins() -> None:
    """After the install and before the check, each language's tools are read
    back against the manifest they came from; shell has no manifest arm."""
    names = [s.get("name") for s in steps(TEMPLATE.read_text(encoding="utf-8"))]
    check_true("the pin step comes after the install and before the check",
               PIN_STEP in names
               and names.index("Install tool dependencies") < names.index(PIN_STEP) < names.index("Run check"))
    step = next((s for s in steps(TEMPLATE.read_text(encoding="utf-8")) if s.get("name") == PIN_STEP), {})
    check("the pin step runs check-pins.py for the job's language",
          step.get("run"), "python ../JP_TOOLS/check-pins.py ${{ matrix.lang }}")
    check_true("the pin step skips the shell job", "matrix.lang != 'shell'" in str(step.get("if", "")))
    own = steps((ROOT / ".github" / "workflows" / "check.yml").read_text(encoding="utf-8"))
    check("JP_TOOLS' own CI reads back every arm",
          sorted(str(s["run"]) for s in own if s.get("name") == PIN_STEP),
          ["python check-pins.py js", "python check-pins.py python php powershell"])


def test_the_template_checks_powershell() -> None:
    """A powershell job: found by its suffixes, PowerShell installed by
    JP_TOOLS' install-pwsh.sh, and its pins read back like any other arm's."""
    doc = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    check_true("powershell is in the language matrix",
               "powershell" in doc["jobs"]["check"]["strategy"]["matrix"]["lang"])
    present = next(s for s in steps(TEMPLATE.read_text(encoding="utf-8")) if s.get("id") == "present")
    check_true("a powershell job looks for .ps1, .psm1 and .psd1",
               "powershell) pats=('*.ps1' '*.psm1' '*.psd1')" in str(present["run"]))
    check_true("the powershell install runs JP_TOOLS' install-pwsh.sh",
               "powershell) bash ../JP_TOOLS/install-pwsh.sh ;;" in install_script("powershell"))
    own = steps((ROOT / ".github" / "workflows" / "check.yml").read_text(encoding="utf-8"))
    check_true("JP_TOOLS' own CI installs PowerShell and checks its .ps1 files",
               any(s.get("run") == "bash install-pwsh.sh" for s in own)
               and any("'*.ps1' '*.psm1' '*.psd1'" in str(s.get("run", "")) for s in own))


RAISED_STEP = "The baseline raises no count over the base's"
PASS_THROUGH = {"python": "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"}


def step_run(name: str, lang: str = "python") -> str:
    step = next(s for s in steps(TEMPLATE.read_text(encoding="utf-8")) if s.get("name") == name)
    return str(step["run"]).replace("${{ matrix.lang }}", lang)


def check_args(baseline: bool) -> tuple[int, list[list[str]]]:
    """The Run check step's exit code and its check.py arguments, in a repo
    with a quality-baseline.json when BASELINE."""
    with fake_tools(keep_path=True, named={"python": ""}) as (work, log):
        if baseline:
            (work / "quality-baseline.json").write_text("{}\n", encoding="utf-8")
        r = subprocess.run(["bash", "-e", "-c", step_run("Run check")], cwd=work,
                           env=dict(os.environ, SMELLS_BASE="abc123"),
                           capture_output=True, text=True, check=False)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
    return r.returncode, [c[1:] for c in calls if c[0] == "python"]


def test_a_repo_with_a_baseline_is_checked_against_it() -> None:
    plain = ["../JP_TOOLS/check.py", ".", "--lang", "python", "--pretty", "--smells-base", "abc123"]
    check("no quality-baseline.json: check.py with no baseline", check_args(False), (0, [plain]))
    check("a quality-baseline.json: check.py compares with it",
          check_args(True), (0, [[*plain, "--baseline", "quality-baseline.json"]]))


def _baseline(errors: int) -> str:
    entry = {"errors": errors, "warnings": 0, "fixable": 0, "fixable_unsafe": 0}
    return json.dumps({"baseline_version": 2, "mode": "per-file", "files": {"a.py": {"ruff": entry}}})


def raised_step(base_errors: int | None, head_errors: int | None, message: str = "change",
                base: str | None = None, after: str | None = None) -> tuple[int, str]:
    """The raised step's exit code and output, in a repo whose base commit has
    a baseline recording BASE_ERRORS (none when None) and whose next commit,
    with MESSAGE, records HEAD_ERRORS (none when None). AFTER, when given, is
    the message of one more commit that leaves the baseline alone. BASE
    overrides the base commit given to the step."""
    with fake_tools(keep_path=True, named=PASS_THROUGH) as (work, _):
        (work.parent / "JP_TOOLS").symlink_to(ROOT)
        git = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
        subprocess.run([*git, "init", "-q", "."], cwd=work, check=True)
        commits = [(base_errors, "base"), (head_errors, message)]
        if after is not None:
            commits.append((None, after))
        for errors, msg in commits:
            if errors is not None:
                (work / "quality-baseline.json").write_text(_baseline(errors), encoding="utf-8")
            (work / "f.txt").write_text(msg, encoding="utf-8")
            subprocess.run([*git, "add", "-A"], cwd=work, check=True)
            subprocess.run([*git, "commit", "-q", "-m", msg], cwd=work, check=True)
            if msg == "base":
                sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, capture_output=True,
                                     text=True, check=True).stdout.strip()
        r = subprocess.run(["bash", "-e", "-c", step_run(RAISED_STEP)], cwd=work,
                           env=dict(os.environ, BASE=base if base is not None else sha,
                                    RUNNER_TEMP=str(work.parent)),
                           capture_output=True, text=True, check=False)
    return r.returncode, r.stdout + r.stderr


def test_a_raised_baseline_is_refused_without_a_reason() -> None:
    rc, out = raised_step(3, 2)
    check("a lowered baseline passes", (rc, "raises no count" in out), (0, True))
    rc, out = raised_step(3, 4)
    check_true("a raised one fails, naming the count", rc == 1 and "a.py  ruff  errors  3 -> 4" in out)
    rc, out = raised_step(3, 4, "re-record\n\nLINT-DEBT-OK: ruff upgraded")
    check_true("a raised one with a LINT-DEBT-OK commit passes, quoting it",
               rc == 0 and "LINT-DEBT-OK: ruff upgraded" in out)
    rc, out = raised_step(3, 4, "re-record\n\nLINT-DEBT-OK:")
    check("  but not with an empty reason", rc, 1)
    rc, out = raised_step(3, 4, after="unrelated\n\nLINT-DEBT-OK: about another file")
    check_true("  nor with a reason in a commit that leaves the baseline alone",
               rc == 1 and "about another file" not in out)
    rc, out = raised_step(3, 4, "re-record\n\nLINT-DEBT-OK: ruff upgraded", after="later")
    check_true("  while the raising commit's reason still counts with a later commit on top",
               rc == 0 and "LINT-DEBT-OK: ruff upgraded" in out)
    rc, out = raised_step(None, 4)
    check_true("a base with no baseline: this change adds it, nothing to compare",
               rc == 0 and "this change adds it" in out)
    rc, out = raised_step(3, 4, base="0" * 40)
    check_true("no base commit: said, not compared", rc == 0 and "No base commit" in out)


ENTRY_STEP = "How are this repo's tests run?"
GATE_RUN =("python3 ../JP_TOOLS/coverage-gate.py clover.xml"
            " --fail-under ${{ vars.COVERAGE_FAIL_UNDER || '95' }}")


def tests_job(text: str) -> dict[str, Any]:
    return dict(yaml.safe_load(text).get("jobs", {}).get("tests", {}))


def entry(files: dict[str, str], executable: tuple[str, ...] = ()) -> tuple[int, dict[str, str], str]:
    """The template's test-entry step run in a git repo holding FILES (tracked,
    EXECUTABLE ones mode 755): its exit code, the outputs it set, and stderr."""
    step = next(s for s in tests_job(TEMPLATE.read_text(encoding="utf-8")).get("steps", [])
                if s.get("name") == ENTRY_STEP)
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        for name, body in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(body, encoding="utf-8")
            (repo / name).chmod(0o755 if name in executable else 0o644)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        out = Path(tmp) / "github_output"
        out.touch()
        r = subprocess.run(["bash", "-eo", "pipefail", "-c", str(step["run"])], cwd=repo,
                           env={"PATH": "/usr/bin:/bin", "GITHUB_OUTPUT": str(out)},
                           capture_output=True, text=True, check=False)
        outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return r.returncode, outputs, r.stderr


def test_the_tests_entry_is_found() -> None:
    """PHP with a phpunit config runs PHPUnit; any project with an executable
    tests/run runs it; both may apply."""
    rc, out, _ = entry({"src/A.php": "<?php\n", "phpunit.xml.dist": "<phpunit/>\n"})
    check("phpunit.xml.dist: phpunit, no tests/run", (rc, out), (0, {"phpunit": "yes", "run": "no"}))
    rc, out, _ = entry({"src/A.php": "<?php\n", "phpunit.xml": "<phpunit/>\n"})
    check("phpunit.xml: phpunit", (rc, out.get("phpunit")), (0, "yes"))
    rc, out, _ = entry({"a.py": "x = 1\n", "tests/run": "#!/bin/sh\n"}, executable=("tests/run",))
    check("an executable tests/run: run", (rc, out), (0, {"phpunit": "no", "run": "yes"}))
    rc, out, _ = entry({"src/A.php": "<?php\n", "phpunit.xml.dist": "<phpunit/>\n",
                        "tests/run": "#!/bin/sh\n"}, executable=("tests/run",))
    check("both: both", (rc, out), (0, {"phpunit": "yes", "run": "yes"}))


def test_code_with_no_way_to_run_its_tests_fails() -> None:
    """A test that did not run is not a pass (METHODOLOGY 8): code and no
    entry point stops the job, saying what to add. A repo with no code at
    all has nothing to test, and says so."""
    for name, files in (("python", {"a.py": "x = 1\n"}), ("js", {"a.js": "1\n"}),
                        ("shell", {"a.sh": "true\n"})):
        rc, _, err = entry(files)
        check(f"{name} code, no entry: exit", rc, 1)
        check_true(f"{name} code, no entry: names the language and tests/run",
                   name in err and "tests/run" in err)
    rc, _, err = entry({"src/A.php": "<?php\n"})
    check("php code, no entry: exit", rc, 1)
    check_true("php code, no entry: says what to add", "phpunit.xml" in err and "tests/run" in err)
    rc, _, err = entry({"a.py": "x = 1\n", "tests/run": "#!/bin/sh\n"})
    check("a tests/run that is not executable: exit", rc, 1)
    check_true("a tests/run that is not executable: says how to fix it", "chmod=+x" in err)
    rc, out, _ = entry({"README.md": "# docs only\n"})
    check("no code: nothing to run, and green", (rc, out), (0, {"phpunit": "no", "run": "no"}))


def test_a_mixed_repo_tests_every_language() -> None:
    """PHPUnit tests PHP only: a mixed repo whose other code has no tests/run
    fails, naming that language, rather than going green with it untested. A
    tests/run covers the whole repo. A lab/ directory anywhere is exempt, as in
    check.py's lab rule (METHODOLOGY 2.9: lab files are held to their header,
    not to zero)."""
    php = {"src/A.php": "<?php\n", "phpunit.xml.dist": "<phpunit/>\n"}
    rc, _, err = entry({**php, "tools/build.py": "x = 1\n"})
    check("php + python, phpunit only: exit", rc, 1)
    check_true("php + python, phpunit only: names python, says PHPUnit covers PHP only",
               "python" in err and "tests/run" in err and "PHP only" in err)
    rc, out, _ = entry({**php, "tools/build.py": "x = 1\n", "tests/run": "#!/bin/sh\n"},
                       executable=("tests/run",))
    check("php + python, phpunit and tests/run: both run", (rc, out), (0, {"phpunit": "yes", "run": "yes"}))
    rc, out, _ = entry({"src/A.php": "<?php\n", "tools/build.py": "x = 1\n", "tests/run": "#!/bin/sh\n"},
                       executable=("tests/run",))
    check("php + python, tests/run alone covers both", (rc, out), (0, {"phpunit": "no", "run": "yes"}))
    rc, out, _ = entry({**php, "tools/lab/probe.py": "x = 1\n", "tools/lab/run.sh": "true\n"})
    check("php + tools/lab scripts, phpunit only: lab is exempt", (rc, out), (0, {"phpunit": "yes", "run": "no"}))
    rc, out, _ = entry({"tools/lab/probe.py": "x = 1\n"})
    check("only tools/lab code: no code to test", (rc, out), (0, {"phpunit": "no", "run": "no"}))
    for where in ("lab/probe.py", "pb/lab/probe.py"):
        rc, out, _ = entry({**php, where: "x = 1\n"})
        check(f"php + {where}, phpunit only: a lab/ anywhere is exempt",
              (rc, out), (0, {"phpunit": "yes", "run": "no"}))
    for where in ("labs/probe.py", "mylab/probe.py"):
        rc, _, err = entry({**php, where: "x = 1\n"})
        check(f"php + {where}, phpunit only: only a directory named lab is exempt", rc, 1)


def test_the_template_runs_tests() -> None:
    """The tests job: PHPUnit writes Clover and coverage-gate.py gates it at
    the repository's threshold (95 by default), each only when the entry step
    found phpunit; tests/run runs only when it found one."""
    job = tests_job(TEMPLATE.read_text(encoding="utf-8"))
    by_run = {str(s.get("run", "")).strip(): str(s.get("if", "")) for s in job.get("steps", [])}
    phpunit = next((r for r in by_run if "phpunit" in r and "--coverage-clover clover.xml" in r), None)
    check_true("PHPUnit runs, writing clover.xml", phpunit is not None)
    check_true("only when the entry found phpunit",
               phpunit is not None and "steps.entry.outputs.phpunit == 'yes'" in by_run[phpunit])
    check_true("coverage-gate.py gates it at the repository threshold",
               any(GATE_RUN in r for r in by_run))
    check("tests/run runs only when found",
          by_run.get("./tests/run"), "steps.entry.outputs.run == 'yes'")
    check("the tests job downloads no Python of its own (the runner's python3 runs the gate)",
          [s.get("uses") for s in job.get("steps", []) if "setup-python" in str(s.get("uses", ""))], [])


def test_control_master_before_the_change_has_no_tests_job() -> None:
    old_text = template_at(BEFORE_TESTS_JOB)
    if old_text is not None:
        check("control: the template before #140 has no tests job", tests_job(old_text), {})


if __name__ == "__main__":
    sys.exit(run(globals(), "CI template"))
