### 2.x Size is a smell; responsibility is the rule

**PROPOSED, NOT ENFORCED.** Joe, 2026-09-14, asked to draft this and not to
push it yet. Nothing in `check.py`, the hook or CI acts on it, and no seat is
asked to change a file because of it. The section number is assigned when it
merges.

Joe's words, verbatim, because the rule is his and the two halves need each
other:

> "we really need someting int he methodology aboutkeeping source files at
> reasonable sizes... 1000 lines should be a big ass potentially embarassing
> file"

> "the complexities and responsibiites hsould dictate over a number, but the
> number is a helluva smell"

**The rule is responsibility.** A file should have one reason to change. That
is §2.2 and §4 at file scale: a file that parses argv, holds domain decisions
and performs IO has collapsed the layering into one place, whatever its
length. A 300-line file doing three jobs is the defect. A 1,400-line file doing
one well-documented job may not be.

**The number is the smell.** Past **1,000 lines** a file has to answer, in its
own header, what its responsibilities are and why they belong together. That is
§2.6 and §2.9 again: an exemption, and a lab tool, must say why, so the claim is
visible and can be disagreed with. A file that cannot state its
responsibilities in a few lines has usually answered the question already.

Crossing the line is not a violation, and it is not a reason to split on its
own. Splitting a file to get under a number, along a seam that is not a
responsibility, makes two files that must change together. That is worse than
one large file, and it hides the coupling the number was pointing at.

#### What counts, measured rather than assumed

Measured 2026-09-14 across the 38 git repos in `~/projects` on iteration8:
1,349 tracked source files, 0 unreadable. The instrument was checked against
`wc -l` on two files it had to match (1,062 and 2,873) before any number here
was used.

| threshold 1,000 | files over |
|---|---|
| physical lines | **25** |
| code lines (comments, docstrings, blanks removed) | **10** |

The counting method halves the number, because this fleet documents in the
source. Code share among the 25 runs from **36%** (`claude-config`
`tools/hook-output-leak.py`, 1,324 physical, 483 code) to **85%**
(`telnet-dungeon` `dungeon/gm/tools.py`, 1,147 physical, 982 code).

The proposal is to **smell on physical lines and decide on responsibility**. A
reader scrolls physical lines, so that is what "big" means to the person the
file costs. A file that is long because it explains itself says so in the
header answer, and that answer is the one the rule cares about. Counting only
code would reward exactly the thing that ought to be questioned: a large file
that has stopped explaining itself.

#### Complexity has no instrument yet

Joe's second sentence says complexity should decide, and **the gate measures
none**. `configs/ruff.toml` selects no complexity rules: no `C90` (mccabe
cyclomatic complexity) and none of the `PLR09xx` counts (too many branches,
statements, arguments). Those are per-function measures, which is the right
grain for complexity; file length is a proxy for it at best.

Enabling them is a separate proposal with its own cost. **What they would find
across the fleet has not been measured**, and it should be before anyone turns
them on, because selecting a rule that is red everywhere on day one is how a
gate gets switched off (§9 item 1).

#### JP_TOOLS goes first

The toolbox would fail its own smell. On `origin/master`:

| file | physical lines |
|---|---|
| `tests/test_spool_audit.py` | 1,899 |
| `spool-audit.py` | 1,462 |
| `check.py` | 1,405 |

`check.py` is the gate itself. It has at least three responsibilities that
change for different reasons: running and parsing a dozen external tools, the
baseline recorder and comparer, and the coverage-exemption inventory. That is
the case this section exists for, and it is recorded here in the way §9 item 10
asks: named, so it reads as a decision rather than a surprise.

#### Limits of the measurement

- iteration8's checkouts, not origin. Some are behind their remotes; JP_TOOLS
  there is on a feature branch where `check.py` is 1,062 lines, not master's
  1,405.
- Chosen by suffix, so extensionless scripts (`bin/` in several repos) are not
  measured. That is the same blind spot `check.py` has.
- Non-Python comment counting is approximate: a line starting with `#`, `//`,
  `/*`, `*` or `--` counts as comment.
- Tracked third-party files are included. One of the 25 is
  `winhlp-tools/oob/winhelp-format-docs/halibut-1.2-4-winhelp.c`, 2,366 lines of
  vendored research material that nobody here wrote.

#### What would move this from proposed to rule

Joe saying so. Then, in order: the header-answer convention written into §7's
greenfield checklist and §9's adoption steps; a `check.py` report (a warning
line, never an error) naming files past the smell with no responsibilities
stated; and a separate decision on the complexity rules above, after they have
been measured.
