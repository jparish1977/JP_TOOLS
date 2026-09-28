# tests/mutants: mutation specs for JP_TOOLS' tests

Each JSON file is a set of deliberate breakages ("mutants") of one source
file, each aimed at ONE named check in one test. Running a spec breaks the code
one mutant at a time and requires that exact check to read FAIL: proof the test
notices what it claims to. pytest collects only `test_*.py`, so nothing here
runs with the suite.

A spec's `about` ends with its run line, which names the file it mutates and
the test that must catch each mutant:

    mutate-on-target.py TREE <target> <test> <spec>

`TREE` is a checkout of this repository. The runner mutates it IN PLACE and
restores it, so give it a scratch copy. A mutant counts only when the test
fails on the check it names; one killed by some other check, or by a crash,
has shown nothing.

The runner, `mutate-on-target.py`, is not part of this repository.

When code moves or changes, a spec's `edits` stop matching and the runner
refuses to start. Re-anchor the mutant to the new code with the SAME mutation;
never drop one because it is inconvenient.
