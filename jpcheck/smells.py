"""The smells check: long or complex files and functions, new ones fail and existing ones warn."""

from pathlib import Path
from typing import Any

from jpcheck.common import _git, _NoGitError, _tool_missing
from jpcheck.coverage import is_project_file
from jpcheck.files import _detect_lang
from jpcheck.measure import (
    _MEASURES,
    DEFINITION,
    TOP_LEVEL,
    Measure,
    ToolMissingError,
    file_complexity,
    measure,
    measure_python,
)

# ── smells ────────────────────────────────────────────────────────────────────
# Four smells: a long file, a long function, a complex function, a complex
# file. The base is today: a smell that was not there before this change
# FAILS; one that was there already WARNS. "Before" is the file at the smells
# base: HEAD by default, which is the last commit when the pre-commit hook runs;
# CI on a pull request passes the PR's base with --smells-base.
#
# What is measured, and how, is jpcheck/measure.py: each function, and the
# code outside every function as one more (TOP_LEVEL), by the standard tool of
# the language. The file complexity is its top level plus its functions.
_SMELL_FILE_LINES = 1000        # a smell at or over
_SMELL_FUNC_LINES = 100         # a smell at or over
_SMELL_FUNC_COMPLEXITY = 11     # over 10
_SMELL_FILE_COMPLEXITY = 101    # over 100
_SMELLS = {"base": "HEAD"}      # set from --smells-base in main()


def _smell_measure(src: str) -> Measure:
    """A Python file's measure. Other programs load check.py by path and call this."""
    return measure_python(src)


class _NoSmellsBaseError(Exception):
    """The ref named with --smells-base does not resolve."""


def _smells_context(path: Path) -> tuple[Path, str] | None:
    """(repo root, base ref) when there is history to compare against.

    None outside git, or in a repo with no commits yet: no history, so every
    smell is new. A named base that does not resolve is refused rather than
    guessed at: new could not be told from existing.
    """
    base = _SMELLS["base"]
    top = _git(["rev-parse", "--show-toplevel"], path.resolve().parent)
    if top.returncode != 0:
        return None
    root = Path(top.stdout.strip())
    if _git(["rev-parse", "-q", "--verify", base + "^{commit}"], root).returncode != 0:
        if base == "HEAD":
            return None
        raise _NoSmellsBaseError(base)
    return root, base


def _base_text(root: Path, base: str, rel: str) -> str | None:
    shown = _git(["show", f"{base}:{rel}"], root)
    return shown.stdout if shown.returncode == 0 else None


def _renamed_from(root: Path, base: str, rel: str) -> str | None:
    """The path this file had at the base, when git sees the change as a rename."""
    out = _git(["diff", "-M", "--name-status", "--diff-filter=R", base, "--"], root).stdout
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2] == rel:
            return parts[1]
    return None


def _function_elsewhere(root: Path, base: str, name: str, lang: str) -> tuple[int, int, int]:
    """A function's size and complexity at the base in ANY file of its language,
    for one that moved: splitting a smelly file moves its smells, it does not
    add them. The largest measure found wins."""
    if lang not in DEFINITION:
        return (0, 0, 0)
    pattern, globs = DEFINITION[lang]
    leaf = name.rsplit(".", 1)[-1]
    hits = _git(["grep", "-l", "-E", pattern.format(leaf), base, "--", *globs], root).stdout
    best = (0, 0, 0)
    for hit in hits.splitlines():
        src = _base_text(root, base, hit.split(":", 1)[-1])
        try:
            found = measure(lang, src)[1].get(name) if src is not None else None
        except (SyntaxError, ValueError):
            found = None
        if found:
            best = (max(best[0], found[0]), max(best[1], found[1]), 0)
    return best


def _base_measure(f: Path, ctx: tuple[Path, str] | None) -> tuple[int, dict[str, tuple[int, int, int]]]:
    """The file as it stood at the base, by its path or the path it was renamed from."""
    if ctx is None:
        return 0, {}
    root, base = ctx
    rel = f.resolve().relative_to(root.resolve()).as_posix()
    src = _base_text(root, base, rel)
    if src is None:
        old = _renamed_from(root, base, rel)
        src = _base_text(root, base, old) if old else None
    try:
        return measure(_detect_lang(str(f)), src) if src is not None else (0, {})
    except (SyntaxError, ValueError):
        # reason: a base that will not parse has no smells to compare, so every smell reads NEW, the stricter side
        return 0, {}


