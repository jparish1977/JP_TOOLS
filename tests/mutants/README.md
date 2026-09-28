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

## State (master bfc51b5, 2026-09-28)

All four run, and every mutant is killed on target, run on the build server:
`jpt-baseline-scope` 4/4, `jpt-ignore` 11/11, `jpt-suppress-reason` 5/5,
`jpt-symlink-once` 6/6. Two needed fixing first: `jpt-suppress-reason`'s
"a bare except-pass is counted here as well as by ruff" re-anchored to the
two-line `ruffs` expression #58 made (`reanchor/jpt-suppress-reason.json`),
and `jpt-baseline-scope` gained the run line its `about` lacked.
