#!/usr/bin/env python3
# AS RUN: this is sizes.py exactly as it ran in session 6fbf59c4 (jp-tools) scratch,
# 2026-09-14 about 03:01 local, for the file-size draft (#62). tools/lab/file-sizes.py
# is its later form; they differ, so both are kept (METHODOLOGY 2.9: lost is not fine).
"""How big are the tracked source files, and how much of that is prose?

Question it answers: for a proposed file-size rule, how many tracked source
files across the given git repos exceed a physical-line threshold, and how
much of each is code versus comments and docstrings.

Tested against: check.py on JP_TOOLS origin/master, whose physical count (1405)
is known from `git show | wc -l`, so the physical column has a positive control.

NOT tested against: non-Python classification is approximate (a line whose
first non-blank characters are #, //, /*, * or --  counts as comment); extensionless
scripts are NOT measured, because the file set is chosen by suffix, which is the
same blind spot check.py has. Generated and vendored files are included if they
are tracked.

Three outcomes per file: measured, unreadable, not-source. The summary reports
all three so a zero cannot hide a file that was never read.
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
THRESHOLD = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1000


def py_split(src: str) -> tuple[int, int, int, int]:
    lines = src.splitlines()
    physical = len(lines)
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
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
                doc_rows.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    code_only = code_rows - doc_rows
    comment_only = comment_rows - code_rows
    return physical, len(code_only), len(comment_only) + len(doc_rows), blank


def other_split(src: str) -> tuple[int, int, int, int]:
    lines = src.splitlines()
    blank = sum(1 for line in lines if not line.strip())
    comment = sum(1 for line in lines if line.strip().startswith(COMMENT_LEADS))
    return len(lines), len(lines) - blank - comment, comment, blank


def main() -> int:
    roots = [Path(a) for a in sys.argv[1:] if not a.isdigit()]
    rows, outcomes = [], {"measured": 0, "unreadable": 0, "not-source": 0}
    repos_seen = 0
    for root in roots:
        r = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=False)
        if r.returncode != 0:
            continue
        repos_seen += 1
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
    big = sorted((x for x in rows if x[2] >= THRESHOLD), key=lambda x: -x[2])
    big_code = [x for x in rows if x[3] >= THRESHOLD]
    print(f"repos {repos_seen}  files: {outcomes}")
    print(f"physical >= {THRESHOLD}: {len(big)}    code-only >= {THRESHOLD}: {len(big_code)}")
    print("repo\tpath\tphysical\tcode\tcomment+doc\tblank\tcode%")
    for repo, rel, phys, code, com, blank in big:
        print(f"{repo}\t{rel}\t{phys}\t{code}\t{com}\t{blank}\t{100 * code // max(phys, 1)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
