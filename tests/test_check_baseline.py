#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_baseline.py
`check.py --record-baseline` and `check.py --baseline`, against real
throwaway git repos and real ruff runs.

A gate with no baseline compares every staged file against zero, so on an
inherited repo the on-ramp is a wall: meet the bar in one move or do not
commit. A baseline records today's counts, and a staged file may not raise
its own.

The baseline records the MODE it was measured in, and a mismatch is refused
rather than compared: a whole-repo run and a per-file run of identical code
disagree by hundreds of findings, but only for the tools that resolve across
files (mypy, phpstan), so comparing across modes reports scope as
regression and accuses files nobody opened.

SKIP without git, or without ruff (nothing else here can produce a
countable finding).

    python tests/test_check_baseline.py
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from _harness import ROOT, check, check_true, load_script, run

DIRTY = "import os\nimport sys\nx = 1\n"  # unused imports: ruff findings to count
WORSE = "import os\nimport sys\nimport json\nx = 1\n"
CLEAN = "x = 1\n"


check_mod = load_script("check.py")

# Every tool these files get but mypy, each where it applies. What is under
# test here is the baseline (recording, comparing, --touched), which treats
# every tool's counts alike, and the fixtures' findings are ruff's and
# shellcheck's; mypy took most of each run's time and has suites of its own.
# The default, every tool, still runs where that is the point
# (git_test_recording, git_test_no_baseline_behaves_as_before).
NO_MYPY = "ruff,no-cover,suppress-reason,noqa,smells,shellcheck,directive-reason"


def check_py(*args: str, cwd: Path, env: dict[str, str] | None = None,
             tools: str | None = NO_MYPY) -> subprocess.CompletedProcess[str]:
    """check.py ARGS run in this process, from CWD with ENV added, as a
    finished process: in-process so mutation testing sees what it reaches.
    TOOLS goes first as --tools, so ARGS naming their own win; None runs
    check.py's default, every tool."""
    out, err = io.StringIO(), io.StringIO()
    here = os.getcwd()
    code: int = 0
    argv = ["check.py", *(["--tools", tools] if tools else []), *args]
    with mock.patch.object(sys, "argv", argv), mock.patch.dict(os.environ, env or {}), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        os.chdir(cwd)
        try:
            check_mod.main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else int(e.code is not None)
        finally:
            os.chdir(here)
    return subprocess.CompletedProcess(list(args), code, out.getvalue(), err.getvalue())


@contextlib.contextmanager
def repo() -> Iterator[Path]:
    """A git repo holding dirty.py (3 ruff findings) and clean.py."""
    with tempfile.TemporaryDirectory() as tmp:
        r = Path(tmp) / "repo"
        r.mkdir()
        subprocess.run(["git", "init", "-q", "."], cwd=str(r), check=True)
        (r / "dirty.py").write_text(DIRTY, encoding="utf-8")
        (r / "clean.py").write_text(CLEAN, encoding="utf-8")
        yield r


def _doc(path: Path) -> dict[str, object]:
    doc: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return doc


def _files(path: Path) -> list[str]:
    files = _doc(path).get("files", {})
    return sorted(files) if isinstance(files, dict) else []


def git_test_recording() -> None:
    with repo() as r:
        base = r / "base.json"
        check("record exits 0", check_py(".", "--record-baseline", str(base), cwd=r, tools=None).returncode, 0)
        doc = _doc(base)
        check("records mode=per-file", doc.get("mode"), "per-file")
        tools = doc.get("tools")
        check_true("records the tool versions it used, every tool's by default (mypy's too)",
                   isinstance(tools, dict) and {"ruff", "mypy"} <= set(tools))
        check_true("records per-tool status, so a tool that did not run stays visible",
                   isinstance(doc.get("status"), dict))
        check("keys files relative to the repo root; a clean file is absent", _files(base), ["dirty.py"])
        check("records version 2", doc.get("baseline_version"), 2)
        files = doc["files"]
        assert isinstance(files, dict)
        entry = files["dirty.py"]["ruff"]
        check("each tool's entry holds every count, fixable ones included",
              sorted(entry), ["errors", "fixable", "fixable_unsafe", "warnings"])
        check_true("  and the unused imports are counted as fixable", entry["fixable"] >= 2)


