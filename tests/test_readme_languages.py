#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_readme_languages.py
The README's table of languages and extensions is the code's, and stays so.

WHAT IS CHECKED
    The extensions in the README's "Multi-Language Directory Scanning" table
    are exactly the extensions check.py maps to a language (_EXT_TO_LANG in
    jpcheck/files.py), each under its language: a copy is allowed only when
    something fails when it drifts (METHODOLOGY 2.13). A control: a table with
    one extension removed is reported.

    python3 tests/test_readme_languages.py
"""

import re
import sys

from _harness import ROOT, check, run
from jpcheck.files import _EXT_TO_LANG

# The README's row label for each language check.py names.
LABELS = {"python": "Python", "js": "JavaScript / TypeScript", "css": "CSS", "html": "HTML",
          "php": "PHP", "shell": "Shell", "powershell": "PowerShell", "cpp": "C / C++"}


def table(text: str) -> dict[str, set[str]]:
    """{row label: its extensions} from the README's language table."""
    section = text.split("## Multi-Language Directory Scanning", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, set[str]] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] in LABELS.values():
            rows[cells[0]] = set(re.findall(r"`(\.[\w]+)`", cells[1]))
    return rows


def expected() -> dict[str, set[str]]:
    rows: dict[str, set[str]] = {}
    for ext, lang in _EXT_TO_LANG.items():
        rows.setdefault(LABELS[lang], set()).add(ext)
    return rows


def test_the_readme_table_is_the_codes() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    check("the README's extensions per language are _EXT_TO_LANG's", table(readme), expected())
    broken = readme.replace("`.htm`", "")
    check("control: a table missing .htm is reported", table(broken) == expected(), False)


if __name__ == "__main__":
    sys.exit(run(globals(), "README languages"))
