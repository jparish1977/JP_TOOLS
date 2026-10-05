#!/usr/bin/env python3
"""How big are the tracked source files, and how much of each is prose?

ANSWERS: how many tracked source files across the given git repos pass a
  physical-line threshold, and how much of each is code versus comments and
  docstrings. Written to size METHODOLOGY 2.14 ("Size is a smell").

TESTED AGAINST: 38 git repositories, 1,349 source files, 0 unreadable.
  Positive control: its physical counts for two files of 1,062 and 2,873
  lines matched `wc -l` exactly, checked before any number was quoted.
  Those numbers came from an earlier revision of this file (in git history
  as tools/lab/file-sizes-as-run.py). The current code was restructured for
  the smell limits and prints byte-identical output to that revision over
  the same repositories; its one intended difference is exiting 1 when no
  argument was a readable git repo.

NOT TESTED AGAINST: non-Python classification is approximate. A line whose
  first non-blank characters are #, //, /*, * or -- counts as comment.
  Extensionless scripts are NOT measured, because the file set is chosen by
  suffix, which is check.py's blind spot too. Tracked vendored files are
  included. The Python code/doc split was never checked against a hand count.

Three outcomes per file: measured, unreadable, not-source. The summary prints
all three, so a zero cannot hide a file that was never read.

usage: file-sizes.py [THRESHOLD] REPO [REPO ...]      e.g. file-sizes.py 1000 ~/src/*/
"""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

# The repo root, so platformtools imports from here.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platformtools.procname import name_process

SUFFIXES = {".py", ".php", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
            ".sh", ".bash", ".c", ".h", ".cpp", ".hpp", ".cc", ".ino"}
COMMENT_LEADS = ("#", "//", "/*", "*", "--")
_LAYOUT_TOKENS = (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                  tokenize.DEDENT, tokenize.ENDMARKER, tokenize.ENCODING)
_DOC_OWNERS = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

Row = tuple[str, str, int, int, int, int]


def _token_rows(src: str) -> tuple[set[int], set[int]]:
    """(rows holding a comment, rows holding code) from Python's tokenizer."""
    comment_rows: set[int] = set()
    code_rows: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            comment_rows.add(tok.start[0])
        elif tok.type not in _LAYOUT_TOKENS:
            code_rows.update(range(tok.start[0], tok.end[0] + 1))
    return comment_rows, code_rows


def _doc_rows(src: str) -> set[int]:
    """Rows inside a module, class or function docstring."""
    rows: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, _DOC_OWNERS) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                rows.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return rows


def py_split(src: str) -> tuple[int, int, int, int]:
    """(physical, code, comment+docstring, blank) for Python source."""
    lines = src.splitlines()
    blank = sum(1 for line in lines if not line.strip())
    comment_rows, code_rows = _token_rows(src)
    doc_rows = _doc_rows(src)
    return (len(lines), len(code_rows - doc_rows),
            len(comment_rows - code_rows) + len(doc_rows), blank)


def other_split(src: str) -> tuple[int, int, int, int]:
    """Approximate split for everything that is not Python."""
    lines = src.splitlines()
    blank = sum(1 for line in lines if not line.strip())
    comment = sum(1 for line in lines if line.strip().startswith(COMMENT_LEADS))
    return len(lines), len(lines) - blank - comment, comment, blank


def _measure(root: Path, rel: str) -> tuple[str, Row | None]:
    """(outcome, row) for one tracked file."""
    p = root / rel
    if p.suffix.lower() not in SUFFIXES:
        return "not-source", None
    try:
        src = p.read_text(encoding="utf-8", errors="strict")
        split = py_split(src) if p.suffix == ".py" else other_split(src)
    except (OSError, UnicodeDecodeError, SyntaxError, tokenize.TokenError, ValueError):
        # reason: counted as the "unreadable" outcome, which the summary prints
        return "unreadable", None
    return "measured", (root.name, rel, *split)


def _tracked(root: Path) -> list[str] | None:
    r = subprocess.run(["git", "-C", str(root), "ls-files"],
                       capture_output=True, text=True, check=False)
    return r.stdout.splitlines() if r.returncode == 0 else None


def _collect(roots: list[str]) -> tuple[int, dict[str, int], list[Row]]:
    """(readable repos, outcome counts, measured rows) over ROOTS."""
    rows: list[Row] = []
    outcomes = {"measured": 0, "unreadable": 0, "not-source": 0}
    repos = 0
    for root in map(Path, roots):
        files = _tracked(root)
        repos += files is not None
        for rel in files or []:
            outcome, row = _measure(root, rel)
            outcomes[outcome] += 1
            rows += [row] if row else []
    return repos, outcomes, rows


def _report(repos: int, outcomes: dict[str, int], rows: list[Row], threshold: int) -> list[str]:
    big = sorted((x for x in rows if x[2] >= threshold), key=lambda x: -x[2])
    return [
        f"repos {repos}  files: {outcomes}",
        (f"physical >= {threshold}: {len(big)}    "
         f"code-only >= {threshold}: {sum(1 for x in rows if x[3] >= threshold)}"),
        "repo\tpath\tphysical\tcode\tcomment+doc\tblank\tcode%",
        *(f"{repo}\t{rel}\t{phys}\t{code}\t{com}\t{blank}\t{100 * code // max(phys, 1)}"
          for repo, rel, phys, code, com, blank in big),
    ]


def main() -> int:
    name_process("jp-file-sizes")
    args = sys.argv[1:]
    threshold = int(args.pop(0)) if args and args[0].isdigit() else 1000
    repos, outcomes, rows = _collect(args)
    print("\n".join(_report(repos, outcomes, rows, threshold)))
    return 0 if repos else 1


if __name__ == "__main__":
    sys.exit(main())