def git_test_recording_takes_tools() -> None:
    """--record-baseline with --tools records those tools, each on the files
    it serves, as a compare with the same --tools measures them."""
    with repo() as r:
        (r / "a.sh").write_text("#!/bin/sh\necho $1\n", encoding="utf-8")
        base = r / "base.json"
        check("record exits 0", check_py(".", "--record-baseline", str(base), cwd=r,
                                          tools="ruff,shellcheck").returncode, 0)
        doc = _doc(base)
        tools, files = doc.get("tools"), doc.get("files")
        assert isinstance(tools, dict) and isinstance(files, dict)
        check("only the tools named are recorded", sorted(tools), ["ruff", "shellcheck"])
        check("  each on the files it serves: ruff on the python file, shellcheck on the shell one",
              {f: sorted(files[f]) for f in files}, {"a.sh": ["shellcheck"], "dirty.py": ["ruff"]})


def git_test_a_version_1_baseline_is_refused_with_the_command() -> None:
    with repo() as r:
        base = r / "base.json"
        check_py(".", "--record-baseline", str(base), cwd=r)
        doc = _doc(base)
        doc["baseline_version"] = 1
        base.write_text(json.dumps(doc), encoding="utf-8")
        p = check_py("dirty.py", "--baseline", str(base), cwd=r)
        check("a version 1 baseline is not compared: NOT_RUN", p.returncode, 2)
        check_true("  and the refusal gives the command that re-records it",
                   f"check.py . --record-baseline {base}" in p.stderr)
        p = check_py(".", "--record-baseline", str(base), cwd=r)
        check("re-recording over it works", (p.returncode, _doc(base).get("baseline_version")), (0, 2))


def git_test_the_three_acceptance_cases() -> None:
    with repo() as r:
        base = str(r / "base.json")
        check_py(".", "--record-baseline", base, cwd=r)
        (r / "dirty.py").write_text(DIRTY + "z = 3\n", encoding="utf-8")
        check("unchanged count exits 0", check_py("dirty.py", "--baseline", base, cwd=r).returncode, 0)
        (r / "dirty.py").write_text(WORSE, encoding="utf-8")
        p = check_py("dirty.py", "--baseline", base, cwd=r)
        check("a raised count exits 1", p.returncode, 1)
        check_true("the refusal names the file and both numbers",
                   "dirty.py" in p.stderr and "3 -> 4" in p.stderr)
        (r / "dirty.py").write_text(CLEAN, encoding="utf-8")
        p = check_py("dirty.py", "--baseline", base, cwd=r)
        check("a lowered count exits 0", p.returncode, 0)
        check_true("the gain is reported, not silently accepted", "-3" in p.stdout)


def git_test_warnings_that_rise_fail() -> None:
    """A shell file's shellcheck warnings going 1 -> 3 is worse, as errors are."""
    if not shutil.which("shellcheck"):
        print("SKIP: shellcheck not found")
        return
    with repo() as r:
        one = "#!/bin/sh\necho $1\n"  # SC2086, shellcheck's "info": a warning here
        (r / "a.sh").write_text(one, encoding="utf-8")
        base = str(r / "base.json")
        check_py(".", "--record-baseline", base, cwd=r)
        check("CONTROL: recorded with one shellcheck warning and no error",
              json.loads(Path(base).read_text(encoding="utf-8"))["files"]["a.sh"]["shellcheck"]
              | {"fixable": 0, "fixable_unsafe": 0}, {"errors": 0, "warnings": 1, "fixable": 0, "fixable_unsafe": 0})
        (r / "a.sh").write_text(one + "echo $2\necho $3\n", encoding="utf-8")
        p = check_py("a.sh", "--baseline", base, cwd=r)
        check_true("warnings 1 -> 3: exit 1, the count named on stderr",
                   p.returncode == 1 and "a.sh  shellcheck  warnings  1 -> 3" in p.stderr)
        (r / "a.sh").write_text(one, encoding="utf-8")
        check("  and back at one: exit 0", check_py("a.sh", "--baseline", base, cwd=r).returncode, 0)


def git_test_touched_holds_a_file_of_warnings() -> None:
    """A touched file whose recorded findings are all warnings must lower them."""
    if not shutil.which("shellcheck"):
        print("SKIP: shellcheck not found")
        return
    with repo() as r:
        two = "#!/bin/sh\necho $1\necho $2\n"  # two SC2086 warnings, no error
        (r / "a.sh").write_text(two, encoding="utf-8")
        base = str(r / "base.json")
        check_py(".", "--record-baseline", base, cwd=r)
        check("CONTROL: recorded with two shellcheck warnings and no error",
              {k: json.loads(Path(base).read_text(encoding="utf-8"))["files"]["a.sh"]["shellcheck"][k]
               for k in ("errors", "warnings")}, {"errors": 0, "warnings": 2})
        check("touched and unchanged: NOT_LOWERED (3)",
              check_py("a.sh", "--baseline", base, "--touched", cwd=r).returncode, 3)
        (r / "a.sh").write_text("#!/bin/sh\necho \"$1\"\necho $2\n", encoding="utf-8")
        check("  one warning fixed: exit 0",
              check_py("a.sh", "--baseline", base, "--touched", cwd=r).returncode, 0)


