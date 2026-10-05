#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_ci_smells_base.py
CI judges a smell against the PR's base, not against the commit under test.

check.py's smells arm fails a smell that is NEW against --smells-base and
warns on one that already existed. Its default base is HEAD, which is
right for the pre-commit hook and wrong in CI: there the change is already
committed, so HEAD is the commit under test, every new smell reads as
existing, and it only ever warns. And actions/checkout is shallow by default,
so a PR's base commit is not in the clone to compare against.

So in every job that runs check.py, the checkout of the repository under
test fetches full history; every check.py call passes --smells-base; and the
base comes from the pull request. A checkout of ANOTHER repository
(`repository:`, such as JP_TOOLS itself in the template) is the tool, not the
code judged, and needs no history; nor does a job that never runs check.py
(the template's tests job), where a full clone would only cost every run. This
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
JOB_HEAD = re.compile(r"^  [A-Za-z_][\w-]*:\s*$")  # a job's key, two spaces in under `jobs:`
PR_BASE = "github.event.pull_request.base.sha"

# Two jobs: one runs check.py over a shallow checkout (reported), one runs no
# check.py and checks out shallow too (not reported: it never reads history).
TWO_JOBS = """\
on: {pull_request: {}}
jobs:
  check:
    steps:
      - uses: actions/checkout@v6
      - run: |
          python ../JP_TOOLS/check.py . --smells-base ${{ github.event.pull_request.base.sha }}
  tests:
    steps:
      - uses: actions/checkout@v6
      - run: ./tests/run
"""

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


def job_spans(lines: list[str]) -> list[range]:
    """Line indexes of each job under `jobs:`; the whole text when there is no
    `jobs:` (a planted fragment), so every line belongs to one span."""
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == "jobs:")
    except StopIteration:
        return [range(len(lines))]
    heads = [i for i in range(start + 1, len(lines)) if JOB_HEAD.match(lines[i])]
    return [range(h, nxt) for h, nxt in zip(heads, [*heads[1:], len(lines)], strict=True)]


def problems(text: str) -> list[str]:
    """What a workflow is missing for the smells arm to judge against a base."""
    lines = text.splitlines()
    found = []
    for n, line in enumerate(lines, 1):
        if CALL.match(line) and "--smells-base" not in line:
            found.append(f"line {n}: check.py without --smells-base")
    for span in job_spans(lines):
        if not any(CALL.match(lines[i]) for i in span):
            continue  # this job runs no check.py, so its history is never read
        for i in span:
            step = lines[i + 1:i + 5]
            if ("uses: actions/checkout" in lines[i]
                    and not any("repository:" in later for later in step)
                    and not any("fetch-depth: 0" in later for later in step)):
                found.append(f"line {i + 1}: a shallow checkout")
    if PR_BASE not in text:
        found.append(f"no {PR_BASE}: nothing names the PR's base")
    return found


def main() -> int:
    print("CONTROL, a planted workflow:")
    bad = problems(BAD)
    check("a shallow checkout, a bare check.py and no PR base are all reported",
          len(bad) == 3, "\n".join(bad))
    other = BAD.replace("      - uses: actions/checkout@v6\n",
                        "      - uses: actions/checkout@v6\n"
                        "        with:\n          fetch-depth: 0\n"
                        "      - uses: actions/checkout@v6\n"
                        "        with:\n          repository: owner/tool\n")
    found = problems(other)
    check("a shallow checkout of ANOTHER repository is not reported; the rest still are",
          not any("shallow" in f for f in found) and len(found) == 2, "\n".join(found))
    found = problems(TWO_JOBS)
    check("a shallow checkout in a job that runs check.py is reported, one in a job that does not is not",
          found == ["line 5: a shallow checkout"], "\n".join(found))
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
