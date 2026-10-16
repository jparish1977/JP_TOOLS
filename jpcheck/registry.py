"""Which tools run for which language, and how their results are summarised."""

import sys
from typing import Any

import jpexit
from jpcheck.common import _RAN
from jpcheck.coverage import run_no_cover
from jpcheck.directives import run_directive_reason
from jpcheck.js_tools import run_eslint, run_npm_audit, run_prettier, run_stylelint
from jpcheck.native_tools import run_cppcheck, run_psscriptanalyzer, run_shellcheck
from jpcheck.php_tools import run_composer_audit, run_phpcs, run_phpstan, run_rector
from jpcheck.python_tools import run_mypy, run_pip_audit, run_ruff
from jpcheck.smells import run_smells
from jpcheck.suppressions import run_noqa, run_suppress_reason


def _exit_code(summary: dict[str, Any], checks: list[dict[str, Any]]) -> int:
    """jpexit.OK clean, FINDINGS errors found, NOT_RUN a check could not run.

    Errors are counted from issues, so `FINDINGS if errors else OK` alone would let a
    run whose tools were all unavailable exit 0 on a file with real errors. A
    check that did not run is not a pass, so it gets its own code, and it is
    named on stderr so a blocked commit says what to install.

    Findings win over not-run: FINDINGS is already a failure, and the not-run tools
    are still named.
    """
    not_run = [c for c in checks if c.get("status") not in _RAN]
    for c in not_run:
        note = c.get("note", "")
        print(f"check.py: did not run {c.get('tool', '?')} "
              f"({c.get('status', '?')}){': ' + note if note else ''}",
              file=sys.stderr)
    if summary["errors"] > 0:
        return jpexit.FINDINGS
    if not_run:
        print(f"check.py: exit {jpexit.NOT_RUN} -- a check that did not run is not a pass.",
              file=sys.stderr)
        return jpexit.NOT_RUN
    return jpexit.OK


TOOL_RUNNERS = {
    "cppcheck":       run_cppcheck,
    "shellcheck":     run_shellcheck,
    "psscriptanalyzer": run_psscriptanalyzer,
    "no-cover":       run_no_cover,
    "suppress-reason": run_suppress_reason,
    "noqa":           run_noqa,
    "directive-reason": run_directive_reason,
    "smells":         run_smells,
    "ruff":           run_ruff,
    "mypy":           run_mypy,
    "eslint":         run_eslint,
    "stylelint":      run_stylelint,
    "prettier":       run_prettier,
    "phpstan":        run_phpstan,
    "phpcs":          run_phpcs,
    "rector":         run_rector,
    "pip-audit":      run_pip_audit,
    "npm-audit":      run_npm_audit,
    "composer-audit": run_composer_audit,
}

DEFAULT_TOOLS = {
    "python": ["ruff", "mypy", "no-cover", "suppress-reason", "noqa", "smells"],
    # directive-reason: each linter's own suppression directives need a reason,
    # as noqa does for Python (jpcheck/directives.py).
    # smells: every language with code to measure (jpcheck/measure.py).
    "js":     ["eslint", "prettier", "directive-reason", "smells"],
    "css":    ["stylelint", "prettier", "directive-reason"],
    "html":   ["eslint", "stylelint", "prettier", "directive-reason"],
    "php":    ["phpstan", "phpcs", "rector", "directive-reason", "smells"],
    "cpp":    ["cppcheck", "directive-reason", "smells"],
    "shell":  ["shellcheck", "directive-reason", "smells"],
    # No smells: jpcheck/measure.py has no PowerShell measure yet.
    "powershell": ["psscriptanalyzer", "directive-reason"],
}

AUDIT_TOOLS = {
    "python": ["pip-audit"],
    "js":     ["npm-audit"],
    "php":    ["composer-audit"],
}


def serves(name: str, lang: str) -> bool:
    """Whether the tool NAME checks LANG's files: one of its tools or its audit."""
    return name in DEFAULT_TOOLS.get(lang, []) + AUDIT_TOOLS.get(lang, [])

# Tools that accept directories natively (pass the dir, not individual files).
# Not smells: several languages run it, and each run over the whole directory
# would report every language's smells again.
_DIR_CAPABLE = {"ruff", "mypy", "no-cover", "suppress-reason", "noqa", "phpstan",
                "phpcs", "rector", "pip-audit", "npm-audit", "composer-audit"}
# cppcheck is deliberately not in _DIR_CAPABLE: the .ino suppression is decided
# per file, and passing a directory would apply sketch rules to every .cpp.


def _no_runner(name: str) -> dict[str, Any]:
    """The check for a tool name TOOL_RUNNERS does not have: it did not run."""
    return {"tool": name, "status": "unknown", "issues": [], "note": f"No runner for '{name}'"}


def _run_tools(tool_names: list[str], target: str) -> list[dict[str, Any]]:
    checks = []
    for name in tool_names:
        runner = TOOL_RUNNERS.get(name)
        if runner:
            checks.append(runner(target))
        else:
            checks.append(_no_runner(name))
    return checks


def _count(issues: list[dict[str, Any]], key: str, value: object = None) -> int:
    """Issues whose KEY equals VALUE, or is truthy when no VALUE is given."""
    return sum(1 for i in issues if (i.get(key) == value if value is not None else i.get(key)))


def _summarize(checks: list[dict[str, Any]]) -> dict[str, Any]:
    all_issues = [i for c in checks for i in c.get("issues", [])]
    counted = [i for i in all_issues if not i.get("exempt")]
    return {
        "total":    len(all_issues),
        "errors":   _count(counted, "severity", "error"),
        "warnings": _count(counted, "severity", "warning"),
        # Listed in "total" and in the issues, never counted as errors: a lab
        # file under its 2.9 header.
        "exempt":   len(all_issues) - len(counted),
        # Split deliberately: "fixable" is what `fix.py` applies now, and
        # "fixable_unsafe" is what `fix.py --unsafe` would additionally attempt.
        # One combined number would describe work no tool does.
        "fixable":  _count(all_issues, "fixable"),
        "fixable_unsafe": _count(all_issues, "fixable_unsafe"),
    }
