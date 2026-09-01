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
  Where a tool misled its own author, say so; that is usually the most valuable
  line in it.

## What is here

| file | answers | header |
|---|---|---|
| `exit-convention-probe.sh` | what does a tool exit with on findings, on clean, and on refusal | all three fields |
| `spool-audit-containment-fixtures.py` | can `--purge` reach outside the directory it was told about | all three fields |
| `forged-verdict-probe.py` | can a filename or symlink target forge a line in the report | answers + history; **no explicit not-tested field** |

**That last row is stated rather than fixed.** The tool is `claude-config-advisor`'s
and its docstring is theirs to complete; editing another seat's header to satisfy
a rule about honest headers would be the wrong way round. Section 2.9 says
outright that the third field is the one nobody writes unasked, so a lab whose
own index shows one missing is the section describing itself accurately.

## Provenance

All three came out of JP_TOOLS PR #37, 2026-09-01, across three review rounds.
`spool-audit-containment-fixtures.py` is `camhelp-docs`', `forged-verdict-probe.py`
is `claude-config-advisor`'s, `exit-convention-probe.sh` is `jp-tools-advisor`'s.
They were preserved because Joe asked: *"good tools take work, i hate to see work
wasted."*
