"""The no-cover rule: every branchy coverage exemption says why."""

import ast
import io
import re
import tokenize
from pathlib import Path
from typing import Any

from jpcheck.common import _status

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
    except (OSError, SyntaxError, ValueError):
        # reason: not this check's job to report an unparseable file; ruff already does (below)
        # Not this check's job to report unparseable files; ruff already does,
        # and reporting it twice makes one problem look like two.
        return []
    # Only a trailing comment on a LINE OF CODE is a directive: prose that
    # documents the pragma, like the comment block above, is not one.
    # Tokenising also excludes docstrings.
    code_rows: set[int] = set()
    comments: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                comments.append((tok.start[0], tok.string))
            elif tok.type not in (
                tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                tokenize.DEDENT, tokenize.ENDMARKER, tokenize.ENCODING,
            ):
                for row in range(tok.start[0], tok.end[0] + 1):
                    code_rows.add(row)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # reason: as above: a file the tokenizer refuses is ruff's to report, and this lists nothing
        return []

    # Every construct a pragma could sit inside, so the innermost one can be
    # named. Sorted by span so the tightest enclosing owner wins.
    owners: list[tuple[int, int, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            label = node.name
        elif isinstance(node, _BRANCHY):
            label = f"<{type(node).__name__.lower()} line {node.lineno}>"
        else:
            continue
        end = node.end_lineno or node.lineno
        owners.append((
            node.lineno, end, label,
            sum(isinstance(n, _BRANCHY) for n in ast.walk(node)),
        ))

    found: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for lineno, text in comments:
        if lineno not in code_rows:
            continue
        match = _NO_COVER.search(text)
        if not match:
            continue
        rest = match.group("rest")
        reason = rest.split("reason:", 1)[1].strip() if _HAS_REASON.search(rest) else ""
        holding = [o for o in owners if o[0] <= lineno <= o[1]]
        if holding:
            begin, finish, label, branches = min(holding, key=lambda o: o[1] - o[0])
        else:
            begin, finish, label, branches = lineno, lineno, "<module level>", 0
        # One entry per owner. Two pragmas inside the same function are one
        # exemption, and counting the span twice would overstate the share of
        # the file that is exempt.
        if (label, begin) in seen:
            continue
        seen.add((label, begin))
        found.append({
            "file":     str(path),
            "line":     begin,
            "function": label,
            "lines":    finish - begin,
            "branches": branches,
            "reason":   reason,
        })
    return found


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


# Directories that are not this project's source. docs/coverage.md tells the
# reader to run `python3 -m venv .venv` in the repo root, so following the
# instructions in this repo put third-party code in the walk: the exemption
# share became a number about site-packages. pyvenv.cfg catches venvs under any
# name, which is the case a hardcoded list misses.
_SKIP_DIRS = {".git", "__pycache__", "node_modules", "vendor", ".mypy_cache",
              ".ruff_cache", ".tox", "site-packages"}


def is_project_file(p: Path, root: Path) -> bool:
    """Is this a file of the project rooted at `root`, not a dependency?"""
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