def _smell(f: Path, line: int, rule: str, what: str,
           counts: tuple[int, int, int], history: bool = True) -> dict[str, Any] | None:
    """counts = (now, before, limit). None when under the limit now.

    With no history, before is a default and not a measurement: the smell still
    counts as new, since nothing shows it is old, and the message says why."""
    now, before, limit = counts
    if now < limit:
        return None
    new = before < limit
    if not history:
        why = "NEW: no git history here, so its age cannot be told and it counts as new"
    elif new:
        why = f"NEW: was {before} before this change"
    else:
        why = f"existing: was already {before}, so a warning"
    return {
        "file": str(f), "line": line, "col": 0,
        "severity": "error" if new else "warning",
        "rule": rule,
        "message": f"{what} is {now} (a smell at {limit}); {why}",
        "fixable": False, "fixable_unsafe": False,
    }


def _unmeasured(f: Path, why: str) -> dict[str, Any]:
    return {"file": str(f), "line": 1, "col": 0, "severity": "warning",
            "rule": "SMELL-UNMEASURED",
            "message": f"its smells were not measured: {why}",
            "fixable": False, "fixable_unsafe": False}


def _file_smells(f: Path) -> list[dict[str, Any]]:
    """The four smells for one file. A function missing at its own path is
    looked for across the base tree before it counts as new; a function renamed
    in place still counts as new, which is a chance to fix it."""
    lang = _detect_lang(str(f))
    if lang not in _MEASURES:
        return [_unmeasured(f, f"no measure for {lang} files")]
    try:
        lines, funcs = measure(lang, f.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return [_unmeasured(f, "it could not be parsed (its linter reports the parse error)")]
    ctx = _smells_context(f)
    history = ctx is not None
    b_lines, b_funcs = _base_measure(f, ctx)
    found = [
        _smell(f, 1, "SMELL-FILE-LINES", "file length",
               (lines, b_lines, _SMELL_FILE_LINES), history),
        _smell(f, 1, "SMELL-FILE-COMPLEXITY", "file complexity (its top level and functions)",
               (file_complexity(funcs, lang), file_complexity(b_funcs, lang),
                _SMELL_FILE_COMPLEXITY), history),
    ]
    found.extend(_function_smells(f, funcs, b_funcs, ctx))
    return [s for s in found if s is not None]


def _function_smells(f: Path, funcs: dict[str, tuple[int, int, int]],
                     b_funcs: dict[str, tuple[int, int, int]],
                     ctx: tuple[Path, str] | None) -> list[dict[str, Any] | None]:
    """Length and complexity for each function, each against its own before."""
    out: list[dict[str, Any] | None] = []
    for name, (size, cx, line) in funcs.items():
        before = b_funcs.get(name)
        smelly = size >= _SMELL_FUNC_LINES or cx >= _SMELL_FUNC_COMPLEXITY
        if before is None and ctx is not None and smelly and name != TOP_LEVEL:
            before = _function_elsewhere(ctx[0], ctx[1], name, _detect_lang(str(f)))
        b_size, b_cx, _ = before or (0, 0, 0)
        out.append(_smell(f, line, "SMELL-FUNC-LINES", f"function {name} length",
                          (size, b_size, _SMELL_FUNC_LINES), ctx is not None))
        out.append(_smell(f, line, "SMELL-FUNC-COMPLEXITY", f"function {name} complexity",
                          (cx, b_cx, _SMELL_FUNC_COMPLEXITY), ctx is not None))
    return out


def run_smells(target: str) -> dict[str, Any]:
    p = Path(target)
    files = ([f for f in sorted(p.rglob("*")) if f.is_file()
              and is_project_file(f, p) and _detect_lang(str(f)) in _MEASURES]
             if p.is_dir() else [p])
    issues: list[dict[str, Any]] = []
    try:
        for f in files:
            issues.extend(_file_smells(f))
    except _NoSmellsBaseError as e:
        return {"tool": "smells", "status": "error", "issues": [],
                "note": f"--smells-base {e} does not resolve, so a new smell "
                        "cannot be told from an existing one"}
    except _NoGitError:
        # Without git there is no history, so new cannot be told from
        # existing: a check that did not run, never a pass.
        return _tool_missing("git (the smells arm reads history to tell new from existing)")
    except ToolMissingError as e:
        return _tool_missing(str(e), "the smells arm measures complexity with it")
    status = "fail" if any(i["severity"] == "error" for i in issues) else "pass"
    return {"tool": "smells", "status": status, "issues": issues}
