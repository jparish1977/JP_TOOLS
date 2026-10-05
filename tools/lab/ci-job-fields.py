#!/usr/bin/env python3
"""Pull check.py's fields out of a GitHub Actions job log.

ANSWERS: what did check.py actually report inside a CI job, per file? Its
  tool statuses, error and warning counts, and any "did not run" line on
  stderr, without reading 400 lines of log. A green job is not the answer: a
  step whose loop kept only the last exit code was green over a failure.

TESTED AGAINST: two GitHub Actions runs of this repo's check job, one before
  and one after a fix. It showed prettier's summary line counted as a file
  (the defect), and then the corrected 6 and 2 warnings.

NOT TESTED AGAINST: Forgejo Actions logs, which prefix lines differently;
  any output but check.py's --pretty JSON.

THE INPUT CAN BE A CONFIDENT NOTHING. `gh run view RUN --log` can return 0
  lines with exit 0, both run-level and per job. So this reports how many
  lines it read, and exits 1 on zero, so an empty log cannot pass for a
  clean one.

usage:
    gh run view RUN -R OWNER/REPO --job JOB --log | tools/lab/ci-job-fields.py [KEY ...]
Default keys: "--- ", "tool", "status", "errors", "warnings", "check.py: ".
"""

import sys
from pathlib import Path

# The repo root, so platformtools imports from here.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platformtools.procname import name_process

DEFAULT_KEYS = ["--- ", '"tool"', '"status"', '"errors"', '"warnings"', "check.py: "]


def main() -> int:
    name_process("jp-ci-fields")
    keys = sys.argv[1:] or DEFAULT_KEYS
    count = 0
    for raw in sys.stdin:
        count += 1
        text = raw.rstrip("\n").split("\t")[-1]
        # Drop the runner's timestamp, "2026-09-14T07:18:49.4041817Z ".
        if text[:4].isdigit() and "Z " in text[:32]:
            text = text.split("Z ", 1)[1]
        if any(k in text for k in keys):
            print(text.strip()[:160])
    print(f"lines read: {count}", file=sys.stderr)
    return 0 if count else 1


if __name__ == "__main__":
    sys.exit(main())
