# check.py's output: the contract

Other repositories read `check.py`'s JSON: to count what was measured, to
compare two runs, to decide whether a gate ran. This is what they may rely on.
Changing a status, a field below, or how the exit code is decided changes the
contract; see the end of this page for what that takes.

`tests/test_check_output_contract.py` holds this page to the code: the status
table against `_VERDICTS`, `_RAN` and `_NOT_RUN` in `jpcheck/common.py`, and
the exit-code rules against what `check.py` does.

## Exit codes

From `jpexit`:

| code | name | meaning |
|---|---|---|
| 0 | `OK` | every check ran (or was `skip`), and no finding is an error |
| 1 | `FINDINGS` | at least one finding with `severity` `error` that is not `exempt` |
| 2 | `NOT_RUN` | nothing was measured, or a check did not run (see the statuses) |
| 3 | `NOT_LOWERED` | `--baseline --touched` only: no worse, but the file had recorded findings and fixed none |

Findings win over not-run: a run with an error AND a check that did not run
exits 1, and the check that did not run is still named on stderr. Warnings
never change the exit code.

With `--baseline FILE` the same JSON is printed, and the exit code compares its
error counts with the baseline instead: 0 no worse, 1 worse, 2 the baseline
could not be used (unreadable, recorded in another mode, or another
`baseline_version`, which names the command that re-records it). A check whose
status is not a verdict, `skip` included, or whose tool version changed since
the baseline, is left out of the comparison and named on stderr. An audit
(`pip-audit`, `npm-audit`, `composer-audit`) is never recorded and never
compared: its findings are in the JSON and counted on stderr, and the exit code
leaves them out. A directory with `--baseline` is measured one file at a time,
as the baseline was recorded.

`check.py NEW --raised-over OLD` reads two baselines and exits 0 when NEW
raises no count over OLD, 1 when it raises one (each named on stderr; a file
or tool OLD lacks counts from zero), 2 when either cannot be read.

With `--touched` as well (one file, the one a commit changes), the file exits
3 when it had recorded findings and its count did not drop (1 still wins when
it got worse), and on exit 0 any lowered count is written back to FILE. Both
leave out `mypy` and `phpstan`, whose per-file counts move when other files
change. The pre-commit hook reads 3 apart from 1: a `LINT-DEBT-OK: <reason>`
line in the commit message lets that file through, and nothing lets a worse
one through.

## Check statuses

Every check in `checks` has a `status`, one of:

| status | class | meaning |
|---|---|---|
| `pass` | verdict | ran, nothing counted |
| `warn` | verdict | ran, every counted finding is a warning |
| `fail` | verdict | ran, at least one counted finding is an error |
| `skip` | ran | the check does not apply here (`npm-audit` with no `package.json`) |
| `error` | not run | the tool ran and stopped short of a verdict (a crash, an exit it does not document) |
| `unavailable` | not run | the tool is not installed |
| `unknown` | not run | no runner for the tool named |

"Ran" is the verdicts plus `skip`. A reader that wants to know whether a check
measured anything should test for the verdicts, and must treat every other
status, including one not in this table, as "did not run". Reading only `pass`
and `fail` as measured is the mistake to avoid: `warn` is a measurement too.

Every check carries `tool`, `status` and `issues`. `tool` is the check's own
name (`ruff`, `smells`, `directive-reason`), whether or not it ran, so a reader
can look a check up by it. In a directory run one tool can appear once per
language it serves (`smells` for Python and again for shell). A check that did
not run also carries a `note` saying why (the install command, the tool's exit
code and the line that explains it).

## The documents

`check.py FILE`, or a directory with `--lang` or `--tools`:

```json
{"target": "/abs/path", "language": "python",
 "checks": [{"tool": "ruff", "status": "pass", "issues": [], "note": "only when not run"}],
 "summary": {"total": 0, "errors": 0, "warnings": 0, "exempt": 0,
             "fixable": 0, "fixable_unsafe": 0}}
```

`check.py DIR`: the same `checks` and `summary`, plus `"mode": "multi-language"`,
`languages` (each `language`, `file_count` and `tools`), `skipped` (each
`extension` and `file_count`, present even when empty) and `ignored` (each file
`.jp-tools-ignore` left out, with its entry).

A file the run left out, exit 0. Printed for a file `.jp-tools-ignore` names,
and with `--skip-unsupported` for a file whose language is not detected:

```json
{"target": "/abs/path/README.md", "skipped": "no language detected"}
```

`skipped` is a string here, and a list in a directory run. The reason is
`no language detected`, or `ignored by .jp-tools-ignore: ENTRY (WHERE)`.

Nothing measured, exit 2: a path that does not exist, a language that cannot
be detected without `--skip-unsupported`, a directory with no source files, a
`.jp-tools-ignore` that does not parse:

```json
{"error": "Path not found: /abs/path"}
```

## An issue

Each entry in a check's `issues`:

| field | |
|---|---|
| `file`, `line`, `col` | where |
| `severity` | `error` or `warning`; only `error` counts toward exit 1 |
| `rule`, `message` | the tool's rule id and text |
| `fixable`, `fixable_unsafe` | whether `fix.py`, or `fix.py --unsafe`, would change it; read a missing one as false |
| `exempt` | only on a finding in a lab file under its METHODOLOGY 2.9 header: the reason it is listed but not counted |

`severity` is the field that decides, and every issue carries it.

`summary` counts `errors` and `warnings` from findings that are not `exempt`,
`exempt` separately, and `total` over all of them.

## Changing it

A new status, a status that changes class, a renamed field, or a change to how
the exit code is decided is a change to this contract:

1. Change this page in the same pull request; the contract test fails until it
   agrees with the code.
2. Before merging, search the repositories that read this output for the old
   values, and name in the pull request which ones were checked.
3. Say in the commit message what a reader has to change.
