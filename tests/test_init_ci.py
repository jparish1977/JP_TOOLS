#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_init_ci.py
init-ci.py installs both CI workflows into a repo, leaves one already there
alone unless --force, and still installs the rest.

WHAT IS CHECKED
    - A fresh repo gets .github/workflows/check.yml and mutmut-nightly.yml,
      each byte for byte its template, and the full message; exit 0.
    - An existing workflow is left alone without --force, saying so, while
      the missing one is installed; exit 1. With --force both are replaced.
    - Every workflow already there: nothing installed, no "activate" lines.
    - Not a git repository, or a template missing: an error, exit 1, and
      nothing written.
    - --help names what the tool and its arguments do.
    Run in this process, so mutation testing sees what it reaches.

    python3 tests/test_init_ci.py
"""

import contextlib
import io
import sys
import tempfile
from pathlib import Path
from unittest import mock

from _harness import ROOT, check, check_true, load_script, run

init_ci = load_script("init-ci.py")
TEMPLATES = ROOT / "templates"
METHODOLOGY = ROOT / "METHODOLOGY.md"


def init(*args: str) -> tuple[int, str]:
    """init-ci.py ARGS: (exit code, stdout)."""
    out = io.StringIO()
    code = 0
    with mock.patch.object(sys, "argv", ["init-ci.py", *args]), contextlib.redirect_stdout(out):
        try:
            init_ci.main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else int(e.code is not None)
    return code, out.getvalue()


def repo(tmp: str) -> Path:
    r = Path(tmp) / "repo"
    (r / ".git").mkdir(parents=True)
    return r


def activate() -> str:
    return ("Commit and push to activate.\n\n"
            "This project now follows JP_TOOLS methodology.\n"
            f"Read it: {METHODOLOGY}\n")


def test_a_fresh_repo_gets_both_workflows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        r = repo(tmp)
        wf = r / ".github" / "workflows"
        code, out = init(str(r))
        check("a fresh repo: exit 0, both installed, then how to activate",
              (code, out), (0, f"Installed CI workflow at {wf / 'check.yml'}\n"
                               f"Installed CI workflow at {wf / 'mutmut-nightly.yml'}\n" + activate()))
        check("check.yml is the CI template, byte for byte",
              (wf / "check.yml").read_bytes(), (TEMPLATES / "ci-check.yml").read_bytes())
        check("mutmut-nightly.yml is its template, byte for byte",
              (wf / "mutmut-nightly.yml").read_bytes(), (TEMPLATES / "mutmut-nightly.yml").read_bytes())
        check("nothing else is written", sorted(p.name for p in wf.iterdir()),
              ["check.yml", "mutmut-nightly.yml"])


def test_the_path_defaults_to_the_cwd() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        r = repo(tmp)
        with contextlib.chdir(r):
            code, _ = init()
        check("no path: the current directory is the repo", code, 0)
        check_true("  and its workflows are installed", (r / ".github" / "workflows" / "check.yml").is_file())


def test_an_existing_workflow_is_left_alone_and_the_rest_installed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        r = repo(tmp)
        wf = r / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "check.yml").write_text("mine\n", encoding="utf-8")
        code, out = init(str(r))
        check("check.yml there: exit 1, it is left alone, the nightly installed",
              (code, out), (1, f"Workflow already exists at {wf / 'check.yml'}: left alone (--force overwrites it)\n"
                               f"Installed CI workflow at {wf / 'mutmut-nightly.yml'}\n" + activate()))
        check("  check.yml is unchanged", (wf / "check.yml").read_text(encoding="utf-8"), "mine\n")
        check_true("  the nightly is its template",
                   (wf / "mutmut-nightly.yml").read_bytes() == (TEMPLATES / "mutmut-nightly.yml").read_bytes())
        code, out = init(str(r))
        check("both there: exit 1, nothing installed, no activate lines",
              (code, out), (1, (f"Workflow already exists at {wf / 'check.yml'}: left alone (--force overwrites it)\n"
                                f"Workflow already exists at {wf / 'mutmut-nightly.yml'}: left alone"
                                " (--force overwrites it)\n")))
        code, out = init(str(r), "--force")
        check("--force: exit 0, both replaced",
              (code, out), (0, f"Installed CI workflow at {wf / 'check.yml'}\n"
                               f"Installed CI workflow at {wf / 'mutmut-nightly.yml'}\n" + activate()))
        check("  check.yml is the template again",
              (wf / "check.yml").read_bytes(), (TEMPLATES / "ci-check.yml").read_bytes())


def test_not_a_repository() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "plain"
        target.mkdir()
        code, out = init(str(target))
        check("not a git repository: exit 1, saying so", (code, out),
              (1, f"Error: {target.resolve()} is not a git repository\n"))
        check("  nothing written", list(target.iterdir()), [])


def test_a_missing_template() -> None:
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as tdir:
        r = repo(tmp)
        (Path(tdir) / "ci-check.yml").write_text("x\n", encoding="utf-8")
        with mock.patch.object(init_ci, "TEMPLATES_DIR", Path(tdir)):
            code, out = init(str(r))
        check("a template missing: exit 1, naming it", (code, out),
              (1, f"Error: template(s) not found in {tdir}: mutmut-nightly.yml\n"))
        check("  nothing written", sorted(p.name for p in r.iterdir()), [".git"])
        with tempfile.TemporaryDirectory() as empty, mock.patch.object(init_ci, "TEMPLATES_DIR", Path(empty)):
            code, out = init(str(r))
        check("both missing: both named, in order", (code, out),
              (1, f"Error: template(s) not found in {empty}: ci-check.yml, mutmut-nightly.yml\n"))


def test_the_help_says_what_it_does() -> None:
    code, out = init("--help")
    check("--help exits 0", code, 0)
    for text in ("Install JP_TOOLS CI workflows into a repo",
                 "Path to git repository (default: cwd)", "Overwrite existing workflow files"):
        check_true(f"--help says {text!r}", text in " ".join(out.split()))


if __name__ == "__main__":
    sys.exit(run(globals(), "init-ci"))
