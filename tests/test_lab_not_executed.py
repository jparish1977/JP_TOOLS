#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_lab_not_executed.py
No tracked file outside tools/lab/ runs a tools/lab/ file (METHODOLOGY 2.9: a
lab tool that grows a dependency is promoted), jpcheck/lab_use.py.

    - Planted in a throwaway repo, each of these is found: Python running a
      lab script through subprocess, through a variable, through a local
      helper, and by Path parts; shell running one directly, through an
      interpreter, behind env and nice, and inside bash -c; a workflow's run:.
    - Mentions are not: a comment, a docstring, a file opened for reading,
      echo and cat naming one, a YAML name:, a Markdown doc, and a lab file
      running another lab file.
    - This repo: the only places are the two tests that prove the old lab
      paths still forward to the promoted scripts.

    python3 tests/test_lab_not_executed.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, run
from jpcheck.lab_use import lab_runs, python_runs, shell_runs

# Each file in the throwaway repo, and the lines lab_runs must report for it.
PLANTED = {
    "py_direct.py": ('import subprocess\nsubprocess.run(["python3", "tools/lab/x.py"])\n', [2]),
    "py_variable.py": ('import subprocess\nTOOL = "tools/lab/x.py"\nsubprocess.run([TOOL])\n', [3]),
    "py_helper.py": (("import subprocess\nfrom pathlib import Path\n"
                      "def go(script):\n    return subprocess.run(['bash', str(script)])\n"
                      "go(Path('.') / 'tools' / 'lab' / 'x.sh')\n"), [5]),
    "py_mentions.py": (('"""Measured with tools/lab/x.py."""\n# see tools/lab/x.py\n'
                        'text = open("tools/lab/README.md").read()\n'), []),
    "sh_direct.sh": ("#!/bin/bash\ntools/lab/x.sh --flag\n", [2]),
    "sh_interpreter.sh": ("#!/bin/bash\nset -e\npython3 tools/lab/x.py arg\n", [3]),
    "sh_wrapped.sh": ("#!/bin/bash\nenv FOO=1 nice -n 5 tools/lab/x.sh\n", [2]),
    "sh_dash_c.sh": ("#!/bin/bash\nbash -c 'tools/lab/x.sh'\n", [2]),
    "sh_after_and.sh": ("#!/bin/bash\ncd /tmp && bash tools/lab/x.sh\n", [2]),
    "sh_mentions.sh": (('#!/bin/bash\n# tools/lab/x.sh measured this\necho "see tools/lab/x.sh"\n'
                        "cat tools/lab/README.md\n"), []),
    "workflow.yml": (("jobs:\n  a:\n    steps:\n      - name: tools/lab/x.py is how\n"
                      "        run: python3 tools/lab/x.py\n"), [5]),
    "notes.md": ("Run `bash tools/lab/x.sh` to measure.\n", []),
    "tools/lab/inner.sh": ("#!/bin/bash\nbash tools/lab/x.sh\n", []),
}


def test_planted_runs_are_found_and_mentions_are_not() -> None:
    root = Path(tempfile.mkdtemp())
    try:
        for name, (body, _) in PLANTED.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(body, encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        found: dict[str, list[int]] = {}
        for rel, line in lab_runs(root):
            found.setdefault(rel, []).append(line)
        for name, (_, lines) in PLANTED.items():
            check(f"{name}", found.get(name, []), lines)
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
