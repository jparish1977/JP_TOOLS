"""Which tracked files outside tools/lab/ RUN a tools/lab/ file.

METHODOLOGY 2.9: a lab tool that grows a dependency is promoted out of the
lab. A dependency is code that executes the tool, not text that names it: a
comment or a doc citing a lab tool as how something was measured is a
mention and passes, and so is a lab path given to a program as data (git
log, grep, cat). So in both languages the question is the same: is the
PROGRAM a lab path, or the script an interpreter is given, or the file or
module code loads.

- Python, jpcheck/lab_python.py: a .py file, or a file with no suffix whose
  #! line names python.
- Shell, jpcheck/lab_shell.py: .sh and .bash files, a file with no suffix
  whose #! line names a shell, and each line of a .yml or .yaml file (a
  workflow's `run:`).

Each module's docstring says what it reads and what it does not see. JS and
PHP launching a process are not seen.
"""

import subprocess
from pathlib import Path

from jpcheck.lab_python import python_runs
from jpcheck.lab_shell import LAB, shell_runs

__all__ = ["lab_runs", "python_runs", "shell_runs"]

_SHELL_SUFFIXES = (".sh", ".bash", ".yml", ".yaml")


def _kind(path: Path) -> str:
    """"python", "shell" or "" for a tracked file: by suffix, else by the
    #! line of a file with none."""
    if path.suffix == ".py":
        return "python"
    if path.suffix in _SHELL_SUFFIXES:
        return "shell"
    if path.suffix:
        return ""
    try:
        with path.open("rb") as fh:
            first = fh.readline(200)
    except OSError:  # reason: an unreadable file runs nothing this can see
        return ""
    if not first.startswith(b"#!"):
        return ""
    if b"python" in first:
        return "python"
    return "shell" if b"sh" in first else ""


def lab_runs(root: Path) -> list[tuple[str, int]]:
    """(path, line) for each place a tracked file outside tools/lab/ runs a
    lab path, in path order."""
    tracked = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True,
                             text=True, check=True).stdout.splitlines()
    found = []
    for rel in tracked:
        if rel.startswith(LAB):
            continue
        path = root / rel
        if not path.is_file():
            continue
        kind = _kind(path)
        if not kind:
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        hits = python_runs(body) if kind == "python" else shell_runs(body)
        found += [(rel, n) for n in hits]
    return found