def git_test_a_per_file_run_compares_only_what_it_measured() -> None:
    """The hook checks one staged file per run; every other baselined file must
    be counted, never compared as if it had improved to zero."""
    with repo() as r:
        (r / "other.py").write_text(DIRTY, encoding="utf-8")
        two = str(r / "two.json")
        p = check_py(".", "--record-baseline", two, cwd=r)
        check_true("CONTROL: both dirty files are recorded",
                   p.returncode == 0 and {"dirty.py", "other.py"} <= set(_files(Path(two))))
        p = check_py("dirty.py", "--baseline", two, cwd=r)
        check_true("an unmeasured baselined file is not printed as improved", "other.py" not in p.stdout)
        check_true("and the measured file's unchanged count says no change",
                   p.returncode == 0 and "baseline: no change" in p.stdout)
        check_true("and the unmeasured one is counted on one line",
                   "1 other baselined file(s) not measured" in p.stdout)
        (r / "dirty.py").write_text(CLEAN, encoding="utf-8")
        p = check_py("dirty.py", "--baseline", two, cwd=r)
        check_true("a measured file that went clean still shows its gain",
                   any(ln.strip().startswith("dirty.py  ruff") and "-> 0 (-" in ln
                       for ln in p.stdout.splitlines()))


def git_test_a_directory_against_a_baseline_is_measured_per_file() -> None:
    """A baseline holds per-file counts, so a directory compared with one is
    measured file by file, as recorded: the run CI makes over the repo."""
    with repo() as r:
        base = r / "base.json"
        check_py(".", "--record-baseline", str(base), cwd=r)
        for args in ((".",), (".", "--lang", "python")):
            p = check_py(*args, "--baseline", str(base), cwd=r)
            check(f"{' '.join(args)}: unchanged, so no change and exit 0",
                  (p.returncode, "baseline: no change" in p.stdout), (0, True))
        (r / "dirty.py").write_text(WORSE, encoding="utf-8")
        p = check_py(".", "--baseline", str(base), cwd=r)
        check_true("a file that got worse fails the directory run, named",
                   p.returncode == 1 and "dirty.py  ruff  errors  3 -> 4" in p.stderr)
        p = check_py(".", "--lang", "js", "--baseline", str(base), cwd=r)
        check("--lang keeps only that language's files: none here, so nothing worse", p.returncode, 0)
        p = check_py(".", "--tools", "ruff,smells", "--baseline", str(base), cwd=r)
        # The report, then the baseline's own lines after it.
        out, _ = json.JSONDecoder().raw_decode(p.stdout)
        check("--tools runs exactly those", sorted({c["tool"] for c in out["checks"]}), ["ruff", "smells"])
        check("  once per file, not once over the directory",
              sum(c["tool"] == "ruff" for c in out["checks"]), 2)
        check("  the output names the directory and the language asked for",
              (out["target"], out["language"]), (str(r.resolve()), "auto"))
        check("  and sums the issues it found", out["summary"]["errors"],
              sum(1 for c in out["checks"] for i in c["issues"] if i.get("severity") == "error"))


