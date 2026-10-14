#!/usr/bin/env python3
"""Run mutmut over this checkout and hold each function to its baseline.

    mutmut-gate.py prepare                 # mutants/ ready for mutmut
    mutmut-gate.py run [--full]            # prepare, then mutmut run
    mutmut-gate.py record OUT.json [--results FILE]
    mutmut-gate.py gate BASELINE.json [--results FILE]

THE RULE: mutants are counted per function, because mutmut renumbers a function's
mutants whenever it changes.

- A function in the baseline fails if it now has MORE survivors, or more
  mutants no test reaches ("no tests"): the untested part may shrink, never
  grow, so a PR that drops the test reaching a function does not pass by
  turning its kills into "no tests".
- A function the baseline does not have (new, or renamed) fails on ANY
  survivor and ANY "no tests": its tests reach and kill all of its mutants.
- "killed" and "timeout" (a mutant that looped was caught) count as detected.
  Any other status mutmut gives ("suspicious", "segfault", "skipped", "not
  checked", ...) is no verdict, so a function holding a different number of
  one than its baseline is NOT_RUN, naming the status.

Scripts whose suites can only run them as a process (tests/test_mutmut_config.py's
ON_SPECS) are not in [tool.mutmut] source_paths, so they are never judged here;
tests/mutants specs hold them.

THE STATE. mutmut re-tests only the functions whose code changed since its last
run, from what it keeps in mutants/. The nightly full run leaves that state;
CI restores it, so a PR re-tests only what it touched. `run --full` starts
from nothing.

Which tests reach which function (mutants/mutmut-stats.json) is kept, except
for the tests a change can move: mutmut 3.8.0 re-traces only tests with new
names, so an existing test that comes to reach a new function, through main()
say, would leave that function "no tests", and the rule above would fail it
for a test it has. So before mutmut runs, the tests in any file changed since
the state was judged, and the tests recorded as reaching a
function in any changed source file, are taken out of the stats; mutmut then
counts them as new and traces just those. A change to a file the tests share
(a conftest.py; a helper, case table or fixture under tests/) can move a
test's reach without touching its file. A data file that only suites name,
by its file name, moves only those suites' tests, so just they are traced
again; anything else shared (a conftest.py, the configuration, a Python
helper, a data file no suite names or a helper names), and a state whose
commit this clone cannot diff against, has all reach traced afresh: slower,
never wrong.

mutmut also keeps each mutant's verdict until its function's code changes,
whatever happens to the tests: a function a test newly reaches would stay
"no tests", one no test reaches any more would keep its kills, and one whose
tests were edited would keep the old tests' verdicts (a weakened test, its
kills), all until the nightly. So before mutmut runs, every function a test in
an edited test file reaches by the kept reach (a changed suite, or a suite
that names a changed fixture; a change any test can read counts every test as
edited) has its verdicts cleared, and the run tests it with the edited tests,
which it traces before it tests any mutant. After the run, every other
function whose code did not change has its verdicts cleared when its set of
reaching tests changed, and mutmut runs once more for just those, by name: a
change that only edits tests has one run.

"Since the state was judged" is since the commit the gate's last whole run
there recorded in mutants/mutmut-gate.json, so a state copied on from a run
(a PR's own, kept between its pushes) is judged again only for what changed
after it. With none, or one this clone does not have (a rebased branch), it is
since mutmut's own commit in the stats, the last full run's.

mutants/ is a git worktree of HEAD with an index and NO checked-out files:
mutmut writes every file in it, and some suites ask git about the checkout, so
it has to answer there too. A normal checkout made mutmut take every file as
already written and mutate nothing. node_modules/ and vendor/ are linked in,
not copied (see pyproject.toml). A state restored from elsewhere carries a
.git link to a worktree that does not exist; `prepare` makes a new one.

THE LOG. `run` reads mutmut's output instead of passing it on (jpcheck/
mutmut_log.py): each phase is said once, with what it works on and why (which
files, which tests, which functions' mutants), mutation testing's progress
every tenth of the way or each minute, and any other line mutmut writes (a
warning, an error, a traceback) whole. Every line of the gate's is stamped
with the UTC time and flushed, and one comes at least each minute.

--results FILE reads `mutmut results --all true` output from FILE instead of
running it, so a run's results are read once and judged twice (record, gate).

Exit codes (jpexit): 0 OK; 1 FINDINGS, a function broke the rule (whatever
else); 2 NOT_RUN, mutmut or git failed, a baseline or results could not be
read, or a mutant's status is no verdict. A run that did not happen is never a
pass, per mutant as much as per run.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Collection
from pathlib import Path
from typing import NoReturn

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jpexit
from jpcheck import mutmut_log
from platformtools.procname import name_process

LINKED = ("node_modules", "vendor")
STATS = "mutmut-stats.json"
# The gate's own record in mutants/, beside mutmut's (whose stats file takes
# no key it does not know): the commit the last whole run judged.
JUDGED = "mutmut-gate.json"
SURVIVED = "survived"
NO_TESTS = "no tests"
# Statuses that are a verdict on the mutant; anything else is not.
VERDICTS = {"killed", "timeout", SURVIVED, NO_TESTS}


class GateError(Exception):
    """Something needed could not be done or read; the message says what."""


def _git(*args: str) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise GateError(f"git {' '.join(args)}: {p.stderr.strip()[:300]}")
    return p.stdout.strip()


def _is_worktree_of(mutants: Path, root: Path) -> bool:
    """Whether MUTANTS is a working git worktree of ROOT's repository."""
    if not (mutants / ".git").is_file():
        return False
    try:
        mine = _git("-C", str(mutants), "rev-parse", "--path-format=absolute", "--git-common-dir")
        theirs = _git("-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir")
    except GateError:  # reason: a .git link git cannot follow is the restored-state case; prepare rebuilds it
        return False
    return Path(mine).resolve() == Path(theirs).resolve()


