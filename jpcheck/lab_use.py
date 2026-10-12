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
from collections.abc import Callable
from pathlib import Path

from jpcheck.lab_python import python_runs
from jpcheck.lab_shell import LAB, shell_runs

__all__ = ["lab_runs", "python_runs", "shell_runs"]

_SHELL_SUFFIXES = (".sh", ".bash", ".yml", ".yaml")
_SHEBANG_MAX = 200  # bytes of a #! line read; one is never longer
_DECODE = ("utf-8", "replace")  # a byte that is not UTF-8 cannot hide a line

Reader = Callable[[str], list[int]]


def _reader(path: Path) -> Reader | None:
    """python_runs or shell_runs for a tracked file, by its suffix, else by
    the #! line of a file with none; None for a file neither reads."""
    if path.suffix == ".py":
        return python_runs
    if path.suffix in _SHELL_SUFFIXES:
        return shell_runs
    if path.suffix:
        return None
    try:
        with path.open("rb") as fh:
            first = fh.readline(_SHEBANG_MAX)
    except OSError:  # reason: an unreadable file runs nothing this can see
        return None
    if not first.startswith(b"#!"):
        return None
    if b"python" in first:
        return python_runs
    return shell_runs if b"sh" in first else None


def lab_runs(root: Path) -> list[tuple[str, int]]:
    """(path, line) for each place a tracked file outside tools/lab/ runs a
    lab path, in path order. A ROOT git cannot list raises: "nothing runs a
    lab tool" is never the answer to a question that could not be asked."""
    tracked = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True,
                             text=True, check=True).stdout.splitlines()
    found = []
    for rel in tracked:
        if rel.startswith(LAB):
            continue
        path = root / rel
        if not path.is_file():
            continue
        read = _reader(path)
        if read is None:
            continue
        found += [(rel, n) for n in read(path.read_bytes().decode(*_DECODE))]
    return found
