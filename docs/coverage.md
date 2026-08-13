# Coverage

Status: **wired**, 2026-08-13. Before that date, nothing in this repo measured
coverage while the source carried `# pragma: no cover` markers, so every marker
suppressed a measurement nobody took. See the history section below, because
the reason it went unnoticed is more useful than the fact.

## How it runs

CI runs the suites **under** coverage, on the same pass that gates them, so
nothing runs twice. Config is `.coveragerc`.

```
python -m coverage run tests/test_<name>.py      # each .py suite
PYTHON=python bash tests/test_<name>.sh          # each .sh suite
python -m coverage combine
python -m coverage report --include='spool-audit.py' --fail-under=80
```

Locally, coverage is not a dependency of running the suites. Install it in a
venv if you want the numbers:

```
python3 -m venv .venv && .venv/bin/pip install coverage
```

## Three decisions worth knowing about

**1. Subprocesses are traced, and it matters more than it sounds.**

`tests/test_spool_audit_invariant.py` and `tests/test_spool_audit_acceptance.sh`
drive the tool as a subprocess, which coverage does not follow by default.
Measured 2026-08-13: without subprocess tracing `spool-audit.py` read **71%**,
with it **82%**. Eleven points of genuine testing would have looked like a gap,
and the obvious remedy for a gap is to add a pragma. A misconfigured measurement
would have argued for making the code less visible.

It needs three things together, and any one missing gives silent undercounting:
`parallel = True` in `.coveragerc`, `COVERAGE_PROCESS_START` in the environment,
and a `.pth` file in site-packages containing
`import coverage; coverage.process_startup()`. CI installs the `.pth` itself.

The shell suite honours `$PYTHON` so a wrapper can choose the interpreter.
Hardcoding `python3` meant the traced interpreter and the invoked one differed.

**2. `pragma: no cover` does NOT reduce the measurement here.**

Coverage's default is to exclude those lines. `.coveragerc` overrides that with
`exclude_lines = (?!)`, a lookahead that can never match, which is how you say
"exclude nothing".

Honouring the pragma scored `spool-audit.py` at **81%** when the honest number
was **71%**. The exempt region was the one that mattered, so excluding it made
the metric agree with the mistake.

The pragma still means something, it just no longer buys a number. It is a
documented claim, policed two ways:

- `check.py`'s `no-cover` check fails on a branchy exemption that states no
  reason.
- `list-exemptions.py` prints every exemption with its reason, because no static
  check can judge whether a justification is any good. On the day the check was
  written it reported "0 unjustified" about a file whose inventory immediately
  showed two reasons three words long that justified nothing.

**3. The gate is per file, not repo-wide.**

`spool-audit.py` must hold **80%**. Everything else is reported and not gated.
Most root scripts have no suite at all, so a repo-wide threshold would be red on
day one for reasons unrelated to any diff, and a gate that is red for unrelated
reasons gets ignored and then removed. This mirrors what the lint step already
does: a named list that must stay clean, and the gap printed rather than hidden.

## Where it stands

`spool-audit.py`, 2026-08-13: **82%** with branch coverage, gate at 80.

Of the 97 uncovered lines, 66 are in `disable_retention` and `_restart_cups`.
Those are the two functions already marked `# pragma: no cover -- reason:
PENDING seams`, so coverage found the same gap the exemption inventory did, by a
different route and without being told. Both are on hold until the tool runs on
`joe-Inspiron-17-7778`, which is the first time either will meet a real
`cupsd.conf`, real ownership and a real daemon.

The remaining 31 are scattered error branches: `read_spool` 14, `_walk_temp` 7,
`main` 6, and single lines in four other functions.

`check.py` sits at 17%, and is not gated. Its suites cover the ANSI handling and
the `no-cover` check; the other eleven tool runners are untested, and most of
them shell out to a linter that is not installed on the runner.

## History, and why nobody noticed

The markers were applied on the grounds, correct at the time, that those
functions were thin wrappers. They stopped being thin. The comments stayed.
Nothing rechecked them, because nothing was reading them at all.

At the peak the exempt region in `spool-audit.py` was **430 lines of 1350, 32%
of the file, holding 51 branch, loop and try statements.** A thin wrapper has
none. Every serious defect on that branch came from inside that region, while
`ruff` and `mypy` together caught **0 of 112 findings** across fourteen review
rounds.

`METHODOLOGY.md` §2.6 had said the right thing the whole time: factor the IO
into a thin wrapper, tag that, and test the logic around it. §7.6 said to use
the pragma "sparingly and only on IO leaves". The doctrine was right and
unenforced, which is why §8 now exists as a checklist that verifies these claims
rather than asserting them.