def prepare(root: Path) -> Path:
    """mutants/ as an index-only worktree at ROOT's HEAD, installs linked in."""
    mutants = root / "mutants"
    mutants.mkdir(exist_ok=True)
    if not _is_worktree_of(mutants, root):
        # git makes a worktree only at a path that does not exist, so make it
        # beside mutants/ and move its .git link in, then repair both ends.
        (mutants / ".git").unlink(missing_ok=True)
        scratch = root / f".mutants-wt-{os.getpid()}"
        _git("-C", str(root), "worktree", "add", "--no-checkout", "--detach", str(scratch), "HEAD")
        os.replace(scratch / ".git", mutants / ".git")
        shutil.rmtree(scratch)
        _git("-C", str(root), "worktree", "repair", str(mutants))
    head = _git("-C", str(root), "rev-parse", "HEAD")
    _git("-C", str(mutants), "update-ref", "--no-deref", "HEAD", head)
    _git("-C", str(mutants), "reset", "-q")
    for name in LINKED:
        link = mutants / name
        if (root / name).is_dir() and not link.exists() and not link.is_symlink():
            link.symlink_to(Path("..") / name)
    return mutants


def _module(path: str) -> str | None:
    """The module mutmut keys a source file's functions by: jpcheck/lab_use.py
    is jpcheck.lab_use, a package's __init__.py the package, mutmut-gate.py
    mutmut-gate. None for a file that is not Python."""
    if not path.endswith(".py"):
        return None
    return path.removesuffix(".py").removesuffix("/__init__").replace("/", ".")


def _shared_by_tests(path: str) -> bool:
    """A file whose change can move the reach of tests that are not in it:
    a conftest.py anywhere; at the root, each file pytest reads its
    configuration from (testpaths and python_functions decide what it
    collects at all); and under tests/ anything but a test_*.py suite and the
    mutant specs (helpers, case tables, fixtures)."""
    name = path.rpartition("/")[2]
    if name == "conftest.py" or path in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"):
        return True
    if not path.startswith("tests/") or path.startswith("tests/mutants/"):
        return False
    return not (name.startswith("test_") and name.endswith(".py"))


