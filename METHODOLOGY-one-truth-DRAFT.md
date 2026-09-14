### 2.x One fact, one home

**PROPOSED, NOT ENFORCED.** Joe, 2026-09-14, asked for a draft of this
principle. Nothing in the gate acts on it and nobody is asked to change
anything because of it. The section number is assigned when it merges.

Joe's words, verbatim:

> "were finding multiple sources of truth far too often"

**The rule.** Every fact has exactly one home: a number, a list, a contract, a
decision, a version pin. Everything else points to that home, or is generated
from it. A copy is allowed only when something checks it against the home and
fails when they disagree.

**Why a copy is worse than it looks.** A second copy is correct the day it is
written and wrong after the first change to either one, and nothing reports the
drift. From then on a reader holding one copy cannot tell whether it is the
current one. The reader who finds out is usually the one who acted on the stale
copy.

#### Five instances in this repo, found in one day

All measured or read on `origin/master`, 2026-09-14.

1. **One name, defined twice.** `check.py` assigns `_SKIP_DIRS` at line 582 and
   again at line 844, with different contents. The second wins at runtime, and
   it lacks `.venv`, `venv`, `dist` and `build`. Measured: pointed at a tree
   holding `.venv/lib/x.js`, `dist/bundle.js` and `node_modules/dep.js`,
   `_collect_files` collected the first two and skipped only `node_modules`.
   README lists `.venv`, `dist` and `build` as skipped. ruff, mypy and
   `no-cover` all pass `check.py` with **0 issues**, so no gate sees it.
   Issue #5, open since 2026-04-07, proposes deriving tool excludes "from
   `_SKIP_DIRS`" without saying which one, so as written that fix would inherit
   the defect.

2. **Exit code 2 means three things.** `check.py`'s docstring says "usage /
   tool-not-found error". README says "usage/path error", and separately that
   missing tools are reported "unavailable" while "the rest still run". The code
   does neither: a missing tool exits **0** (#29, measured). Three sources give
   three answers, and the code is the only one that runs.

3. **The coverage rule, stated five ways.** §2.5 says 95%+, §2.6 says 100% for
   business logic, §7 item 6 leaves it to the adopter's config, §8 item 3 gives
   the method, and `docs/coverage.md` gates one file at 90. See #55.

4. **Tool pins in three homes.** `requirements-dev.txt` pins ruff and mypy.
   `.github/workflows/check.yml` pins ruff, mypy and coverage.
   `templates/ci-check.yml` pins ruff, mypy and pip-audit. §7 already records
   two of these drifting. `requirements-dev.txt` was written as the fix, and
   both workflows still carry their own `pip install` lines.

5. **A comment that restated a fact.** `check.py` near line 705 says "nothing in
   this repo measures coverage at all". That has been false since 2026-08-13,
   when coverage was wired into CI, and on 2026-09-14 a seat reading the code
   quoted it to Joe as the current state. A comment that says **why** cannot go
   stale that way. A comment that says **what the repo does** is a second copy
   of a fact whose home is somewhere else.

#### This document already holds the pieces

- §9 item 8: a component leaves by moving. `ntfs-inventory.py` in two repos,
  119 lines apart, each holding a fix the other lacks.
- §7, "Declare the instrument's own dependencies": pins in two workflows that
  drifted.
- `check.py`'s own `coverage_exemptions` docstring: "there is ONE
  implementation of 'what counts as an exemption' rather than two that must
  agree, which is this repo's most repeated defect."

Three statements of one idea in three places: the principle's own failure,
inside the document that should state it. This section is meant to be their
home, and the three should point here.

#### How to apply it

- **Point, don't restate.** A doc that describes behaviour links to the code, or
  to a test that asserts the behaviour. README's exit-code line becomes a test
  that runs `check.py` and checks the codes. Then a wrong README fails a build
  instead of misleading a reader.
- **A copy must be generated or checked.** If a CI line or a README table has to
  exist, generate it from the home, or add a check that fails when they
  disagree. §7's pin check is the model: the manifest is the home, and a
  fifteen-line check proves that what is installed matches what is pinned.
- **One name, one definition.** A module constant assigned twice is two sources
  of truth in one file, and the later one wins silently.
- **Comment the why.** Restating the what is a copy.
- **A pointer is not a copy.** From the fleet's coordination board, 2026-09-07:
  a claim note that listed ten open decisions "was enumerated here and drifted
  within the hour (said NINE while the file held ten)". The fix was to list tags
  only and point at the file. A note that points cannot drift; a note that
  enumerates can.
- **Splitting along a false seam makes two homes.** This is the size draft's
  warning seen from the other side: two files that must change together are two
  sources of truth about one responsibility.

#### No instrument yet

Nothing in the gate detects duplication. Instance 1 passed ruff, mypy and
`no-cover` at 0 issues. None of the rules selected in `configs/ruff.toml`
caught a module constant assigned twice. **Whether any available ruff or mypy
rule would catch it has not been measured.** Doc-against-code tests exist
nowhere in this repo yet.

#### What would move this from proposed to rule

Joe saying so. Then: each of the five instances fixed to a single home, each as
its own change; a test pinning README's claims about exit codes and skipped
directories to what `check.py` actually does; and a separate, measured decision
on whether any linter rule can serve as an instrument.

Instance 1 is a live bug, and at the time of writing it has **no issue**. #5 is
a neighbour, not a duplicate: it says `_collect_files` "correctly skips
vendor/, node_modules/, etc.", which is true for those two.
