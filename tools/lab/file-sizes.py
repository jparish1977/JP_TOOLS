#!/usr/bin/env python3
"""How big are the tracked source files, and how much of each is prose?

ANSWERS: for the proposed file-size smell (METHODOLOGY draft, "Size is a
  smell; responsibility is the rule"), how many tracked source files across
  the given git repos pass a physical-line threshold, and how much of each is
  code versus comments and docstrings.

TESTED AGAINST: 38 repos under ~/projects on iteration8, 2026-09-14, 1,349
  files, 0 unreadable. Positive control: its physical counts for
  JP_TOOLS/check.py (1,062) and projectbook/pbq.py (2,873) matched `wc -l`
  exactly, checked before any number was quoted.

NOT TESTED AGAINST: non-Python classification is approximate. A line whose
  first non-blank characters are #, //, /*, * or -- counts as comment.
  Extensionless scripts are NOT measured, because the file set is chosen by
  suffix, which is check.py's blind spot too. Tracked vendored files are
  included (one of the 25 was a vendored .c). The Python code/doc split was
  never checked against a hand count.

Three outcomes per file: measured, unreadable, not-source. The summary prints
all three, so a zero cannot hide a file that was never read.

usage: file-sizes.py [THRESHOLD] REPO [REPO ...]      e.g. file-sizes.py 1000 ~/projects/*/
"""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

SUFFIXES = {".py", ".php", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
            ".sh", ".bash", ".c", ".h", ".cpp", ".hpp", ".cc", ".ino"}
COMMENT_LEADS = ("#", "//", "/*", "*", "--")


def _name_process(name: bytes) -> None:
    """btop shows this instead of python3 (CLAUDE.md, every script names itself)."""
    try:
        import ctypes
        ctypes.CDLL(None).prctl(15, name, 0, 0, 0)
    except (OSError, AttributeError):
        # reason: no libc or no prctl (named types): the process runs unnamed, which is never fatal
        pass


def py_split(src: str) -> tuple[int, int, int, int]:
    """(physical, code, comment+docstring, blank) for Python source."""
    lines = src.splitlines()
    blank = sum(1 for line in lines if not line.strip())
    comment_rows: set[int] = set()
    code_rows: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            comment_rows.add(tok.start[0])
        elif tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                              tokenize.DEDENT, tokenize.ENDMARKER, tokenize.ENCODING):
            code_rows.update(range(tok.start[0], tok.end[0] + 1))
    doc_rows: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                doc_rows.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return (len(lines), len(code_rows - doc_rows),
            len(comment_rows - code_rows) + len(doc_rows), blank)


def other_split(src: str) -> tuple[int, int, int, int]:
    """Approximate split for everything that is not Python."""
    lines = src.splitlines()
    blank = sum(1 for line in lines if not line.strip())
    comment = sum(1 for line in lines if line.strip().startswith(COMMENT_LEADS))
    return len(lines), len(lines) - blank - comment, comment, blank


def main() -> int:
    _name_process(b"jp-file-sizes")
    args = sys.argv[1:]
    threshold = int(args.pop(0)) if args and args[0].isdigit() else 1000
    rows: list[tuple[str, str, int, int, int, int]] = []
    outcomes = {"measured": 0, "unreadable": 0, "not-source": 0}
    repos = 0
    for arg in args:
        root = Path(arg)
        r = subprocess.run(["git", "-C", str(root), "ls-files"],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            continue
        repos += 1
        for rel in r.stdout.splitlines():
            p = root / rel
            if p.suffix.lower() not in SUFFIXES:
                outcomes["not-source"] += 1
                continue
            try:
                src = p.read_text(encoding="utf-8", errors="strict")
                split = py_split(src) if p.suffix == ".py" else other_split(src)
            except (OSError, UnicodeDecodeError, SyntaxError, tokenize.TokenError, ValueError):
                outcomes["unreadable"] += 1
                continue
            outcomes["measured"] += 1
            rows.append((root.name, rel, *split))
    big = sorted((x for x in rows if x[2] >= threshold), key=lambda x: -x[2])
    print(f"repos {repos}  files: {outcomes}")
    print(f"physical >= {threshold}: {len(big)}    "
          f"code-only >= {threshold}: {sum(1 for x in rows if x[3] >= threshold)}")
    print("repo\tpath\tphysical\tcode\tcomment+doc\tblank\tcode%")
    for repo, rel, phys, code, com, blank in big:
        print(f"{repo}\t{rel}\t{phys}\t{code}\t{com}\t{blank}\t{100 * code // max(phys, 1)}")
    return 0 if repos else 1


if __name__ == "__main__":
    sys.exit(main())
