#!/usr/bin/env python3
"""Run mutmut over this checkout and hold each function to its baseline.

    mutmut-gate.py prepare                 # mutants/ ready for mutmut
    mutmut-gate.py run [--full]            # prepare, then mutmut run
    mutmut-gate.py record OUT.json [--results FILE]
    mutmut-gate.py gate BASELINE.json [--results FILE]

THE RULE (Joe, 2026-10-10, "yes for the srict rule"; no tests added 2026-10-11):
mutants are counted per function, because mutmut renumbers a function's
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

Which tests reach which function (mutants/mutmut-stats.json) is NOT kept:
every run traces it afresh. mutmut 3.8.0 re-traces only tests with new names,
so an existing test that comes to reach a new function, through main() say,
would leave that function "no tests", and the rule above would fail it for a
test it has.

mutants/ is a git worktree of HEAD with an index and NO checked-out files:
mutmut writes every file in it, and some suites ask git about the checkout, so
it has to answer there too. A normal checkout made mutmut take every file as
already written and mutate nothing. node_modules/ and vendor/ are linked in,
not copied (see pyproject.toml). A state restored from elsewhere carries a
.git link to a worktree that does not exist; `prepare` makes a new one.

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
from pathlib import Path
from typing import NoReturn

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jpexit
from platformtools.procname import name_process

LINKED = ("node_modules", "vendor")
STATS = "mutmut-stats.json"
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


def run(root: Path, full: bool, children: int) -> None:
    """Prepare, then mutmut run. Its Python goes first on PATH: the git hooks
    start `python3`, and in mutants/ every script imports mutmut."""
    if full:
        if (root / "mutants").exists():
            shutil.rmtree(root / "mutants")
        _git("-C", str(root), "worktree", "prune")
    mutants = prepare(root)
    (mutants / STATS).unlink(missing_ok=True)  # traced afresh: see THE STATE
    env = dict(os.environ, PATH=os.pathsep.join(
        [str(Path(sys.executable).parent), os.environ.get("PATH", "")]))
    p = subprocess.run([sys.executable, "-m", "mutmut", "run", "--max-children", str(children)],
                       cwd=root, env=env, check=False)
    if p.returncode != 0:
        raise GateError(f"mutmut run exited {p.returncode}")


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