def git_test_counts_with_their_keys_missing() -> None:
    """What each piece of the baseline makes of an entry, a check or a
    document with something missing, which it reads as zero, "?", the root,
    or empty."""
    from jpcheck import baseline, common
    totals: dict[str, int] = {}
    baseline._move_totals(totals, {"errors": 2, "warnings": 1, "fixable": 3, "fixable_unsafe": 4}, {})
    check("an entry falling to one with no counts: every total falls by the old count, total by "
          "errors and warnings only", totals,
          {"errors": -2, "warnings": -1, "fixable": -3, "fixable_unsafe": -4, "total": -3})
    totals = {}
    baseline._move_totals(totals, {}, {"errors": 1, "fixable": 1})
    check("  an old entry with no counts reads as zero", totals,
          {"errors": 1, "warnings": 0, "fixable": 1, "fixable_unsafe": 0, "total": 1})
    root = Path(tempfile.gettempdir())
    got = baseline._counts_by_file([
        {"issues": [{"file": str(root / "a.py"), "severity": "warning", "fixable_unsafe": True}]},
        {"tool": "ruff"},
        {"tool": "ruff", "issues": [{"severity": "error", "fixable": True}]},
    ], root)
    check("a check with no tool is '?', one with no issues adds none, an issue with no file "
          "counts at the root; a warning and each fixable kind counted where they are", got,
          {"a.py": {"?": {"errors": 0, "warnings": 1, "fixable": 0, "fixable_unsafe": 1}},
           common._rel("", root): {"ruff": {"errors": 1, "warnings": 0, "fixable": 1, "fixable_unsafe": 0}}})
    with repo() as r:
        base = r / "base.json"
        base.write_text(json.dumps({"baseline_version": 2}), encoding="utf-8")
        p = check_py("dirty.py", "--baseline", str(base), cwd=r)
        check_true("a baseline naming no mode is refused, saying 'unknown'",
                   p.returncode == 2 and "mode='unknown'" in p.stderr)
        base.write_text(json.dumps({"baseline_version": 2, "mode": "per-file"}), encoding="utf-8")
        p = check_py("dirty.py", "--baseline", str(base), cwd=r)
        check_true("one with no files and no tools: every finding is new, so worse",
                   p.returncode == 1 and "dirty.py  ruff  errors  0 -> 3" in p.stderr)
        base.write_text("{ not json", encoding="utf-8")
        p = check_py("dirty.py", "--baseline", str(base), cwd=r)
        check_true("  and a baseline that is not JSON is named on stderr, not stdout",
                   p.returncode == 2 and p.stderr.startswith(f"baseline: cannot read {base}: ")
                   and "cannot read" not in p.stdout)
        p = check_py(".", "--record-baseline", str(base), "--force-baseline", cwd=r)
        check("forced over a file that is not a baseline: says so, not 'a baseline of 0 file(s)'",
              p.stdout.splitlines()[1:],
              ["  replaced a file that could not be read as a baseline, on --force-baseline"])
        p = check_py(".", "--lang", "python", "--baseline", str(base), "--touched", cwd=r)
        check("--touched on a directory: refused on stderr, exactly",
              (p.returncode, p.stderr.splitlines()[-1]),
              (2, "baseline: --touched takes one file, the one being changed"))
        check_true("  not on stdout", "--touched takes one file" not in p.stdout)
        from jpcheck import baseline as bl
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            checks = bl.per_file_checks(str(r / "dirty.py"))
            rc = bl.compare_baseline(checks, str(r / "dirty.py"), str(base), "per-file")
        check("compare_baseline without touched= is not --touched: an unchanged count passes", rc, 0)


def git_test_audits_in_a_per_file_run_follow_the_languages() -> None:
    """--audit with a directory and a baseline: each present language's audit
    runs once over the directory, the files' own tools per file. A stand-in
    records the audit calls, so no advisory database is asked."""
    with repo() as r:
        base = r / "base.json"
        check_py(".", "--record-baseline", str(base), cwd=r)
        (r / "app.js").write_text("const x = 1;\n", encoding="utf-8")
        calls: list[tuple[list[str], str]] = []

        def audits(names: list[str], target: str) -> list[dict[str, object]]:
            calls.append((names, target))
            return []

        with mock.patch.object(check_mod, "_run_tools", audits):
            check_py(".", "--audit", "--baseline", str(base), cwd=r)
            check("no --lang: one call, every present language's audit, over the directory",
                  calls, [(["npm-audit", "pip-audit"], str(r.resolve()))])
            calls.clear()
            check_py(".", "--lang", "python", "--audit", "--baseline", str(base), cwd=r)
            check("--lang python: python's audit only", calls, [(["pip-audit"], str(r.resolve()))])
            calls.clear()
            check_py(".", "--baseline", str(base), cwd=r)
            check("no --audit: no audit", calls, [])


