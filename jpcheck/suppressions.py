"""The suppress-reason and noqa rules: a swallowed failure or a silenced finding says why."""

import ast
import io
import re
import sys
import tokenize
from pathlib import Path
from typing import Any

from jpcheck.common import _status
from jpcheck.coverage import _HAS_REASON, is_project_file

# ── suppress-reason ───────────────────────────────────────────────────────────
# A swallowed exception is a decision that a failure does not matter here, and
# the decision is invisible unless the line says why. A reason has to be a real
# reason, not an easy escape route.
#
# The rule is the no-cover rule's: not "no suppression", but that one must SAY
# WHY, on the line, where a reader and this check can see it:
#
#     with contextlib.suppress(OSError):                    # flagged
#     with contextlib.suppress(OSError):  # reason: a stale cache is a miss
#     except OSError:                                       # then pass, continue,
#         pass                                              #   or return <default>: flagged
#     except OSError:  # reason: no ledger dir means no ledger; the caller says so
#         return None
#
# A handler that DOES something (logs, re-raises, sets state and carries on) is
# a decision the code shows and is not this check's business. The reason may
# sit on the `except`/`with` line or on any line of the handler's own body.
#
# TWO THINGS THIS DOES NOT DO:
#   * A reason does NOT satisfy a BLIND catch. `except Exception`,
#     `except BaseException`, a bare `except:` or `suppress(Exception)` that
#     swallows silently is flagged whatever the comment says; the fix is to
#     log it (logging.exception, or exc_info=True) or re-raise, which ruff
#     already accepts as not blind.
#   * It does not double-count what ruff already reports: a bare or blind
#     `except: pass` is S110, and a typed `except X: pass` on a one-statement
#     try is SIM105 (which pushes code into the suppress() form this rule
#     then sees). The new reach is suppress(...) and a handler that returns a
#     default, continues, breaks, or passes after a longer try body.
_BLIND_NAMES = {"Exception", "BaseException"}


def _is_suppress_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
    return name == "suppress"


def _names_blind(node: ast.expr | None) -> bool:
    """True for a bare except, or one naming Exception/BaseException anywhere."""
    if node is None:
        return True
    for n in ast.walk(node):
        name = n.attr if isinstance(n, ast.Attribute) else n.id if isinstance(n, ast.Name) else ""
        if name in _BLIND_NAMES:
            return True
    return False


def _is_literal(node: ast.expr) -> bool:
    """A value written out in full: a constant, `[]`, `{}`, `()`, `-1`, a
    list of constants. `return []` and `return -1` are the commonest silent
    defaults, and a Constant-only test would let them through."""
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        # reason: literal_eval refusing is the answer "not a literal"; the
        # types are what it documents raising on a non-literal expression
        return False
    return True


def _handler_is_silent(h: ast.ExceptHandler) -> bool:
    """True when the handler's body only passes, continues, breaks or returns a
    literal or a bare name: nothing is recorded and nothing is decided."""
    for stmt in h.body:
        if isinstance(stmt, (ast.Pass, ast.Continue, ast.Break)):
            continue
        if isinstance(stmt, ast.Return) and (stmt.value is None or isinstance(stmt.value, ast.Name)
                                             or _is_literal(stmt.value)):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue                                         # a docstring-shaped string
        return False
    return True


def silent_catches(path: Path) -> list[dict[str, Any]]:
    """Every contextlib.suppress and every silent except handler in one file,
    with whether a `reason:` is stated within its span. The inventory, not the
    violations, the same split as coverage_exemptions()."""
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError, ValueError):  # reason: ruff reports an unparseable file; twice is one problem looking like two
        return []
    comments: dict[int, str] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                comments[tok.start[0]] = comments.get(tok.start[0], "") + tok.string
    except (tokenize.TokenError, IndentationError, SyntaxError):  # reason: as above, ruff's to report
        return []

    def reason_in(begin: int, end: int) -> str:
        for row in range(begin, end + 1):
            m = _HAS_REASON.search(comments.get(row, ""))
            if m:
                return comments[row].split("reason:", 1)[1].strip()
        return ""

    # See `ruffs` in _handler_entries: the deferral assumes ruff is reporting,
    # and a noqa is how that assumption stops being true.
    hushed = hushed_lines(path)

    found: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            found.extend(_suppress_entries(path, node, reason_in))
        elif isinstance(node, ast.Try):
            found.extend(_handler_entries(path, node, reason_in, hushed))
    return found