def _is_suite(path: str) -> bool:
    name = path.rpartition("/")[2]
    return path.startswith("tests/") and name.startswith("test_") and name.endswith(".py")


def _suites_naming(root: Path, path: str) -> set[str] | None:
    """The suites whose own text names the changed shared file PATH by its file
    name, so only their tests can have moved; None when the change can move
    any test's reach: a conftest.py or a configuration file, any Python file
    (a helper is imported, not named), or a data file that no suite names or
    that a helper names (then whoever imports the helper reads it)."""
    name = path.rpartition("/")[2]
    # A conftest.py is a Python file, so the .py test covers it.
    if path in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini") or name.endswith(".py"):
        return None
    tests = [p for p in _git("-C", str(root), "ls-files", "tests").splitlines() if p.endswith(".py")]
    # Matched as bytes: a file name is ASCII here, and a test file that is not
    # valid UTF-8 is still searched rather than refused.
    needle = name.encode()
    naming = {p for p in tests if needle in (root / p).read_bytes()}
    if not naming or not all(map(_is_suite, naming)):
        return None
    return naming


def _since(root: Path, mutants: Path, doc: dict[str, object]) -> object:
    """The commit the kept verdicts and reach were judged at: the one the
    gate's last run there recorded (JUDGED), when this clone has it; else
    mutmut's own in DOC, the stats, which only a full run moves."""
    try:
        judged = json.loads((mutants / JUDGED).read_bytes()).get("commit")
        _git("-C", str(root), "cat-file", "-e", f"{judged}^{{commit}}")
    except (FileNotFoundError, ValueError, AttributeError, GateError):  # reason: no judged commit this clone has; mutmut's own is the one to diff against
        return doc.get("git_commit")
    return judged


def forget_moved_reach(root: Path, stats: Path) -> str:
    """Take out of STATS the tests whose reach a change since it was traced
    can have moved (see THE STATE), so mutmut traces just those; say what
    was done."""
    try:
        doc = json.loads(stats.read_bytes())
    except FileNotFoundError:  # reason: no state kept is the full run's case; mutmut traces it all and the message says so
        return "no test reach kept: all of it traced"
    except ValueError:
        stats.unlink()
        return "kept test reach unreadable: all of it traced"
    since = _since(root, stats.parent, doc)
    try:
        # Against the working tree, not HEAD: mutmut mutates the files as they are.
        changed = set(_git("-C", str(root), "diff", "--name-only", str(since)).splitlines())
    except GateError:
        stats.unlink()
        return f"cannot diff against the traced commit {since}: all test reach traced"
    # A shared file that only some suites name moves only their tests; one that
    # any test can read moves them all.
    readers: set[str] = set()
    for p in sorted(p for p in changed if _shared_by_tests(p)):
        naming = _suites_naming(root, p)
        if naming is None:
            stats.unlink()
            return f"{p} changed, which tests share: all test reach traced"
        readers |= naming
    modules = {m for m in map(_module, changed) if m}
    reach: dict[str, list[str]] = doc.setdefault("tests_by_mangled_function_name", {})
    durations: dict[str, float] = doc.setdefault("duration_by_test", {})
    moved = {t for key, tests in reach.items() if key.rpartition(".")[0] in modules for t in tests}
    # A test id is FILE::NAME, or FILE::CLASS::NAME: the file is before the first ::.
    moved |= {t for t in durations if t.partition("::")[0] in changed | readers}
    for t in moved:
        durations.pop(t, None)  # a test reach names may have no duration kept
    for key, tests in reach.items():
        reach[key] = [t for t in tests if t not in moved]
    stats.write_bytes(json.dumps(doc).encode())
    named = f", the shared files' readers among them ({', '.join(sorted(readers))})" if readers else ""
    return f"{len(changed)} file(s) changed since the traced commit: {len(moved)} test(s) traced again{named}"


