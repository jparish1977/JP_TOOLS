#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_ci_smells_base.py
CI judges a smell against the PR's base, not against the commit under test.

check.py's smells arm fails a smell that is NEW against --smells-base and
warns on one that already existed (#55). Its default base is HEAD, which is
right for the pre-commit hook and wrong in CI: there the change is already
committed, so HEAD is the commit under test, every new smell reads as
existing, and it only ever warns. And actions/checkout is shallow by default,
so a PR's base commit is not in the clone to compare against.

So every checkout in these workflows fetches full history, every check.py
call passes --smells-base, and the base comes from the pull request. This
reads the workflow files; it does not run CI. A planted workflow without them
must fail, or the checks below could never fail.

    python3 tests/test_ci_smells_base.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = (ROOT / ".github" / "workflows" / "check.yml", ROOT / "templates" / "ci-check.yml")
CALL = re.compile(r"^\s*python3?\s+\S*check\.py\s")
PR_BASE = "github.event.pull_request.base.sha"

BAD = """\
    steps:
      - uses: actions/checkout@v6
      - name: Run check
        run: |
          python ../JP_TOOLS/check.py . --pretty
"""

fails = 0
checks = 0


def check(what: str, ok: bool, detail: str = "") -> None:
    global fails, checks
    checks += 1
    if not ok:
        fails += 1
    print(f"  [{' ok ' if ok else 'FAIL'}] {what}")
    if not ok and detail:
        for line in detail.strip().splitlines()[:12]:
            print(f"         {line}")


def problems(text: str) -> list[str]:
    """What a workflow is missing for the smells arm to judge against a base."""
    lines = text.splitlines()
    found = []
    for n, line in enumerate(lines, 1):
        if CALL.match(line) and "--smells-base" not in line:
            found.append(f"line {n}: check.py without --smells-base")
        if "uses: actions/checkout" in line and not any(
                "fetch-depth: 0" in later for later in lines[n:n + 3]):
            found.append(f"line {n}: a shallow checkout")
    if PR_BASE not in text:
        found.append(f"no {PR_BASE}: nothing names the PR's base")
    return found


def main() -> int:
    print("CONTROL, a planted workflow:")
    bad = problems(BAD)
    check("a shallow checkout, a bare check.py and no PR base are all reported",
          len(bad) == 3, "\n".join(bad))
    print("\nThe real workflows:")
    for wf in WORKFLOWS:
        text = wf.read_text(encoding="utf-8")
        calls = sum(1 for line in text.splitlines() if CALL.match(line))
        found = problems(text)
        check(f"{wf.relative_to(ROOT)}: {calls} check.py call(s), each judged against the base",
              calls > 0 and not found, "\n".join(found))
    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