def _suppress_entries(path: Path, node: ast.With | ast.AsyncWith,
                      reason_in: Any) -> list[dict[str, Any]]:
    calls = [i.context_expr for i in node.items if _is_suppress_call(i.context_expr)]
    if not calls:
        return []
    first_body = min(s.lineno for s in node.body)
    return [{
        "file": str(path), "line": node.lineno,
        "what": ", ".join(ast.unparse(c) for c in calls),
        "blind": any(_names_blind(a) for c in calls if isinstance(c, ast.Call) for a in c.args),
        "ruffs": False,
        "reason": reason_in(node.lineno, first_body - 1),
    }]


def _handler_entries(path: Path, node: ast.Try, reason_in: Any,
                     hushed: set[int] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for h in node.handlers:
        if not _handler_is_silent(h):
            continue
        blind = _names_blind(h.type)
        pass_only = len(h.body) == 1 and isinstance(h.body[0], ast.Pass)
        out.append({
            "file": str(path), "line": h.lineno,
            "what": "except " + (ast.unparse(h.type) if h.type is not None else "<bare>"),
            "blind": blind,
            # S110 owns a blind except-pass; SIM105 owns a typed one on a
            # one-statement try. Both are ruff errors already -- UNLESS a noqa
            # on that line has switched ruff off. A deferral to a tool that is
            # not running is not a deferral.
            "ruffs": (pass_only and (blind or len(node.body) == 1)
                      and h.lineno not in (hushed or set())),
            "reason": reason_in(h.lineno, h.end_lineno or h.lineno),
        })
    return out


def _unreasoned_catches(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in silent_catches(path):
        if e["ruffs"] or (e["reason"] and not e["blind"]):
            continue
        if e["blind"]:
            message = (f"'{e['what']}' is blind and swallows the failure. No "
                       "reason excuses a blind catch: log it "
                       "(logging.exception, or exc_info=True) or re-raise")
        else:
            message = (f"'{e['what']}' swallows the failure and says nothing. A "
                       "suppression is a claim that this failure does not "
                       "matter here. State the claim on the line, "
                       "'# reason: ...', or let it raise")
        out.append({
            "file":     e["file"],
            "line":     e["line"],
            "col":      1,
            "severity": "error",
            "rule":     "suppress-reason",
            "message":  message,
            "fixable":        False,
            "fixable_unsafe": False,
        })
    return out


def run_suppress_reason(target: str) -> dict[str, Any]:
    p = Path(target)
    files = ([f for f in sorted(p.rglob("*.py")) if is_project_file(f, p)]
             if p.is_dir() else [p])
    issues: list[dict[str, Any]] = []
    for f in files:
        issues.extend(_unreasoned_catches(f))
    return {"tool": "suppress-reason", "status": _status(issues), "issues": issues}


# ── noqa rules ────────────────────────────────────────────────────────────────
# A branchy `# pragma: no cover` has to say why, so a coverage exemption is a
# claim someone can disagree with. A `# noqa` is held to the same bar: no
# suppression without a real reason.
#
# WHY THIS IS LOAD-BEARING: a blind `except Exception: pass` is two ruff errors
# (S110 and BLE001), and one `# noqa: BLE001,S110` takes it to zero from every
# tool, suppress-reason included, because suppress-reason defers that shape to
# ruff. So whatever rule exists, a noqa turns it off unless the noqa itself is
# checked.
#
# THIS REFUSES AND DOES NOT LIST. A warning per marker would scroll past by the
# hundred on a large repo (structural E402s on imports after a `sys.path`
# insert, for one) and fail nothing. `noqa-no-reason` forces the reason at the
# moment the marker is written, which is when someone reads it. The JS side
# keeps its per-suppression warnings.
#
# THREE THINGS FAIL, and they are deliberately not one rule:
#   * A marker with no reason. For a structural suppression the sentence IS the
#     evidence: `E402` on an import after a run-time `sys.path` insert is a
#     claim about layout that a reader checks by looking, and no amount of
#     logging can express it.
#   * A BLANKET `# noqa` carrying no codes. It suppresses every rule on the
#     line, including ones nobody has written yet, and it cannot be inventoried
#     by code because it names none.
#   * `BLE001` and `S110`, under any reason at all. A reason on the line does
#     not make a blind catch less blind, and the same handler still swallows a
#     `NameError` as an empty result. Ruff treats `logging.exception`, `exc_info=True` and
#     `raise` as not-blind, so the stronger evidence is available and a
#     sentence is not accepted in its place.
#
# `# type: ignore` IS PARSED AND NEVER FAILS. Whether it should be held to the
# same rules is undecided; it is read only because the same scan reads both.
# IGNORECASE because ruff's directive is case-insensitive: the upper-case
# spelling suppresses exactly as the lower-case one does, so a case-sensitive
# match would miss it.
_NOQA_RE = re.compile(
    r"#\s*noqa\b(?::\s*(?P<codes>[A-Za-z]+[0-9]+(?:\s*,\s*[A-Za-z]+[0-9]+)*))?(?P<rest>.*)$",
    re.IGNORECASE)
_TYPE_IGNORE_RE = re.compile(
    r"#\s*type:\s*ignore(?:\[(?P<codes>[^\]]*)\])?(?P<rest>.*)$")
# Prose after `--` (the eslint convention this file already reads) or after
# `reason:` (the no-cover and suppress-reason convention). Both are accepted
# because both are in use, and a third spelling would make every existing
# marker a finding.
_NOQA_REASON_RE = re.compile(r"(?:--|reason:)\s*(?P<why>\S.*)$")
_NOT_EXPLAINABLE = {"BLE001", "S110"}
# The codes silent_catches stands aside for, on the grounds that ruff reports
# them. A noqa naming any of these is how ruff stops reporting them.
_DEFERRED_TO_RUFF = {"S110", "SIM105", "BLE001"}


def _marker_from_comment(tok: tokenize.TokenInfo) -> dict[str, Any] | None:
    """One comment token to a marker, or None when it is not one.

    Split out from noqa_markers so that function stays under the complexity
    smell.
    """
    if not tok.line[:tok.start[1]].strip():
        return None                       # a standalone comment: prose, not a directive
    for kind, rx in (("noqa", _NOQA_RE), ("type-ignore", _TYPE_IGNORE_RE)):
        m = rx.search(tok.string)
        if m is None:
            continue
        why = _NOQA_REASON_RE.search(m.group("rest") or "")
        return {
            "kind":   kind,
            "line":   tok.start[0],
            "col":    tok.start[1] + 1,
            "codes":  [c.strip().upper()
                       for c in (m.group("codes") or "").split(",") if c.strip()],
            "reason": why.group("why").strip() if why else "",
        }
    return None


def hushed_lines(path: Path) -> set[int]:
    """Lines where a noqa has switched ruff off for a code silent_catches defers to."""
    return {m["line"] for m in noqa_markers(path)
            if m["kind"] == "noqa" and (not m["codes"]
                                        or set(m["codes"]) & _DEFERRED_TO_RUFF)}


def noqa_markers(path: Path) -> list[dict[str, Any]]:
    """Every `# noqa` and `# type: ignore` in one file: codes, reason, line.

    The markers, not the violations, the same split as coverage_exemptions()
    and silent_catches(). Two callers: the rules below, and hushed_lines(),
    which is what stops suppress-reason deferring to a silenced ruff.

    READ FROM COMMENT TOKENS, NEVER FROM RAW LINES, AND ONLY TRAILING ONES.
    A line scan matches the marker inside a string literal, and tests carry
    those as fixtures. Tokenizing is not enough on its own, because prose
    about a marker is also a comment: the block above this function mentions
    `# noqa` to explain it.

    So only a marker TRAILING code counts. That is a scoping choice, not a
    claim about ruff: a standalone marker cannot suppress anything for the
    code around it, and requiring a reason on one would refuse every sentence
    written about the subject.

    NOT COVERED: the file-level forms, `# ruff: noqa`, `# flake8: noqa` and
    `# ruff: noqa: CODES`. Each disables a whole file and none matches this
    trailing shape. They are the largest blanket there is and want a rule of
    their own.
    """
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        # Said, not skipped: a file left out of an inventory makes the
        # inventory shorter than the truth and nothing says which file.
        print(f"check.py: {path} unreadable ({e}); NOT inventoried", file=sys.stderr)
        return []
    out: list[dict[str, Any]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                continue
            marker = _marker_from_comment(tok)
            if marker is not None:
                out.append(marker)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # reason: ruff reports a file that will not tokenize, and twice is one
        # problem looking like two; same call as silent_catches makes
        return []
    return out


def _noqa_issues(path: Path) -> list[dict[str, Any]]:
    # WHICH LINES ACTUALLY SWALLOW, so `noqa-not-explainable` fires on a hidden
    # swallow and not on every blind catch. The evidence for a broad catch is
    # THE EXCEPTION RECORDED rather than a sentence, so a handler that records
    # it (a test harness returning `f"{type(e).__name__}: {e}"` as its failure,
    # say) has met that bar, whatever ruff's BLE001 thinks of its breadth.
    # Refusing those buys nothing and spends the gate's credibility, which
    # METHODOLOGY 9.1 says is how --no-verify becomes a habit.
    silent = {e["line"] for e in silent_catches(path)}
    issues: list[dict[str, Any]] = []
    for m in noqa_markers(path):
        codes = m["codes"]
        label = ",".join(codes) if codes else "(every rule)"
        base = {"file": str(path), "line": m["line"], "col": m["col"],
                "fixable": False, "fixable_unsafe": False}
        if m["kind"] == "type-ignore":
            continue
        banned = sorted(set(codes) & _NOT_EXPLAINABLE) if m["line"] in silent else []
        if banned:
            issues.append({**base, "severity": "error", "rule": "noqa-not-explainable",
                           "message": f"'# noqa: {','.join(banned)}' is not available under "
                                      "any reason: this handler swallows the failure, and a "
                                      "sentence is not evidence that it does not matter. "
                                      "Record the exception (logging.exception, or "
                                      "exc_info=True), re-raise, or do something with it -- "
                                      "ruff accepts all three, and a narrower except as well"})
        elif not codes:
            issues.append({**base, "severity": "error", "rule": "noqa-blanket",
                           "message": "a bare '# noqa' suppresses every rule on this line, "
                                      "including ones not written yet, and names none for an "
                                      "inventory to list. Name the codes it is for"})
        elif not m["reason"]:
            issues.append({**base, "severity": "error", "rule": "noqa-no-reason",
                           "message": f"'# noqa: {label}' suppresses a finding and states no "
                                      "reason. A suppression is a claim that the rule is wrong "
                                      "HERE. State it on the line, "
                                      f"'# noqa: {label} -- why', or remove the marker"})
    return issues


def run_noqa(target: str) -> dict[str, Any]:
    p = Path(target)
    files = ([f for f in sorted(p.rglob("*.py")) if is_project_file(f, p)]
             if p.is_dir() else [p])
    issues: list[dict[str, Any]] = []
    for f in files:
        issues.extend(_noqa_issues(f))
    # Every issue this emits is an error today, so _status() would do. Said by
    # severity anyway, because the day something here warns is the day a silent
    # `_status()` would start failing runs that should pass.
    status = "fail" if any(i["severity"] == "error" for i in issues) else "pass"
    return {"tool": "noqa", "status": status, "issues": issues}