def edited_tests(root: Path, stats: Path) -> frozenset[str] | None:
    """The test files whose tests may now judge a mutant differently than when
    STATS was traced: each changed test file, and each suite that names a
    changed fixture. None when that is every test: no kept trace, a commit
    this clone cannot diff against, or a change to a file any test can read
    (a conftest.py, the configuration, a helper). Read BEFORE
    forget_moved_reach, which may remove STATS."""
    try:
        since = _since(root, stats.parent, json.loads(stats.read_bytes()))
        changed = set(_git("-C", str(root), "diff", "--name-only", str(since)).splitlines())
    except (FileNotFoundError, ValueError, GateError):  # reason: nothing to diff against, so every test counts as edited
        return None
    edited = {p for p in changed if _is_suite(p)}
    for p in changed:
        if _shared_by_tests(p):
            naming = _suites_naming(root, p)
            if naming is None:
                return None
            edited |= naming
    return frozenset(edited)


def _reach(stats: Path) -> dict[str, frozenset[str]] | None:
    """Which tests reach each function, as STATS records it; None when there
    is no readable record."""
    try:
        doc = json.loads(stats.read_bytes())
    except (FileNotFoundError, ValueError):  # reason: no record to compare, which run() reads as "every function may have moved"
        return None
    return {fn: frozenset(tests) for fn, tests in doc.get("tests_by_mangled_function_name", {}).items()}


def _metas(mutants: Path) -> list[tuple[Path, str]]:
    """Each .meta mutmut keeps, with the module its function names sit under."""
    found = []
    for meta in sorted(mutants.rglob("*.py.meta")):
        module = _module(meta.relative_to(mutants).as_posix().removesuffix(".meta"))
        if module:
            found.append((meta, module))
    return found


def _hashes(mutants: Path) -> dict[str, str]:
    """Each function's code hash, as mutmut kept it, by module.function."""
    return {f"{module}.{fn}": h for meta, module in _metas(mutants)
            for fn, h in json.loads(meta.read_bytes()).get("hash_by_function_name", {}).items()}


def reset_moved_verdicts(mutants: Path, before: dict[str, frozenset[str]] | None,
                         hashes_before: dict[str, str],
                         edited: frozenset[str] | None,
                         why: dict[str, str],
                         retested: Collection[str] = ()) -> tuple[int, int]:
    """Clear the verdicts of every function whose tests may now judge it
    differently, so the next run tests them again; return (verdicts,
    functions) cleared. mutmut keeps a verdict until the function's code
    changes, whatever happens to its tests: a function a test newly reaches
    would stay "no tests", one a test no longer reaches would keep its kills,
    and one whose tests were EDITED would keep the verdicts the old tests gave
    (a weakened test, its kills). So a function counts as moved when its set
    of reaching tests changed, or when any test now reaching it is in an
    EDITED file (edited_tests; None: every test). A function whose code
    changed (or that is new) was tested by that run already, and so was one
    in RETESTED (its verdicts cleared before it, clear_edited_reach). With no
    reach recorded before it, every other function counts as moved. WHY gets
    each moved function's reason."""
    after = _reach(mutants / STATS) or {}
    hashes = _hashes(mutants)
    fresh = {fn for fn, h in hashes.items() if hashes_before.get(fn) != h} | set(retested)
    reasons: dict[str, str] = {}
    if before is None or edited is None:
        reasons = dict.fromkeys((hashes.keys() | after.keys()) - fresh, "every test counts as edited")
    else:
        for fn in (before.keys() | after.keys()) - fresh:
            if before.get(fn, frozenset()) != after.get(fn, frozenset()):
                reasons[fn] = "test reach moved"
            elif any(t.partition("::")[0] in edited for t in after.get(fn, frozenset())):
                reasons[fn] = "tests edited"
    why.update(reasons)
    return _clear(mutants, reasons.keys())


