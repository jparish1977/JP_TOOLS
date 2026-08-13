# Coverage is not wired up, and `# pragma: no cover` has never suppressed anything

Status: **open**. Raised 2026-08-13 from JP_TOOLS PR #24.

## The finding

This repo has `# pragma: no cover` markers in its Python. Nothing reads them.

- no `.coveragerc`
- no `pyproject.toml` or `setup.cfg` with a `[coverage]` section
- no `coverage` invocation in `.github/workflows/check.yml`
- no coverage tool in `check.py`

So every pragma here has only ever been a note to a human saying *do not look
here*. It worked. On `spool-audit.py` the exempt region reached **430 lines of
1350, 32% of the file, holding 51 branch, loop and try statements**, and every
serious defect on that branch came from inside it. Over fourteen review rounds,
`ruff` and `mypy` together caught **0 of 112 findings**.

A marker that suppresses a measurement nobody takes is worse than no marker,
because it reads as though the exclusion was considered and approved.

## What is in place instead, today

`check.py` grew a `no-cover` check. It flags any function carrying
`# pragma: no cover` that contains a branch, loop or try, unless the pragma
states a reason:

```python
def _unlink_path(p): ...   # pragma: no cover
def _restart_cups(): ...   # pragma: no cover -- reason: runs a live init system
```

No branches means no reason needed: it is self-evidently a wrapper. Branches
mean it is deciding something, and decisions are testable. This is a proxy for
coverage, not a replacement. It cannot tell you whether a covered line was
actually exercised, only that a claim of "nothing to test here" is implausible
on its face.

## What wiring it properly needs

1. `coverage` in CI, run over `tests/` against the checked scripts. The suites
   are plain scripts with no pytest, so this is `coverage run --append` per
   suite, then `coverage report`.
2. A decision on what a failing threshold means. Given 17 of 22 root scripts
   have pre-existing findings, a repo-wide gate would be red on day one. The
   same approach `check.py` already takes for linting applies: a named list of
   files that must stay clean, and a printed list of the ones that are not
   covered, so the gap is visible rather than hidden.
3. Once real coverage runs, revisit every pragma. The `-- reason:` annotations
   added on 2026-08-13 are honest but two of them say `PENDING`, meaning the
   seams are not built yet rather than that the code cannot be tested:
   - `disable_retention`, 126 lines, 16 branches. Rewrites a live `cupsd.conf`
     as root and restarts the daemon.
   - `_restart_cups`, 10 lines, 4 branches. Picks an init system and runs it.

   Both are on hold pending the run on `joe-Inspiron-17-7778`, which is the
   first time either will execute against a real config, real ownership and a
   real daemon.

## Why this is not just a to-do

The pattern that produced it is general and is written up in the `review-loop`
skill: **a coverage exemption is a claim about the shape of the code, and
nothing rechecks it.** It was true when written, the function grew, and the
comment stayed. The same shape appears in `check.py`'s own manual list of
linted files, which is why that list prints what it is *not* checking rather
than staying quiet about it.
