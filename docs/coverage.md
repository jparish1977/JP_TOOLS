# Coverage

## How it runs

CI runs the suites **under** coverage, on the same pass that gates them, so
nothing runs twice. Config is `.coveragerc`; coverage is pinned in
`requirements-dev.txt`.

```
python -m coverage run tests/test_<name>.py      # each .py suite
PYTHON=python bash tests/test_<name>.sh          # each .sh suite
python -m coverage combine
python -m coverage report --include='spool-audit.py,spoolaudit/*' --fail-under=90
```

Locally, install `requirements-dev.txt` in a venv to get the numbers.

## Three decisions worth knowing about

**1. Subprocesses are traced.**

Several suites drive a tool as a subprocess (the spool-audit invariant and
acceptance suites among them), which coverage does not follow by default.
Untraced, code those suites genuinely exercise reads as uncovered, and the
obvious remedy for an apparent gap is a pragma: a misconfigured measurement
argues for making the code less visible.

Tracing needs three things together, and any one missing undercounts
silently: `parallel = True` in `.coveragerc`, `COVERAGE_PROCESS_START` in the
environment, and a `.pth` file in site-packages containing
`import coverage; coverage.process_startup()`. CI installs the `.pth` itself.
The shell suites honour `$PYTHON`, so the interpreter they invoke is the one
that is traced.

**2. `pragma: no cover` does NOT reduce the measurement.**

Coverage's default is to exclude those lines. `.coveragerc` overrides that with
`exclude_lines = (?!)`, a lookahead that can never match, which is how you say
"exclude nothing". An exempt region is exactly where untested decisions hide,
so excluding it makes the number agree with the mistake.

The pragma still means something; it just buys no number. It is a documented
claim, policed two ways:

- `check.py`'s `no-cover` check fails on an exemption over code with branches
  that states no reason (METHODOLOGY 2.6: only a branch-free IO leaf may go
  without one).
- `list-exemptions.py` prints every exemption with its reason, because no
  static check can judge whether a justification is any good.

**3. The gate is where a threshold has been earned.**

`spool-audit.py` and `spoolaudit/` must hold **90%**. Everything else is
reported and not gated: a threshold over code with no suite would be red for
reasons unrelated to any diff, and a gate that is red for unrelated reasons
gets ignored and then removed.

## PHP

PHPUnit measures coverage but has no "fail under N%" of its own, so a PHP
project writes a Clover report and `coverage-gate.py` gates it:

```
vendor/bin/phpunit --coverage-clover clover.xml
python3 JP_TOOLS/coverage-gate.py clover.xml --fail-under 95
```

It reads the PROJECT's line coverage (Clover's project-level
`coveredstatements` over `statements`) and exits with `jpexit`'s codes: 0 at
or over the threshold, 1 under it, 2 when nothing was measured (no report, not
Clover, or zero statements). An unreadable or empty report is never a pass.

**A coverage driver is needed**, or PHPUnit writes no figures: `pcov`
(preferred, it only measures) or `xdebug` with `XDEBUG_MODE=coverage` for the
run, since xdebug is off by default where it is installed.

**Why not an established Composer tool** (METHODOLOGY 2.15). Measured
2026-10-05 against reports with a known answer: `rregeer/phpunit-coverage-check`
(the most used, about 7M downloads, last released 2019) and its maintained
fork `esi/phpunit-coverage-check` both pass 96% and fail 90% correctly, but
both exit 1 for a missing report and for one with zero statements, the same
code as "under the threshold". A gate that cannot tell "could not look" from
"looked and found too little" fails the rule this repo's gates are built on,
so the check is our own, and small.

## Where it stands

`spool-audit.py` with `spoolaudit/`: **97%** with branch coverage (569
statements, 15 uncovered, all error branches in `spoolaudit/read.py` and
`spoolaudit/purge.py`). One exemption remains: `_list_names`, a one-line,
branch-free listing wrapper.