def git_test_modes_and_versions_are_never_compared_across() -> None:
    with repo() as r:
        base = r / "base.json"
        check_py(".", "--record-baseline", str(base), cwd=r)
        doc = _doc(base)
        doc["mode"] = "whole-repo"
        base.write_text(json.dumps(doc), encoding="utf-8")
        p = check_py("dirty.py", "--baseline", str(base), cwd=r)
        check("a per-file run against a whole-repo baseline REFUSES", p.returncode, 2)
        check_true("and says which two modes disagree", "per-file" in p.stderr and "whole-repo" in p.stderr)
        check_py(".", "--record-baseline", str(base), "--force-baseline", cwd=r)
        doc = _doc(base)
        tools = doc["tools"]
        assert isinstance(tools, dict)
        tools["ruff"] = "0.0.1-not-a-real-version"
        base.write_text(json.dumps(doc), encoding="utf-8")
        (r / "dirty.py").write_text(WORSE, encoding="utf-8")
        p = check_py("dirty.py", "--baseline", str(base), cwd=r)
        # Genuinely worse, under a different ruff: "cannot tell", not regression and not pass.
        check("a version mismatch excludes that tool instead of failing", p.returncode, 0)
        check_true("and says so out loud", "cannot compare ruff" in p.stderr)


def git_test_recording_never_silently_discards() -> None:
    """Recording rebuilds the whole document, so a second per-file recording at
    one path would drop the first; it is refused, and the file left intact."""
    with repo() as r:
        narrow = str(r / "narrow.json")
        check("recording one file exits 0",
              check_py("dirty.py", "--record-baseline", narrow, cwd=r).returncode, 0)
        p = check_py("clean.py", "--record-baseline", narrow, cwd=r)
        check("recording a second file over it REFUSES", p.returncode, 2)
        check_true("and names the file that would be lost, and the path that works",
                   "dirty.py" in p.stderr and "--record-baseline" in p.stderr and "<dir>" in p.stderr)
        check("and leaves the existing baseline intact", _files(Path(narrow)), ["dirty.py"])
        p = check_py("clean.py", "--record-baseline", narrow, "--force-baseline", cwd=r)
        check_true("--force-baseline writes it and says what it discarded",
                   p.returncode == 0 and "discarding" in p.stdout)
        check("and the discard really happened", "dirty.py" in _files(Path(narrow)), False)
        p = check_py(".", "--record-baseline", narrow, cwd=r)
        check_true("widening over a narrower baseline exits 0 and says what it replaced",
                   p.returncode == 0 and "replaced a baseline of" in p.stdout)


def git_test_an_unreadable_baseline_is_not_an_absent_one() -> None:
    """A truncated write or conflict markers: the prior numbers cannot be
    reconstructed, so it is the worst case to overwrite quietly."""
    with repo() as r:
        broken = r / "broken.json"
        broken.write_text("{ this is not json", encoding="utf-8")
        p = check_py("dirty.py", "--record-baseline", str(broken), cwd=r)
        check_true("recording over an unparseable baseline REFUSES, saying why",
                   p.returncode == 2 and "cannot be read as a baseline" in p.stderr)
        check("and leaves it untouched", broken.read_text(encoding="utf-8"), "{ this is not json")
        p = check_py("dirty.py", "--record-baseline", str(broken), "--force-baseline", cwd=r)
        check_true("--force-baseline still overwrites it deliberately",
                   p.returncode == 0 and "dirty.py" in _files(broken))
        alien = r / "alien.json"
        alien.write_text('{"something": "else"}', encoding="utf-8")
        check("a parseable non-baseline is refused too",
              check_py("dirty.py", "--record-baseline", str(alien), cwd=r).returncode, 2)


