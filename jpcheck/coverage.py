"""The no-cover rule: every branchy coverage exemption says why."""

import ast
import io
import re
import tokenize
from pathlib import Path
from typing import Any

from jpcheck.common import _status
from jpcheck.files import _SKIP_DIRS

# A coverage exemption is a CLAIM: that the function is a thin wrapper with
# nothing in it worth testing. Nothing rechecks that claim, so it goes stale
# silently as the function grows, and the exempt region becomes the region
# nobody looks at. A thin wrapper has no branches; an exempt region full of
# branches, loops and try blocks is untested decision-making, which ruff and
# mypy do not see. See docs/coverage.md.
#
# The rule is not "no exemptions". It is that an exemption must SAY WHY, so the
# claim is visible and can be disagreed with:
#
#     def _unlink(p): ...                    # pragma: no cover
#     def restart():  ...                    # pragma: no cover -- reason: runs systemctl
#
# A pragma on a function with no branches needs no reason: it is self-evidently
# a wrapper. One with branches is making a decision, and decisions are testable.
_NO_COVER = re.compile(r"#\s*pragma:\s*no\s*cover(?P<rest>.*)$")
_HAS_REASON = re.compile(r"reason:\s*\S")
_BRANCHY = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try)


def coverage_exemptions(path: Path) -> list[dict[str, Any]]:
    """Every `# pragma: no cover` in one file, with the facts about each.

    The inventory, not the violations. Both the `no-cover` check and
    list-exemptions.py read this, so there is ONE implementation of "what
    counts as an exemption" rather than two that must agree.

    Reporting only violations is not enough on its own: a check can report
    "0 unjustified" over reasons three words long that justify nothing, and
    only the inventory shows them to a reader.

    EVERY pragma line is scanned, not just function signatures, so a pragma on
    a class, on a bare statement or at module level is seen by the gate and
    the inventory alike. A checker that silently cannot see a construct would
    print "No coverage exemptions" as though it had looked.
    """
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
        directives = _trailing_comments(src)
    except (OSError, SyntaxError, ValueError, tokenize.TokenError):
        # reason: not this check's job to report an unreadable or unparseable
        # file; ruff already does, and twice would make one problem look like two
        return []
    owners = _owners(tree)
    found: list[dict[str, Any]] = []
    for lineno, text in directives:
        match = _NO_COVER.search(text)
        if not match:
            continue
        entry = _exemption(path, lineno, match.group("rest"), owners)
        # One entry per owner. Two pragmas inside the same function are one
        # exemption, and counting the span twice would overstate the share of
        # the file that is exempt.
        if not any(e["function"] == entry["function"] and e["line"] == entry["line"]
                   for e in found):
            found.append(entry)
    return found


def _trailing_comments(src: str) -> list[tuple[int, str]]:
    """(row, text) of each comment that trails code on its line.

    Only a trailing comment on a LINE OF CODE is a directive: prose that
    documents the pragma, like the comment block above, is not one.
    Tokenising also excludes docstrings. IndentationError and TokenError
    propagate for the caller to treat as unparseable.
    """
    code_rows: set[int] = set()
    comments: list[tuple[int, str]] = []
    layout = (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
              tokenize.DEDENT, tokenize.ENDMARKER, tokenize.ENCODING)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            comments.append((tok.start[0], tok.string))
        elif tok.type not in layout:
            code_rows.update(range(tok.start[0], tok.end[0] + 1))
    return [(row, text) for row, text in comments if row in code_rows]


def _owners(tree: ast.AST) -> list[tuple[int, int, str, int]]:
    """(first line, last line, label, branch count) for every construct a pragma can sit in."""
    owners = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            label = node.name
        elif isinstance(node, _BRANCHY):
            label = f"<{type(node).__name__.lower()} line {node.lineno}>"
        else:
            continue
        owners.append((node.lineno, node.end_lineno or node.lineno, label,
                       sum(isinstance(n, _BRANCHY) for n in ast.walk(node))))
    return owners


def _exemption(path: Path, lineno: int, rest: str,
               owners: list[tuple[int, int, str, int]]) -> dict[str, Any]:
    """The exemption a pragma on LINENO makes, owned by the tightest construct holding it."""
    reason = rest.split("reason:", 1)[1].strip() if _HAS_REASON.search(rest) else ""
    holding = [o for o in owners if o[0] <= lineno <= o[1]]
    if holding:
        begin, finish, label, branches = min(holding, key=lambda o: o[1] - o[0])
    else:
        begin, finish, label, branches = lineno, lineno, "<module level>", 0
    return {"file": str(path), "line": begin, "function": label,
            "lines": finish - begin, "branches": branches, "reason": reason}


def _branchy_no_cover(path: Path) -> list[dict[str, Any]]:
    return [
        {
            "file":     e["file"],
            "line":     e["line"],
            "col":      1,
            "severity": "error",
            "rule":     "no-cover-branchy",
            "message":  (
                f"'{e['function']}' is exempt from coverage but contains "
                f"{e['branches']} branch/loop/try statement(s) over "
                f"{e['lines']} lines. An exemption claims there is nothing to "
                "test. Extract the decision, or state the claim: "
                "'# pragma: no cover -- reason: ...'"
            ),
            "fixable":        False,
            "fixable_unsafe": False,
        }
        for e in coverage_exemptions(path)
        if e["branches"] and not e["reason"]
    ]


def is_project_file(p: Path, root: Path) -> bool:
    """Is this a file of the project rooted at `root`, not a dependency?

    A venv inside the repo (docs/coverage.md creates one) would otherwise put
    site-packages in the walk. pyvenv.cfg catches venvs under any name, which
    is the case a list of directory names misses.
    """
    rel = p.relative_to(root) if p.is_relative_to(root) else p
    for part in rel.parts[:-1]:
        if part in _SKIP_DIRS:
            return False
    probe = p.parent
    while probe != root and probe.parent != probe:
        if (probe / "pyvenv.cfg").exists():
            return False
        probe = probe.parent
    return not (root / "pyvenv.cfg").exists() if probe == root else True


def run_no_cover(target: str) -> dict[str, Any]:
    p = Path(target)
    files = ([f for f in sorted(p.rglob("*.py")) if is_project_file(f, p)]
             if p.is_dir() else [p])
    issues: list[dict[str, Any]] = []
    for f in files:
        issues.extend(_branchy_no_cover(f))
    return {"tool": "no-cover", "status": _status(issues), "issues": issues}
