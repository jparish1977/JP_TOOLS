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
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '')]
                                                         PSScriptAnalyzer

A C out-of-bounds write that fails the gate passes with the cppcheck line above
it, and a PHP type error the same with the phpstan one. So each of these fails
here unless the line also says why.

A REASON IS ACCEPTED ONLY IN A FORM ITS OWN TOOL HONOURS. Each form below was
run through the tool, with a control the directive must silence. A form a tool
does not honour is worse than no reason: `-- why` after a shellcheck
directive is a parse error, and `reason: why` after an eslint, stylelint or
phpcs directive makes the tool drop the suppression while the line reads as
explained. tests/test_directive_forms_real_tools.py re-runs every form.

    # shellcheck disable=SC2086  # why                   a second comment
    // cppcheck-suppress id ; why     (or // why, -- why)
    // @phpstan-ignore-next-line -- why                  (and -line)
    // @phpstan-ignore identifier (why)                  parentheses only
    // phpcs:ignore Sniff.Name -- why                    (and disable)
    // eslint-disable-next-line rule -- why              (every eslint-disable)
    /* stylelint-disable rule -- why */                  (every stylelint-disable)
    [...SuppressMessageAttribute('Rule', '', Justification = 'why')]

A reason does not have to be long; it has to be there, in letters.

WHAT IS MATCHED: a shellcheck directive only on a line of its own, which is
the only place shellcheck honours one (after a command it is a parse error),
so a string holding the words is not a match. The C-family directives need a
comment opener before them on the line; text in a string that also holds
`//` or `/*` before the words still matches, and fails closed, as does a
heredoc line that starts with `# shellcheck`.
"""

import re
from pathlib import Path
from typing import Any

from jpcheck.common import _verdict
from jpcheck.files import _detect_lang

# A comment opener before a C-family directive on the same line: `//`, `/*`,
# or a docblock's leading `*`.
_C_OPEN = r"(?://|/\*+|^\s*\*)\s*"

# Prose after `--`, which eslint, stylelint, phpcs and phpstan's line forms
# all honour; a closing `*/` is not part of it.
_DASHES = r"--\s*(?P<why>.*?)\s*(?:\*/)?\s*$"


class Directive:
    """One kind of suppression directive: how to find it, and its tool's reason form."""

    def __init__(self, name: str, pattern: str, reason: str) -> None:
        self.name = name
        self.pattern = re.compile(pattern)
        self.reason = re.compile(reason)

    def reasoned(self, rest: str) -> bool:
        """Whether REST, the text after the directive, holds a reason in the honoured form."""
        m = self.reason.search(rest)
        return bool(m and re.search(r"[A-Za-z]", m.group("why")))


_SHELLCHECK = Directive("shellcheck disable",
                        r"^\s*#\s*shellcheck\s+disable=\S+(?P<rest>.*)$",
                        r"^\s+#\s*(?P<why>.*)$")
_CPPCHECK = Directive("cppcheck-suppress",
                      _C_OPEN + r"cppcheck-suppress(?:-file|-begin|-macro)?"
                                r"(?:\s*\[[^\]]*\]|\s+[\w:.-]+)?(?P<rest>.*)$",
                      r"^\s*(?:;|//|--)\s*(?P<why>.*)$")
_PHPSTAN_LINE = Directive("@phpstan-ignore-next-line",
                          _C_OPEN + r"@phpstan-ignore-(?:next-line|line)\b(?P<rest>.*)$",
                          _DASHES)
_PHPSTAN_ID = Directive("@phpstan-ignore",
                        _C_OPEN + r"@phpstan-ignore(?![\w-])(?P<rest>.*)$",
                        r"\(\s*(?P<why>[^)]*)\)")
_PHPCS = Directive("phpcs",
                   _C_OPEN + r"phpcs:(?:ignore|disable|ignoreFile)\b(?P<rest>.*)$",
                   _DASHES)
_ESLINT = Directive("eslint-disable",
                    _C_OPEN + r"eslint-disable(?:-next-line|-line)?\b(?P<rest>.*)$",
                    _DASHES)
_STYLELINT = Directive("stylelint-disable",
                       _C_OPEN + r"stylelint-disable(?:-next-line|-line)?\b(?P<rest>.*)$",
                       _DASHES)

# PSScriptAnalyzer's suppression is an attribute, and it silences the rule
# whether or not it carries a Justification (measured, 1.25.0), so the
# Justification is the reason this requires. Matched on one line; an
# attribute split over several lines reads as unreasoned, failing closed.
_PSSA = Directive("SuppressMessageAttribute",
                  r"\[\s*(?:System\.)?Diagnostics\.CodeAnalysis\.SuppressMessage(?:Attribute)?"
                  r"\s*\((?P<rest>.*)$",
                  r"Justification\s*=\s*(?P<q>['\"])(?P<why>.*?)(?P=q)")

_DIRECTIVES: dict[str, list[Directive]] = {
    "shell": [_SHELLCHECK],
    "powershell": [_PSSA],
    "cpp":   [_CPPCHECK],
    "php":   [_PHPSTAN_LINE, _PHPSTAN_ID, _PHPCS],
    "js":    [_ESLINT],
    "css":   [_STYLELINT],
    # HTML carries both script and style.
    "html":  [_ESLINT, _STYLELINT],
}


def _written(d: Directive, m: re.Match[str]) -> tuple[str, str]:
    """(the directive as written, without its comment marks or its reason;
    its reason, or "" when it states none in its tool's form)."""
    rest = m.group("rest")
    why = d.reason.search(rest)
    keep = rest[:why.start()] if why else rest
    head = m.group(0)[:len(m.group(0)) - len(rest)] + keep
    return (head.strip().lstrip("#/* ").rstrip(" */;-(#"),
            why.group("why").strip() if why and d.reasoned(rest) else "")


def directives(text: str, lang: str) -> list[dict[str, Any]]:
    """Every honoured suppression directive in TEXT for LANG: name, line, whether
    it states a reason, and for an inventory, the directive as written and the reason."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for d in _DIRECTIVES.get(lang, []):
            m = d.pattern.search(line)
            if m:
                written, reason = _written(d, m)
                found.append({"name": d.name, "line": n, "reasoned": d.reasoned(m.group("rest")),
                              "directive": written, "reason": reason})
                break
    return found


# What each directive's reason looks like, for the message that asks for one.
_FORM = {
    "shellcheck disable": "a second comment: '# shellcheck disable=SC2086  # why'",
    "cppcheck-suppress": "'// cppcheck-suppress id ; why'",
    "@phpstan-ignore-next-line": "'// @phpstan-ignore-next-line -- why'",
    "@phpstan-ignore": "'// @phpstan-ignore identifier (why)'",
    "phpcs": "'// phpcs:ignore Sniff.Name -- why'",
    "eslint-disable": "'// eslint-disable-next-line rule -- why'",
    "stylelint-disable": "'/* stylelint-disable rule -- why */'",
    "SuppressMessageAttribute": ("[Diagnostics.CodeAnalysis.SuppressMessageAttribute('Rule', '', "
                                 "Justification = 'why')] on one line"),
}


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
        "message": f"'{d['name']}' turns a finding off and states no reason in a form its "
                   f"tool honours. Say why as {_FORM[d['name']]}, or remove it",
        "fixable": False, "fixable_unsafe": False,
    } for d in directives(text, lang) if not d["reasoned"]]
    return {"tool": "directive-reason", "status": _verdict(issues), "issues": issues}
