# tools/lab

Apparatus that answered a real question and would cost the next person the same
effort to rebuild. **No bar at entry.** Nothing is required to put something
here; dusty is fine, lost is not. The failure mode this exists to prevent is
work being deleted, and a bar at entry costs you tools.

See `METHODOLOGY.md` section 2.9. The contract, in short:

- **A dependency is the signal to promote.** When something comes to depend on a
  lab tool, the untestedness stops being yours and starts being inherited.
  Promotion costs a test that pins the invariants, not the happy path.
- **A header carries three things**: what question it answers, what it was tested
  against including the positive control, and **what it was NOT tested against**.
  Where a tool can mislead whoever runs it, say how; that is usually the most
  valuable line in it.
- **A recorded result never freezes the code that produced it.** Fix a lab tool
  like any other code, re-run it against what it was tested on, and say in the
  header what changed and whether the output did. The revision that produced a
  recorded number stays in git history; no "as run" copies.

## What is here

| file | answers |
|---|---|
| `exit-convention-probe.sh` | what does a tool exit with on findings, on clean, and on refusal |
| `spool-audit-containment-fixtures.py` | can `--purge` reach outside the directory it was told about |
| `forged-verdict-probe.py` | can a filename or symlink target forge a line in the report |
| `run-suites.sh` | nothing: forwards to the promoted `run-suites.sh` at the repo root |
| `i8-proof.sh` | nothing: forwards to the promoted `prove-head.sh` at the repo root |
| `test-against-ref.sh` | does a new test fail on the code before the fix |
| `ci-job-fields.py` | what did check.py report inside a CI job, per file; exits 1 on an empty log |
| `file-sizes.py` | how many source files pass a line threshold, and how much of each is prose |
| `doc-pointers.py` | do the `~/` paths in the docs still resolve, in three outcomes |
| `psscriptanalyzer-sweep.py` | how much would a PSScriptAnalyzer gate find across a tree of repos, by severity, rule and repo |
| `shellcheck-sweep.py` | how much would a shellcheck gate find across a tree of repos, by level and by repo |
| `ast-before-after.py` | which functions a cleanup restructured between two refs, drawn before and after, with complexity and check.py findings by rule |
| `prune-merged-branches.py` | which remote branches hold nothing the base does not already have, and which only LOOK unmerged because they landed rebased |
| `gate-files.py` | what check.py says about each named or tracked file, one line each, judged against a base ref as CI does |
| `ci-mutation-repro.sh` | does the PR mutation job's result come from the job's setting or the code: the job's steps in fleet-ci:current with its volumes, on the runner's daemon, the PR's kept state restored and never saved |
| `mutmut-cost.py` | where a mutmut run's time goes: functions by mutants x reaching test seconds, tests by mutant-runs charged to them |
| `coverage-like-ci.sh` | what CI's gated spool-audit coverage will be, measured as CI measures it, in a throwaway venv |
| `import-every-file.py` | which tracked Python files fail at import time after code moved between files |
| `by-path-consumers.py` | which other repos load one of this repo's scripts by path, and what they read off it |
| `directive-sweep.py` | which linter suppression directives across a set of repos give no reason, by the gate's own parser |
| `complexity-sweep.py` | what each language's standard complexity and size rules would find across a tree of repos, per rule and repo, before they are selected |
| `scrub-history.py` | what a branch's history looks like with a list of names gone from messages, identities and every file version, in a fresh clone, and whether anything named survives; `--squash` makes it one commit of the tip instead; never pushes |
| `lab-use-sweep.py` | which tracked files across ~/projects and which Claude memories name one of this repo's lab tools, per tool: the candidates for METHODOLOGY 2.9's promotion rule |
| `mutmut-by-module.py` | after a mutmut run, which modules hold the "no tests" mutants and how many, with killed, survived and pending beside |
| `scratch-guard-live.sh` | is the scratch commit guard live on this box: a real commit through the installed hook, with a clean one as the control |

Every header carries all three fields.