def git_test_touched_must_lower_and_keeps_the_gain() -> None:
    """--touched: a changed file with findings must leave with fewer, and a
    lower count is written back, so the next change starts from it."""
    with repo() as r:
        base = r / "base.json"
        check_py(".", "--record-baseline", str(base), cwd=r)
        recorded = base.read_text(encoding="utf-8")
        files = _doc(base).get("files")
        assert isinstance(files, dict)
        n = files["dirty.py"]["ruff"]["errors"]
        (r / "dirty.py").write_text(DIRTY + "z = 3\n", encoding="utf-8")
        check("CONTROL: without --touched an unchanged count passes",
              check_py("dirty.py", "--baseline", str(base), cwd=r).returncode, 0)
        p = check_py("dirty.py", "--baseline", str(base), "--touched", cwd=r)
        check("with --touched an unchanged count is refused, as NOT_LOWERED (3)", p.returncode, 3)
        check_true("  naming the file and both numbers",
                   f"dirty.py was changed and still has {n} of its {n}" in p.stderr)
        check_true("  and the escape", "LINT-DEBT-OK: <reason>" in p.stderr)
        check("  and the baseline is not written", base.read_text(encoding="utf-8"), recorded)
        (r / "dirty.py").write_text(DIRTY + "import json\n", encoding="utf-8")
        check("a count that grows is still FINDINGS (1), which no message lets through",
              check_py("dirty.py", "--baseline", str(base), "--touched", cwd=r).returncode, 1)

        (r / "dirty.py").write_text("import os\nx = 1\n", encoding="utf-8")
        p = check_py("dirty.py", "--baseline", str(base), "--touched", cwd=r)
        check("one fixed passes", p.returncode, 0)
        files = _doc(base).get("files")
        assert isinstance(files, dict)
        check_true("  and the lower count is written back", 0 < files["dirty.py"]["ruff"]["errors"] < n)
        check_true("  and said", "lowered in base.json" in p.stdout)
        fresh = r / "fresh.json"
        check_py(".", "--record-baseline", str(fresh), cwd=r)
        kept, again = _doc(base)["totals"], _doc(fresh)["totals"]
        assert isinstance(kept, dict) and isinstance(again, dict)
        counts = ("errors", "warnings", "total", "fixable", "fixable_unsafe")
        check("  and totals move with it: every count as a fresh record says",
              {k: kept[k] for k in counts}, {k: again[k] for k in counts})
        check_true("  CONTROL: the fresh record's errors and fixable did fall",
                   again["errors"] < json.loads(recorded)["totals"]["errors"]
                   and again["fixable"] < json.loads(recorded)["totals"]["fixable"])
        check("the same count again is refused: each change fixes one",
              check_py("dirty.py", "--baseline", str(base), "--touched", cwd=r).returncode, 3)

        written = base.read_text(encoding="utf-8")
        check("a clean file passes", check_py("clean.py", "--baseline", str(base), "--touched",
                                              cwd=r).returncode, 0)
        check("  and writes nothing", base.read_text(encoding="utf-8"), written)
        check("--touched on a directory refuses",
              check_py(".", "--lang", "python", "--baseline", str(base), "--touched",
                       cwd=r).returncode, 2)


def git_test_touched_never_counts_cross_file_tools() -> None:
    """mypy and phpstan counts move when other files change: never required
    to drop, never written back."""
    from jpcheck import baseline
    was = {"a.py": {"mypy": {"errors": 4, "warnings": 0}, "ruff": {"errors": 0, "warnings": 0}}}
    check("a file whose only findings are mypy's is not required to drop",
          baseline._not_reduced("a.py", was, was, set()), None)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "b.json"
        doc: dict[str, object] = {"files": json.loads(json.dumps(was))}
        now = {"a.py": {"mypy": {"errors": 1, "warnings": 0}}}
        check("a mypy drop is not locked in", baseline._lock_in(str(src), doc, "a.py", now, set()), [])
        check("  and nothing is written", src.exists(), False)
    was2 = {"a.py": {"ruff": {"errors": 3, "warnings": 0}}}
    check("a tool that cannot be compared is not counted either",
          baseline._not_reduced("a.py", was2, was2, {"ruff"}), None)
    warned = {"b.sh": {"shellcheck": {"errors": 0, "warnings": 2}}}
    check("a file holding only warnings, none fixed, is not lowered",
          baseline._not_reduced("b.sh", warned, warned, set()), (2, 2))
    fixed = {"b.sh": {"shellcheck": {"errors": 0, "warnings": 1}}}
    check("  and one warning fixed lowers it",
          baseline._not_reduced("b.sh", fixed, warned, set()), None)
    rose = {"a.py": {"mypy": {"errors": 5, "warnings": 0}, "ruff": {"errors": 0, "warnings": 0}}}
    worse, _ = baseline._deltas({"a.py"}, rose, was, set())
    check("but a mypy count that RISES is worse, as any tool's is",
          worse, [("a.py", "mypy", "errors", 4, 5)])
    helptext = " ".join(check_py("--help", cwd=ROOT).stdout.split())
    check_true("--help says both halves: every tool counts against worse",
               "Every tool counts against getting worse" in helptext)
    check_true("  and mypy and phpstan need not fall",
               "mypy and phpstan need not fall" in helptext)


def git_test_no_baseline_behaves_as_before() -> None:
    with repo() as r:
        check("dirty file with no --baseline still exits 1", check_py("dirty.py", cwd=r, tools=None).returncode, 1)
        check("clean file with no --baseline still exits 0", check_py("clean.py", cwd=r, tools=None).returncode, 0)