def clear_edited_reach(mutants: Path, before: dict[str, frozenset[str]] | None,
                       edited: frozenset[str] | None) -> dict[str, str]:
    """Before mutmut runs: clear the verdicts of every function a test in an
    EDITED file reaches, by the kept reach BEFORE (edited_tests; None: every
    test, and so does no reach kept), so the run's own pass tests them with
    the edited tests instead of a second run after it. Each such function and
    its reason, for the plan and for reset_moved_verdicts' RETESTED."""
    if before is None or edited is None:
        cleared = dict.fromkeys(_hashes(mutants), "every test counts as edited")
    else:
        cleared = {fn: "tests edited" for fn, tests in before.items()
                   if any(t.partition("::")[0] in edited for t in tests)}
    _clear(mutants, cleared.keys())
    return cleared


def _clear(mutants: Path, functions: Collection[str]) -> tuple[int, int]:
    """Clear every verdict of FUNCTIONS in mutants/; (verdicts, functions) cleared."""
    verdicts = 0
    hit_functions: set[str] = set()
    for meta, _ in _metas(mutants):
        doc = json.loads(meta.read_bytes())
        codes = doc.get("exit_code_by_key", {})
        hit = [k for k, code in codes.items()
               if code is not None and k.partition("__mutmut_")[0] in functions]
        for k in hit:
            codes[k] = None
            hit_functions.add(k.partition("__mutmut_")[0])
        if hit:
            verdicts += len(hit)
            # As mutmut writes it: indent 4, ASCII (json's default escapes the rest).
            meta.write_bytes(json.dumps(doc, indent=4).encode())
    return verdicts, len(hit_functions)


def _mutmut_run(root: Path, children: int, plan: mutmut_log.Plan) -> None:
    """mutmut run, its Python first on PATH: the git hooks start `python3`,
    and in mutants/ every script imports mutmut. The plan's NAMES, when
    given, are the only mutants it runs, and its clean run then covers only
    the tests that reach them instead of the whole suite in one process. Its
    output goes through mutmut_log's relay (THE LOG), unbuffered, so
    mutmut's own lines reach it as they are printed."""
    env = dict(os.environ, PYTHONUNBUFFERED="1", PATH=os.pathsep.join(
        [str(Path(sys.executable).parent), os.environ.get("PATH", "")]))
    rc = mutmut_log.run_relayed(
        [sys.executable, "-m", "mutmut", "run", "--max-children", str(children), *plan.names],
        root, env, mutmut_log.Relay(plan))
    if rc != 0:
        raise GateError(f"mutmut run exited {rc}")


def _codes(mutants: Path) -> dict[str, int | None]:
    """Every mutant's exit code as mutmut keeps it now; None, no verdict."""
    return {k: code for meta, _ in _metas(mutants)
            for k, code in json.loads(meta.read_bytes()).get("exit_code_by_key", {}).items()}


# The most a named run's mutant names may take on its command line. Linux
# allows about 2 MiB for arguments and environment together; past this the
# run goes unnamed (every pending mutant, the whole suite as its clean run),
# which tests the same mutants, more slowly.
NAMES_MAX_BYTES = 256 * 1024


def _cleared(mutants: Path) -> list[str]:
    """Every mutant with no verdict, as mutmut keys it: after a run, exactly
    the ones reset_moved_verdicts just cleared."""
    return sorted(k for k, code in _codes(mutants).items() if code is None)


def _named(names: list[str]) -> list[str]:
    """NAMES for a named run, or none (an unnamed run) when they would not
    fit on one command line."""
    return names if sum(len(n) + 1 for n in names) <= NAMES_MAX_BYTES else []


