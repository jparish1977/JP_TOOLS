#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_ci_reap.py
ci-image/reap.sh --can-run, and build.sh asking it before it builds, with
stand-ins for sudo (recording that the daemon was asked, then failing) and
claude-job (recording that it was started). Each case of their headers:

    - --can-run with a claude-job (CLAUDE_JOB, or the one on PATH): exit 0,
      silent, the job not started and the daemon not asked.
    - --can-run without one (none on PATH, or CLAUDE_JOB not runnable):
      exit 1, saying so; nothing asked.
    - build.sh tagging current with no claude-job: exit 2, "nothing built",
      the daemon never asked.
    - build.sh with a claude-job, or with --no-current: past the question, on
      to asking the daemon.

    python3 tests/test_ci_reap.py
"""

import os
import subprocess
import sys

from _harness import ROOT, check, run
from fake_tools import calls, fake_tools

REAP = ROOT / "ci-image" / "reap.sh"
BUILD = ROOT / "ci-image" / "build.sh"
SUDO = "sys.exit(1)\n"
JOB = "sys.exit(0)\n"


def _run(script: object, *args: str, job: str | None) -> tuple[int, str, str]:
    """(exit, stdout, stderr) of SCRIPT ARGS with CLAUDE_JOB as JOB, or unset."""
    child = {k: v for k, v in os.environ.items() if k != "CLAUDE_JOB"}
    if job is not None:
        child["CLAUDE_JOB"] = job
    p = subprocess.run(["/bin/bash", str(script), *args], capture_output=True, text=True,
                       env=child, check=False)
    return p.returncode, p.stdout, p.stderr


def test_can_run_with_a_claude_job() -> None:
    with fake_tools(sudo=SUDO, named={"claude-job": JOB}) as (work, log):
        on_path = _run(REAP, "--can-run", job=None)
        named = _run(REAP, "--can-run", job=str(work.parent / "bin" / "claude-job"))
        check("on PATH, or named by CLAUDE_JOB: exit 0, silent", (on_path, named), ((0, "", ""), (0, "", "")))
        check("  the job not started, the daemon not asked", calls(log), [])


def test_can_run_without_one() -> None:
    with fake_tools(sudo=SUDO) as (work, log):
        none = _run(REAP, "--can-run", job=None)
        named = _run(REAP, "--can-run", job=str(work / "absent"))
        check("none on PATH: exit 1, saying so",
              none, (1, "", "reap: no claude-job (none on PATH); set CLAUDE_JOB; removing nothing\n"))
        check("CLAUDE_JOB not runnable: exit 1, naming it",
              named, (1, "", f"reap: no claude-job ({work / 'absent'}); set CLAUDE_JOB; removing nothing\n"))
        check("  nothing asked", calls(log), [])


def test_build_asks_before_it_builds() -> None:
    with fake_tools(keep_path=True, sudo=SUDO, named={"claude-job": JOB}) as (work, log):
        job = str(work.parent / "bin" / "claude-job")
        rc, out, err = _run(BUILD, job=str(work / "absent"))
        check("tagging current with no claude-job: exit 2, reap's reason and nothing built, the daemon never asked",
              (rc, out, err, calls(log)),
              (2, "", (f"reap: no claude-job ({work / 'absent'}); set CLAUDE_JOB; removing nothing\n"
                       "ci-image: tagging fleet-ci:current runs reap.sh, which could not; nothing built\n"), []))
        rc, _, _ = _run(BUILD, job=job)
        asked = [c[:3] for c in calls(log)]
        check("  with one: past the question, the daemon asked (and failing here)",
              (rc, asked[:1]), (2, [["sudo", "-n", "docker"]]))
        log.write_text("")
        _run(BUILD, "--no-current", job=str(work / "absent"))
        check("--no-current: no question, the daemon asked", [c[:3] for c in calls(log)][:1],
              [["sudo", "-n", "docker"]])


if __name__ == "__main__":
    sys.exit(run(globals(), "reap.sh --can-run"))
