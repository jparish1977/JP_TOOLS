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

## Where it stands

`spool-audit.py` with `spoolaudit/`: **97%** with branch coverage (569
statements, 15 uncovered, all error branches in `spoolaudit/read.py` and
`spoolaudit/purge.py`). One exemption remains: `_list_names`, a one-line,
branch-free listing wrapper.
