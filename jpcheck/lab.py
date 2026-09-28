"""Lab files (METHODOLOGY 2.9) are held to their header, not to zero."""

import re
from pathlib import Path
from typing import Any

# A LAB FILE IS HELD TO ITS HEADER, NOT TO ZERO. METHODOLOGY 2.9: a tools/lab
# instrument has no bar at entry ("dusty is fine, lost is not"); what it owes
# is a header saying what it answers, what it was tested against, and what it
# was NOT tested against. Holding lab files to zero would block any edit to one
# with an old finding. "TESTED AGAINST" is a substring of "NOT TESTED AGAINST",
# so a plain `in` test would accept a header missing the tested-against field;
# hence the lookbehind. Exempt findings are still listed.
_LAB_DIR = "lab"
_LAB_TESTED = re.compile(r"(?<!NOT )TESTED AGAINST")
_LAB_EXEMPT_REASON = "lab file under its METHODOLOGY 2.9 header"


def _repo_root_of(p: Path) -> Path | None:
    for parent in p.parents:
        if (parent / ".git").exists():
            return parent
    return None


def lab_claim(p: Path) -> bool:
    """True for a file in a `lab/` directory of its repo whose opening 4000
    characters carry all three 2.9 fields.

    The `lab/` test is relative to the repo root, so a repo that merely lives
    under some directory called `lab` does not exempt all of its files.
    """
    p = p.resolve()
    root = _repo_root_of(p)
    parts = p.relative_to(root).parts[:-1] if root else p.parts[:-1]
    if _LAB_DIR not in parts:
        return False
    try:
        with p.open(encoding="utf-8", errors="replace") as fh:
            head = fh.read(4000)
    except OSError:
        # reason: a file that cannot be read cannot claim the exemption; it
        # is held to zero like any other file
        return False
    return ("ANSWERS" in head and "NOT TESTED AGAINST" in head
            and _LAB_TESTED.search(head) is not None)


def _mark_lab_exempt(checks: list[dict[str, Any]]) -> None:
    """Tag each issue in a lab file under its header as exempt."""
    claims: dict[str, bool] = {}
    for c in checks:
        issues = c.get("issues", [])
        for i in issues:
            f = str(i.get("file", ""))
            if f not in claims:
                claims[f] = bool(f) and lab_claim(Path(f))
            if claims[f]:
                i["exempt"] = _LAB_EXEMPT_REASON
        if c.get("status") == "fail" and issues and all(i.get("exempt") for i in issues):
            c["status"] = "pass"
