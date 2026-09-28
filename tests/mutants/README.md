# tests/mutants -- mutation specs for JP_TOOLS' tests

Each JSON file is a set of deliberate breakages ("mutants") of `check.py`,
each aimed at ONE named check in one test. Running a spec breaks the code one
mutant at a time and requires that exact check to read FAIL -- proof the test
notices what it claims to. pytest collects only `test_*.py`, so nothing here
runs with the suite.

Kept in another repo's `tools/lab/mutants/` until 2026-09-28, when Joe moved
them here: specs belong with the project whose tests they check.

## Running them

The runner stays in another repo (shared with another repo and
another repo):

    ~/projects/another repo/tools/lab/mutate-spec.py JPT_TREE tests/mutants/<spec>.json

It mutates JPT_TREE IN PLACE and restores it, so give it a scratch copy.
To see whether a change has stranded a spec (seconds, changes nothing):

    ~/projects/another repo/tools/lab/port-mutant-spec.py JPT_TREE tests/mutants/<spec>.json

## State when they arrived (master bfc51b5)

- `jpt-ignore` (11) and `jpt-symlink-once` (6): every mutant still anchors.
- `jpt-suppress-reason`: 4 anchor; "a bare except-pass is counted here as
  well as by ruff" no longer matches check.py.
- `jpt-baseline-scope`: its `about` has no run line, so the tools cannot tell
  its target and test (#36; its test is tests/test_check_baseline.py).
None was re-run here; the last two need porting before they can run.