def git_test_a_linked_worktree_under_the_hooks_git_dir() -> None:
    """A hook in a linked worktree runs with GIT_DIR set and no GIT_WORK_TREE.
    A file's key must still be its path from the repo root, or it misses its
    baseline entry and every existing finding reads as new."""
    with tempfile.TemporaryDirectory() as tmp:
        main_tree, wt = Path(tmp) / "wtrepo", Path(tmp) / "wtrepo-worktree"
        (main_tree / "sub").mkdir(parents=True)

        def git(*args: str, cwd: Path = main_tree) -> str:
            return subprocess.run(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=test",
                                   "-c", "commit.gpgsign=false", *args],
                                  cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()

        git("init", "-q", "-b", "master")
        (main_tree / "sub" / "dirty.py").write_text(DIRTY, encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "a file with findings, in a subdirectory")
        git("worktree", "add", "-q", "-b", "wt", str(wt))
        base = str(Path(tmp) / "wt-base.json")
        check_py(".", "--record-baseline", base, cwd=wt)
        check("recorded in the worktree, the key is the path from the root", _files(Path(base)),
              ["sub/dirty.py"])
        (wt / "sub" / "dirty.py").write_text(DIRTY + "z = 3\n", encoding="utf-8")
        check("CONTROL: an unchanged count, without GIT_DIR, exits 0",
              check_py("sub/dirty.py", "--baseline", base, cwd=wt).returncode, 0)
        env = dict(os.environ, GIT_DIR=git("rev-parse", "--absolute-git-dir", cwd=wt))
        check("with the hook's GIT_DIR set, the same unchanged count still exits 0",
              check_py("sub/dirty.py", "--baseline", base, cwd=wt, env=env).returncode, 0)


def _entry(errors: int = 0, warnings: int = 0, fixable: int = 0, unsafe: int = 0) -> dict[str, int]:
    return {"errors": errors, "warnings": warnings, "fixable": fixable, "fixable_unsafe": unsafe}


def git_test_a_baseline_may_only_fall() -> None:
    """raised_counts names every count a newer baseline holds above an older
    one; check.py BASELINE --raised-over OLD is the hook's and CI's use of it."""
    from jpcheck import baseline
    old = {"files": {"a.py": {"ruff": _entry(3, 1, 2)}, "v1.py": {"ruff": {"errors": 1, "warnings": 0}}}}
    same = json.loads(json.dumps(old))
    check("the same baseline raises nothing", baseline.raised_counts(old, same), [])
    lower = {"files": {"a.py": {"ruff": _entry(2, 1, 1)}}}
    check("a lower one, a file dropped included, raises nothing", baseline.raised_counts(old, lower), [])
    each = {"files": {"a.py": {"ruff": _entry(4, 2, 3, 1)}, "v1.py": {"ruff": _entry(1, 0, 5, 5)}}}
    check("each count that rose is named; one the old entry never held is not compared",
          baseline.raised_counts(old, each),
          [("a.py", "ruff", "errors", 3, 4), ("a.py", "ruff", "warnings", 1, 2),
           ("a.py", "ruff", "fixable", 2, 3), ("a.py", "ruff", "fixable_unsafe", 0, 1)])
    gap = {"files": {"a.py": {"ruff": {"warnings": 1}}}}
    check("an old entry lacking one count still compares the counts after it",
          baseline.raised_counts(gap, {"files": {"a.py": {"ruff": _entry(9, 2)}}}),
          [("a.py", "ruff", "warnings", 1, 2)])
    check("a new entry lacking a count reads it as zero, so raises nothing there",
          baseline.raised_counts({"files": {"a.py": {"ruff": _entry()}}},
                                 {"files": {"a.py": {"ruff": {"errors": 0}}}}), [])
    check("a baseline with no files at all: the old holds nothing, the new raises nothing",
          (baseline.raised_counts({}, {"files": {"a.py": {"ruff": _entry(1)}}}), baseline.raised_counts(old, {})),
          ([("a.py", "ruff", "errors", 0, 1)], []))
    added = {"files": {"a.py": {"ruff": _entry(3, 1, 2), "mypy": _entry(1)}, "new.py": {"ruff": _entry(0, 2)}}}
    check("a tool or a file the old one lacks counts from zero",
          baseline.raised_counts(old, added),
          [("a.py", "mypy", "errors", 0, 1), ("new.py", "ruff", "warnings", 0, 2)])
    with tempfile.TemporaryDirectory() as tmp:
        o, n = Path(tmp) / "old.json", Path(tmp) / "new.json"
        o.write_text(json.dumps(old), encoding="utf-8")
        n.write_text(json.dumps(lower), encoding="utf-8")
        new = str(n.resolve())
        p = check_py(str(n), "--raised-over", str(o), cwd=Path(tmp))
        check("--raised-over, nothing raised: exit 0, said on stdout",
              (p.returncode, p.stdout, p.stderr), (0, f"baseline: {new} raises no count over {o}\n", ""))
        n.write_text(json.dumps(each), encoding="utf-8")
        p = check_py(str(n), "--raised-over", str(o), cwd=Path(tmp))
        check("raised: exit 1, each count named on stderr",
              (p.returncode, p.stdout, p.stderr),
              (1, "", (f"baseline: {new} RAISES 4 count(s) over {o}:\n"
                       "  a.py  ruff  errors  3 -> 4\n  a.py  ruff  warnings  1 -> 2\n"
                       "  a.py  ruff  fixable  2 -> 3\n  a.py  ruff  fixable_unsafe  0 -> 1\n")))
        o.write_text("{ not json", encoding="utf-8")
        p = check_py(str(n), "--raised-over", str(o), cwd=Path(tmp))
        check_true("an unreadable old baseline: NOT_RUN, saying which",
                   p.returncode == 2 and p.stderr.startswith(f"baseline: cannot read {o}: ") and p.stdout == "")
        o.write_text(json.dumps(old), encoding="utf-8")
        n.write_text("{ not json", encoding="utf-8")
        p = check_py(str(n), "--raised-over", str(o), cwd=Path(tmp))
        check_true("an unreadable new one: the same, naming it",
                   p.returncode == 2 and p.stderr.startswith(f"baseline: cannot read {new}: "))


