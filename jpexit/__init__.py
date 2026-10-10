"""Exit codes JP_TOOLS' scripts share, named once.

A caller (a pre-commit hook, CI, a seat's script) reads these to tell "it ran
and all is well" from "it ran and found something" from "it did not run, or
did not finish". The last must never read as the first: a check or a scan
that did not happen is not a pass. Each value is a contract with whatever runs
these scripts, so tests/test_exit_codes.py pins it, and a script imports the
name rather than writing the number.

A script whose usage documents its own codes keeps them; these are the
meanings it reaches for when it has one of the first three to say.
"""

OK = 0
"""It ran to the end, and nothing it reports was wrong."""

FINDINGS = 1
"""It ran to the end and found what it reports as wrong: check.py's errors, or
a file worse than its baseline."""

NOT_RUN = 2
"""A step did not run or did not finish: a usage or path error, a tool not
installed, a tool that stopped short of a verdict, a baseline that cannot be
compared or written, a read that failed part-way. Never a pass, and what did
not run is named on stderr."""

NOT_LOWERED = 3
"""check.py --baseline --touched only: nothing got worse, but the file the
commit changes had recorded findings and fixed none. A failure to any caller
that reads non-zero as one; the pre-commit hook tells it from FINDINGS
because the commit message can still let it through with LINT-DEBT-OK."""