def run(root: Path, full: bool, children: int) -> None:
    """Prepare, clear the verdicts edited tests reach (clear_edited_reach),
    then mutmut run; then, if any function's test reach moved, clear its
    verdicts and run again (reset_moved_verdicts)."""
    if full:
        if (root / "mutants").exists():
            shutil.rmtree(root / "mutants")
        _git("-C", str(root), "worktree", "prune")
    mutants = prepare(root)
    before = _reach(mutants / STATS)
    hashes_before = _hashes(mutants)
    edited = edited_tests(root, mutants / STATS)
    sources = _sources(root, mutants)
    retested = clear_edited_reach(mutants, before, edited)
    mutmut_log.say(f"tests edited since the state was judged: {len(retested)} function(s)' verdicts cleared,"
                   " tested again in this run")
    traced = forget_moved_reach(root, mutants / STATS)
    mutmut_log.say(traced)

    def codes() -> dict[str, int | None]:
        return _codes(mutants)

    _mutmut_run(root, children, mutmut_log.Plan(codes, [], traced, sources,
                                                 lambda: {**retested, **mutmut_log.code_reasons(hashes_before,
                                                                                                 _hashes(mutants))}))
    why: dict[str, str] = {}
    verdicts, functions = reset_moved_verdicts(mutants, before, hashes_before, frozenset(), why, retested.keys())
    if verdicts:
        names = _named(_cleared(mutants))
        how = f"by name, {len(names)} mutant(s)" if names else "unnamed: too many names for one command line"
        mutmut_log.say(f"test reach moved for {functions} function(s): "
                       f"their {verdicts} verdict(s) tested again ({how})")
        _mutmut_run(root, children, mutmut_log.Plan(codes, names, "the first run traced it",
                                                    "none changed since the first run", lambda: why))
    # Every verdict and every test's reach now stand for HEAD: the next run
    # here, or from a copy of this state, judges only what changed since.
    (mutants / JUDGED).write_bytes(json.dumps({"commit": _git("-C", str(root), "rev-parse", "HEAD")}).encode())


def _sources(root: Path, mutants: Path) -> str:
    """What mutmut will rebuild mutants for: the source files it keeps, and
    those changed since the state's commit, named. Read before
    forget_moved_reach, which may remove the trace that holds the commit."""
    changed: list[str] | None
    try:
        since = _since(root, mutants, json.loads((mutants / STATS).read_bytes()))
        changed = sorted(p for p in _git("-C", str(root), "diff", "--name-only", str(since)).splitlines()
                         if _module(p) and not p.startswith("tests/"))
    except (FileNotFoundError, ValueError, GateError):  # reason: with no commit to diff against, mutmut decides by file times alone
        changed = None
    return mutmut_log.sources_line(len(_metas(mutants)), changed)