def git_test_a_lang_run_compares_only_that_language() -> None:
    """--lang measures one language's files, so another language's baselined
    file was not looked at: counted as not measured, never a gain to zero."""
    with repo() as r:
        base = r / "base.json"
        check_py(".", "--record-baseline", str(base), cwd=r)
        (r / "b.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        p = check_py(".", "--lang", "shell", "--baseline", str(base), cwd=r)
        check("a --lang shell run against a python file's baseline passes", p.returncode, 0)
        check_true("  printing no line for the python file it never measured",
                   "dirty.py" not in p.stdout)
        check_true("  and counting it as not measured",
                   "baseline: 1 other baselined file(s) not measured in this run" in p.stdout)
        p = check_py(".", "--lang", "python", "--baseline", str(base), cwd=r)
        check_true("--lang python still compares it", p.returncode == 0
                   and "baseline: no change against base.json" in p.stdout and "not measured" not in p.stdout)
        from jpcheck import baseline as bl
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            bl.compare_baseline([], str(r), str(base), "per-file")
        check_true("compare_baseline with no lang is every language: the python file is compared",
                   "dirty.py  ruff  errors  3 -> 0" in out.getvalue() and "not measured" not in out.getvalue())


def git_test_audits_are_reported_and_never_baselined() -> None:
    """An audit's count moves when an advisory is published, with no change to
    any file, so it is neither recorded nor part of a baseline's verdict."""
    from jpcheck import baseline
    audit = {"tool": "pip-audit", "status": "fail",
             "issues": [{"file": "requirements.txt", "severity": "error"}] * 2}
    ruff = {"tool": "ruff", "status": "pass", "issues": []}
    check("the audits are left out of what a baseline counts",
          baseline._not_audits([audit, ruff]), [ruff])
    with repo() as r:
        base = r / "base.json"
        (r / "requirements.txt").write_text("requests==2.0.0\n", encoding="utf-8")
        check_py(".", "--record-baseline", str(base), cwd=r)
        doc = _doc(base)
        tools, status = doc["tools"], doc["status"]
        assert isinstance(tools, dict) and isinstance(status, dict)
        check("no audit tool is recorded", sorted(baseline._AUDITS & (set(tools) | set(status))), [])
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = baseline.compare_baseline([audit, ruff], str(r / "clean.py"), str(base), "per-file")
        check("an audit's findings do not fail the verdict", rc, 0)
        check_true("  and are said to be reported and not compared",
                   "pip-audit is an audit: 2 finding(s)" in err.getvalue())


if __name__ == "__main__":
    missing = [t for t in ("git", "ruff") if shutil.which(t) is None]
    if missing:
        print(f"SKIP: {' and '.join(missing)} not found; nothing here can produce a countable finding")
        sys.exit(0)
    sys.exit(run({k.removeprefix("git_"): v for k, v in globals().items()
                  if k.startswith("git_test_")}, "baseline"))
