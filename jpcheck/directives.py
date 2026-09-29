"""The directive-reason rule: a suppression a linter honours says why, on its line.

Python has noqa-no-reason and suppress-reason. The other languages' linters
honour directives of their own, and each one turns a finding off with no
reason required:

    # shellcheck disable=SC2086                          shellcheck
    // cppcheck-suppress arrayIndexOutOfBounds           cppcheck
    // @phpstan-ignore-next-line                         phpstan
    // phpcs:ignore Generic.Files.LineLength             phpcs
    // eslint-disable-next-line no-eval                  eslint
    /* stylelint-disable color-no-hex */                 stylelint

A C out-of-bounds write that fails the gate passes with the cppcheck line above
it, and a PHP type error the same with the phpstan one. So each of these fails
here unless the line also says why. A reason is prose after one of the
separators the tools themselves accept without dropping the suppression:

    --  or  reason:          every language (eslint's and phpcs's own form)
    # prose                  shellcheck: a second comment after the directive
    ; prose  or  // prose    cppcheck, after the id
    (prose)                  phpstan's own form: @phpstan-ignore id (why)

A reason does not have to be long; it has to be there, where a reader and this
check can see it. Only directives inside a comment count, so a string that
merely contains the words is not a finding.
"""

import re
from pathlib import Path
from typing import Any

from jpcheck.files import _detect_lang

# A comment opener before the directive on the same line: `#` for shell,
# `//`, `/*` or a docblock's leading `*` for the C-family languages.
_SHELL_OPEN = r"(?:^|\s)#\s*"
_C_OPEN = r"(?://|/\*+|^\s*\*)\s*"

# Each language's directives: (name, pattern). A pattern's named group `rest`
# is whatever follows the directive and its rule ids, where a reason must be.
_DIRECTIVES: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "shell": [
        ("shellcheck disable", re.compile(_SHELL_OPEN + r"shellcheck\s+disable=\S+(?P<rest>.*)$")),
    ],
    "cpp": [
        ("cppcheck-suppress", re.compile(
            _C_OPEN + r"cppcheck-suppress(?:-file|-begin|-macro)?(?:\s*\[[^\]]*\]|\s+\S+)?(?P<rest>.*)$")),
    ],
    "php": [
        ("@phpstan-ignore", re.compile(
            _C_OPEN + r"@phpstan-ignore(?:-next-line|-line)?(?:\s+[\w.,\s-]*?)?(?P<rest>(?:--|reason:|\().*|\s*)$")),
        ("phpcs", re.compile(
            _C_OPEN + r"phpcs:(?:ignore|disable|ignoreFile)(?:\s+[\w.,]+)?(?P<rest>.*)$")),
    ],
    "js": [
        ("eslint-disable", re.compile(
            _C_OPEN + r"eslint-disable(?:-next-line|-line)?(?:\s+[\w@/,\s-]*?)?(?P<rest>(?:--|reason:).*|\s*(?:\*/)?\s*)$")),
    ],
    "css": [
        ("stylelint-disable", re.compile(
            _C_OPEN + r"stylelint-disable(?:-next-line|-line)?(?:\s+[\w@/,\s-]*?)?(?P<rest>(?:--|reason:).*|\s*(?:\*/)?\s*)$")),
    ],
}
# HTML carries both script and style, so both sets of directives.
_DIRECTIVES["html"] = _DIRECTIVES["js"] + _DIRECTIVES["css"]

# The separators each language's tools accept before a reason.
_SEPARATORS = {
    "shell": r"(?:--|reason:|#)",
    "cpp":   r"(?:--|reason:|;|//)",
    "php":   r"(?:--|reason:|\()",
    "js":    r"(?:--|reason:)",
    "css":   r"(?:--|reason:)",
    "html":  r"(?:--|reason:)",
}


def has_reason(rest: str, lang: str) -> bool:
    """Whether REST, the text after a directive, opens with a separator and holds prose."""
    m = re.match(r"\s*" + _SEPARATORS[lang] + r"\s*(?P<why>.*)$", rest)
    return bool(m and re.search(r"[A-Za-z]", m.group("why").rstrip("*/) ")))


def directives(text: str, lang: str) -> list[dict[str, Any]]:
    """Every honoured suppression directive in TEXT for LANG: name, line, reason or not."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for name, rx in _DIRECTIVES.get(lang, []):
            m = rx.search(line)
            if m:
                found.append({"name": name, "line": n,
                              "reasoned": has_reason(m.group("rest"), lang)})
                break
    return found


def run_directive_reason(target: str) -> dict[str, Any]:
    """Fail each suppression directive in the file TARGET that gives no reason.

    The language comes from the file itself, as check.py detects it: suffix,
    or the #! line of an extensionless script.
    """
    lang = _detect_lang(target)
    try:
        text = Path(target).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"tool": "directive-reason", "status": "error", "issues": [],
                "note": f"could not read {target}: {exc.__class__.__name__}"}
    issues = [{
        "file": target, "line": d["line"], "col": 0, "severity": "error",
        "rule": "directive-no-reason",
        "message": f"'{d['name']}' turns a finding off and states no reason. A suppression "
                   "is a claim that the rule is wrong HERE: say why on the line "
                   "(after --, reason:, or the tool's own form), or remove it",
        "fixable": False, "fixable_unsafe": False,
    } for d in directives(text, lang) if not d["reasoned"]]
    return {"tool": "directive-reason", "status": "fail" if issues else "pass",
            "issues": issues}
