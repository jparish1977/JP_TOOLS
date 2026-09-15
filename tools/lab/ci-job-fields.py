#!/usr/bin/env python3
"""Pull check.py's fields out of a GitHub Actions job log.

ANSWERS: what did check.py actually report inside a CI job, per file? Its
  tool statuses, error and warning counts, and any "did not run" line on
  stderr, without reading 400 lines of log. A green job is not the answer: a
  step whose loop kept only the last exit code was green over a failure.

TESTED AGAINST: JP_TOOLS PR #59, runs 34817262214 and 34818216343,
  2026-09-14. It showed prettier's summary line counted as a file, the defect
  behind that PR's second commit, and then the corrected 6 and 2 warnings.

NOT TESTED AGAINST: Forgejo Actions logs, which prefix lines differently;
  any output but check.py's --pretty JSON.

THE INPUT CAN BE A CONFIDENT NOTHING. `gh run view RUN --log` returned 0 lines
  with exit 0 for master's run 33540757691, both run-level and per job. So this
  reports how many lines it read, and exits 1 on zero, so an empty log cannot
  pass for a clean one.

usage:
    gh run view RUN -R OWNER/REPO --job JOB --log | tools/lab/ci-job-fields.py [KEY ...]
Default keys: "--- ", "tool", "status", "errors", "warnings", "check.py: ".
"""

import sys

DEFAULT_KEYS = ["--- ", '"tool"', '"status"', '"errors"', '"warnings"', "check.py: "]


def _name_process(name: bytes) -> None:
    """btop shows this instead of python3 (CLAUDE.md, every script names itself)."""
    try:
        import ctypes
        ctypes.CDLL(None).prctl(15, name, 0, 0, 0)
    except (OSError, AttributeError):
        # reason: no libc or no prctl (named types): the process runs unnamed, which is never fatal
        pass


def main() -> int:
    _name_process(b"jp-ci-fields")
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