def results_text(root: Path, source: str | None) -> str:
    if source:
        try:
            return Path(source).read_text(encoding="utf-8")
        except OSError as e:
            raise GateError(f"cannot read results {source}: {e}") from e
    p = subprocess.run([sys.executable, "-m", "mutmut", "results", "--all", "true"],
                       cwd=root, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise GateError(f"mutmut results exited {p.returncode}: {p.stderr.strip()[:300]}")
    return p.stdout


def by_function(text: str) -> dict[str, dict[str, int]]:
    """{function: {status: count}} from `mutmut results --all true` lines,
    `    module.x_func__mutmut_N: status`. A function's mutants share the name
    before `__mutmut_`."""
    out: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        name, sep, status = line.strip().rpartition(": ")
        if not sep or "__mutmut_" not in name:
            continue
        function = name.rsplit("__mutmut_", 1)[0]
        counts = out.setdefault(function, {})
        counts[status] = counts.get(status, 0) + 1
    if not out:
        raise GateError("no mutant results found; did mutmut run?")
    return out


def violations(baseline: dict[str, dict[str, int]],
               current: dict[str, dict[str, int]]) -> list[str]:
    """Each function breaking the rule: more survivors or more "no tests"
    than the baseline, or any of either in a function the baseline lacks."""
    out = []
    for function, counts in sorted(current.items()):
        now = Counter(counts)  # a status a function lacks reads as 0
        if function not in baseline:
            if now[SURVIVED]:
                out.append(f"{function}: NEW, {now[SURVIVED]} mutant(s) survived; "
                           "a new function kills all of its")
            if now[NO_TESTS]:
                out.append(f"{function}: NEW, {now[NO_TESTS]} mutant(s) no test reaches; "
                           "a new function's tests reach all of its")
            continue
        was = Counter(baseline[function])
        for status in (SURVIVED, NO_TESTS):
            if now[status] > was[status]:
                out.append(f"{function}: {status} {was[status]} -> {now[status]}")
    return out


def no_verdicts(baseline: dict[str, dict[str, int]],
                current: dict[str, dict[str, int]]) -> list[str]:
    """Each function holding a different number of mutants with a status that
    is no verdict ("suspicious", "not checked", ...) than its baseline does.
    The same number is the baseline's own, carried in the kept state."""
    out = []
    for function, counts in sorted(current.items()):
        was = Counter(baseline.get(function))  # Counter(None) is empty
        for status, n in sorted(counts.items()):
            if status not in VERDICTS and n != was[status]:
                out.append(f"{function}: {n} mutant(s) {status!r}, which is no verdict "
                           f"(the baseline has {was[status]})")
    return out


def _load(path: str) -> dict[str, dict[str, int]]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise GateError(f"cannot read baseline {path}: {e}") from e
    if not isinstance(doc, dict):
        raise GateError(f"baseline {path} is not a JSON object")
    return doc


def _gate(baseline: dict[str, dict[str, int]], current: dict[str, dict[str, int]]) -> int:
    """Print each finding and each mutant with no verdict; a finding wins."""
    found, unjudged = violations(baseline, current), no_verdicts(baseline, current)
    for line in found:
        print(f"FAIL {line}")
    for line in unjudged:
        print(f"NOT RUN {line}")
    if found:
        print(f"mutmut-gate: {len(found)} rule(s) broken")
        return jpexit.FINDINGS
    if unjudged:
        print(f"mutmut-gate: could not judge {len(unjudged)} function(s)")
        return jpexit.NOT_RUN
    print("mutmut-gate: PASS")
    return jpexit.OK


class _Parser(argparse.ArgumentParser):
    """argparse, but a usage error is jpexit.NOT_RUN rather than argparse's own 2."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"mutmut-gate: {message}", file=sys.stderr)
        raise SystemExit(jpexit.NOT_RUN)


def _parser() -> _Parser:
    p = _Parser(description="Run mutmut and hold each function to its baseline.")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    sub.add_parser("prepare", help="make mutants/ ready for mutmut")
    r = sub.add_parser("run", help="prepare, then mutmut run")
    r.add_argument("--full", action="store_true", help="start from nothing, not the kept state")
    r.add_argument("--max-children", type=int, default=os.cpu_count() or 1)
    for name, what in (("record", "write per-function counts to OUT"),
                       ("gate", "fail on a function that breaks the rule")):
        c = sub.add_parser(name, help=what)
        c.add_argument("file", metavar="OUT" if name == "record" else "BASELINE")
        c.add_argument("--results", metavar="FILE", help="read mutmut results from FILE")
    return p


def main(argv: list[str] | None = None) -> int:
    name_process("mutmut-gate")
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    root = Path.cwd()
    try:
        if args.cmd == "prepare":
            print(f"mutmut-gate: {prepare(root)} ready")
        elif args.cmd == "run":
            run(root, args.full, args.max_children)
        elif args.cmd == "record":
            counts = by_function(results_text(root, args.results))
            Path(args.file).write_text(json.dumps(counts, indent=1, sort_keys=True) + "\n",
                                       encoding="utf-8")
            print(f"mutmut-gate: {len(counts)} function(s) recorded to {args.file}")
        else:
            return _gate(_load(args.file), by_function(results_text(root, args.results)))
    except GateError as e:
        print(f"mutmut-gate: {e}", file=sys.stderr)
        return jpexit.NOT_RUN
    return jpexit.OK


if __name__ == "__main__":
    sys.exit(main())
