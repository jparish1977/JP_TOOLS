#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_lab_not_executed.py
No tracked file outside tools/lab/ runs a tools/lab/ file (METHODOLOGY 2.9: a
lab tool that grows a dependency is promoted), jpcheck/lab_use.py.

    - Python (tests/_lab_python_cases.py): running a lab path is found whatever
      carries it (a runner by module or alias, a variable through two
      assignments, an attribute, a held argv list, a local helper or method,
      shell=True, executable=, os.exec*, spawn*, posix_spawn, popen,
      getoutput, asyncio), and so is loading one (import, sys.path,
      importlib, runpy, exec, load_script). A lab path that is only an
      argument is not: git log, grep, an env value, mock.call, a method
      named run, a helper whose parameter is not the program.
    - Shell (tests/_lab_shell_cases.py): the command word after VAR=, control words and wrappers with
      their value options; an interpreter's script past its value options;
      -c and -m; find -exec; $(...) and backticks, also inside double quotes.
      Not: cat, echo, git log, a comment, single-quoted or escaped text, a
      YAML name:.
    - Files: .py, shell and YAML by suffix, and a file with no suffix by its
      #! line (Python or shell); Markdown and lab files are not scanned.
    - This repo: the only places are the two tests that prove the old lab
      paths still forward to the promoted scripts.

    python3 tests/test_lab_not_executed.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _lab_edge_cases import PY_EDGES, SH_EDGES
from _lab_python_cases import PY_CASES
from _lab_shell_cases import SH_CASES

from _harness import ROOT, check, check_true, run
from jpcheck.lab_use import lab_runs, python_runs, shell_runs


def test_python_cases() -> None:
    for label, runs, text in PY_CASES + PY_EDGES:
        check(label, bool(python_runs(text)), runs)


def test_shell_cases() -> None:
    for label, runs, line in SH_CASES + SH_EDGES:
        check(label, bool(shell_runs(line + "\n")), runs)


def test_a_root_git_cannot_list_raises() -> None:
    """"Nothing runs a lab tool" is never the answer to a question that
    could not be asked."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            lab_runs(Path(tmp))
            raised = False
        except subprocess.CalledProcessError:
            raised = True
    check_true("lab_runs outside a git repo raises", raised)


# Each file in the throwaway repo, and the lines lab_runs must report for it.
PLANTED = {
    "py_direct.py": ('import subprocess\nsubprocess.run(["python3", "tools/lab/x.py"])\n', [2]),
    "sh_direct.sh": ("#!/bin/bash\nset -e\ntools/lab/x.sh --flag\n", [3]),
    "workflow.yml": (("jobs:\n  a:\n    steps:\n      - name: tools/lab/x.py is how\n"
                      "        run: python3 tools/lab/x.py\n"), [5]),
    "bin/pyscript": ('#!/usr/bin/env python3\nimport subprocess\nsubprocess.run(["tools/lab/x.sh"])\n', [3]),
    "bin/shscript": ("#!/bin/sh\nbash tools/lab/x.sh\n", [2]),
    "bin/perlscript": ("#!/usr/bin/perl\nsystem('bash tools/lab/x.sh');\n", []),
    "bin/data": ("tools/lab/x.sh\n", []),
    "notes.md": ("Run `bash tools/lab/x.sh` to measure.\n", []),
    "tools/lab/inner.sh": ("#!/bin/bash\nbash tools/lab/x.sh\n", []),
    # Made unreadable after git add: it runs nothing this can see.
    "bin/locked": ("#!/bin/sh\nbash tools/lab/x.sh\n", []),
    # Tracked, then deleted from the working tree.
    "gone.sh": ("bash tools/lab/x.sh\n", []),
}


def test_planted_files_are_routed_by_kind() -> None:
    root = Path(tempfile.mkdtemp())
    try:
        for name, (body, _) in PLANTED.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(body, encoding="utf-8")
        # A byte that is not UTF-8 before the line that runs one.
        (root / "latin1.sh").write_bytes(b"echo caf\xe9\nbash tools/lab/x.sh\n")
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        (root / "bin" / "locked").chmod(0)
        (root / "gone.sh").unlink()
        found: dict[str, list[int]] = {}
        for rel, line in lab_runs(root):
            found.setdefault(rel, []).append(line)
        for name, (_, lines) in PLANTED.items():
            check(f"{name}", found.get(name, []), lines)
        check("latin1.sh: read past a byte that is not UTF-8", found.get("latin1.sh", []), [2])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_an_unparsable_file_runs_nothing_it_can_see() -> None:
    check("python that does not parse", python_runs("subprocess.run(['tools/lab/x.py'\n"), [])
    check("shell with an unbalanced quote", shell_runs("bash 'tools/lab/x.sh\n"), [])


# The tests that run the old lab paths, to prove they still forward to the
# promoted scripts. Anything else in this list is a lab tool to promote.
FORWARDER_TESTS = {"tests/test_prove_head.py", "tests/test_run_suites.py"}


def test_this_repo_runs_no_lab_file() -> None:
    files = {rel for rel, _ in lab_runs(ROOT)}
    check("only the forwarder tests run a lab path", sorted(files), sorted(FORWARDER_TESTS))


if __name__ == "__main__":
    sys.exit(run(globals(), "lab tools are not run from outside the lab"))
