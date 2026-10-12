#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_ci_tools.py
ci-image/install-php-tools.sh and ci-image/mutation-tools.sh, and the
workflows' use of them. The rules:

    - Every job that installs the PHP tools does it through
      install-php-tools.sh: no workflow step runs composer install itself.
      The three mutmut jobs (check.yml's mutation and mutation-state,
      mutmut-nightly.yml's full-run) set up through mutation-tools.sh, which
      calls it; the Python job calls it directly. One copy, so none drifts.
    - Each step that runs either script hands it the secret, the owner
      variable and the forge address.
    - install-php-tools.sh refuses, exit 1 and naming what to set, when any
      of the three is missing, without calling composer.
    - Given all three, it adds both libraries' repositories under the owner,
      turns secure-http off for an http forge only, and installs with the
      token in COMPOSER_AUTH for the forge's host, never in argv.

    python3 tests/test_ci_tools.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from _harness import ROOT, check, check_true, run

PHP = ROOT / "ci-image" / "install-php-tools.sh"
MUT = ROOT / "ci-image" / "mutation-tools.sh"
WORKFLOWS = ROOT / ".github" / "workflows"
ENV = {"FLEET_READ_TOKEN": "${{ secrets.FLEET_READ_TOKEN }}",
       "OWNER": "${{ vars.ITERATION8_LIBRARIES_OWNER }}",
       "SERVER": "${{ github.server_url }}"}


def _jobs() -> dict[str, dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = {}
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for name, job in (yaml.safe_load(wf.read_text(encoding="utf-8")).get("jobs") or {}).items():
            jobs[f"{wf.name}:{name}"] = job
    return jobs


def _runs(job: dict[str, Any], script: str) -> list[dict[str, Any]]:
    return [s for s in job.get("steps", []) if s.get("run", "").strip() == f"bash ci-image/{script}"]


def test_the_workflows_use_the_scripts() -> None:
    jobs = _jobs()
    inline = sorted(f"{name}: {s.get('name', s['run'][:30])}" for name, job in jobs.items()
                    for s in job.get("steps", []) if "composer install" in s.get("run", ""))
    check("no workflow step runs composer install itself", inline, [])
    for name in ("check.yml:mutation", "check.yml:mutation-state", "mutmut-nightly.yml:full-run"):
        steps = _runs(jobs[name], "mutation-tools.sh")
        check(f"{name} sets up through mutation-tools.sh, once", len(steps), 1)
        check("  with the secret, the owner and the forge", steps[0].get("env") if steps else None, ENV)
    steps = _runs(jobs["check.yml:python"], "install-php-tools.sh")
    check("the Python job installs the PHP tools through the script, once", len(steps), 1)
    check("  with the secret, the owner and the forge", steps[0].get("env") if steps else None, ENV)
    check_true("mutation-tools.sh calls install-php-tools.sh",
               "bash ci-image/install-php-tools.sh" in MUT.read_text(encoding="utf-8"))


def _fake_composer(where: Path) -> Path:
    """A composer that logs each call's argv, and COMPOSER_AUTH, one JSON line each."""
    bindir = where / "bin"
    bindir.mkdir()
    fake = bindir / "composer"
    fake.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                    f"with open({str(where / 'calls')!r}, 'a') as f:\n"
                    "    f.write(json.dumps([sys.argv[1:], os.environ.get('COMPOSER_AUTH')]) + '\\n')\n",
                    encoding="utf-8")
    fake.chmod(0o755)
    return bindir


def _php(where: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], list[Any]]:
    bindir = _fake_composer(where)
    base = {k: v for k, v in os.environ.items() if k not in ("FLEET_READ_TOKEN", "OWNER", "SERVER")}
    base["PATH"] = f"{bindir}{os.pathsep}{base.get('PATH', '')}"
    p = subprocess.run(["bash", str(PHP)], cwd=where, env={**base, **env},
                       capture_output=True, text=True, check=False)
    log = where / "calls"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return p, calls


def test_it_refuses_without_each_of_the_three() -> None:
    full = {"FLEET_READ_TOKEN": "tok", "OWNER": "own", "SERVER": "http://forge:3000"}
    for missing in full:
        with tempfile.TemporaryDirectory() as td:
            p, calls = _php(Path(td), **{k: v for k, v in full.items() if k != missing})
            check(f"no {missing}: exit 1", p.returncode, 1)
            check_true("  naming the secret and the variable to set",
                       "FLEET_READ_TOKEN" in p.stderr and "ITERATION8_LIBRARIES_OWNER" in p.stderr)
            check("  and composer never called", calls, [])


def test_it_installs_with_the_token_in_the_environment() -> None:
    with tempfile.TemporaryDirectory() as td:
        p, calls = _php(Path(td), FLEET_READ_TOKEN="tok", OWNER="own", SERVER="http://forge:3000")
        check("exit 0", p.returncode, 0)
        check("the two repositories, http allowed, then the install", [c[0] for c in calls], [
            ["config", "-g", "repositories.iteration8-core", "vcs", "http://forge:3000/own/iteration8-core.git"],
            ["config", "-g", "repositories.iteration8-utilities", "vcs",
             "http://forge:3000/own/iteration8-utilities.git"],
            ["config", "-g", "secure-http", "false"],
            ["install", "--no-interaction", "--no-progress"]])
        check("  the token reaches only the install, for the forge's host",
              [json.loads(c[1]) if c[1] else None for c in calls],
              [None, None, None, {"http-basic": {"forge:3000": {"username": "ci", "password": "tok"}}}])
        check_true("  and never in argv", all("tok" not in a for c in calls for a in c[0]))
    with tempfile.TemporaryDirectory() as td:
        _, calls = _php(Path(td), FLEET_READ_TOKEN="tok", OWNER="own", SERVER="https://forge")
        check_true("an https forge keeps secure-http on",
                   all("secure-http" not in c[0] for c in calls) and len(calls) == 3)


if __name__ == "__main__":
    sys.exit(run(globals(), "CI tool scripts"))
