#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_baseline_units.py
The pieces of jpcheck/baseline.py, each called directly with what it is
given: the document a recording writes, byte for byte; every message it
prints, whole, on the stream it goes to; which paths a comparison covers;
which tools it cannot compare; which files a per-file run measures.

A baseline is read and written as UTF-8 whatever the platform's default
codec is: each read and write here runs with a default that is not UTF-8.

No real tool runs: the runner, the repo root and the version reader are
stood in for.

    python tests/test_baseline_units.py
"""

import contextlib
import io
import json
import sys
import tempfile
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest import mock

from _harness import check, run
from jpcheck import baseline
from jpcheck.registry import DEFAULT_TOOLS, _summarize


@contextlib.contextmanager
def _default_codec(name: str) -> Iterator[None]:
    """Open text files with NAME wherever no encoding is given, as a platform
    whose default codec is not UTF-8 would."""
    real = io.text_encoding

    def pick(encoding: str | None, stacklevel: int = 2) -> str:
        return real(encoding) if encoding is not None else name

    with mock.patch("io.text_encoding", pick):
        yield


def _captured(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[Any, str, str]:
    """(what FN returned, or "raised <type>", its stdout, its stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            result = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 -- reason: a crash is an outcome the check compares, not an error here
            result = f"raised {type(e).__name__}"
    return result, out.getvalue(), err.getvalue()


def _entry(errors: int = 0, warnings: int = 0, fixable: int = 0, unsafe: int = 0) -> dict[str, int]:
    return {"errors": errors, "warnings": warnings, "fixable": fixable, "fixable_unsafe": unsafe}


def _utf8_json(path: Path, doc: object) -> None:
    """DOC as JSON in PATH, UTF-8, an odd number of bytes long, so no
    two-byte codec can read it."""
    text = json.dumps(doc, ensure_ascii=False)
    if len(text.encode("utf-8")) % 2 == 0:
        text += " "
    path.write_bytes(text.encode("utf-8"))


# ── reading ───────────────────────────────────────────────────────────────────

def test_dropped_entries() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "base.json"
        check("dropped: no destination drops nothing and had nothing",
              baseline._dropped_entries(str(dest), {"a.py": {}}), ([], 0, False))
        _utf8_json(dest, {"files": {"gone.py": {}, "a.py": {}, "é.py": {}}})
        with _default_codec("utf-16"):
            got = _captured(baseline._dropped_entries, str(dest), {"a.py": {}, "é.py": {}})
        check("dropped: read as UTF-8 whatever the default codec",
              got, ((["gone.py"], 3, False), "", ""))
        dest.write_text("{ not json", encoding="utf-8")
        check("dropped: unparseable is unreadable, holding no count",
              baseline._dropped_entries(str(dest), {}), ([], 0, True))
        dest.write_text('{"files": []}', encoding="utf-8")
        check("dropped: parsed but not a baseline is unreadable, holding no count",
              baseline._dropped_entries(str(dest), {}), ([], 0, True))


def test_read_baseline_is_utf8() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "base.json"
        doc = {"baseline_version": baseline.BASELINE_VERSION, "mode": "per-file",
               "files": {"é.py": {"ruff": _entry(1)}}}
        _utf8_json(source, doc)
        with _default_codec("utf-16"):
            got = _captured(baseline._read_baseline, str(source), "per-file")
        check("read_baseline: read as UTF-8 whatever the default codec", got, (doc, "", ""))


def test_check_raised_is_utf8() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old, new = Path(tmp) / "old.json", Path(tmp) / "new.json"
        _utf8_json(old, {"files": {"é.py": {"ruff": _entry(1)}}})
        _utf8_json(new, {"files": {"é.py": {"ruff": _entry(1)}}})
        with _default_codec("utf-16"):
            got = _captured(baseline.check_raised, str(old), str(new))
        check("check_raised: both read as UTF-8 whatever the default codec",
              got, (0, f"baseline: {new} raises no count over {old}\n", ""))


# ── what a run reports ────────────────────────────────────────────────────────

def test_statuses() -> None:
    check("statuses: a check with no tool is '?', one with no status 'unknown'",
          baseline._statuses([{"tool": "ruff", "status": "pass"}, {"status": "fail"}, {"tool": "mypy"}]),
          {"ruff": "pass", "?": "fail", "mypy": "unknown"})


_UNREADABLE = (
    "baseline: REFUSING to write d.json -- it exists but cannot be read as a baseline, "
    "so this run cannot say what would be lost.\n"
    "  That is a reason to stop, not to proceed: a truncated write, conflict markers or a "
    "half-synced file all look like this, and the numbers in it are the ones nobody can "
    "reconstruct.\n"
    "  Inspect it, or overwrite deliberately with --force-baseline.\n")


def _dropping(existing: int, measured: int, dropped: int, shown: str, dest: str = "d.json") -> str:
    return (f"baseline: REFUSING to write {dest} -- it records {existing} file(s) and this run "
            f"measured {measured}, so {dropped} would be discarded: {shown}\n"
            "  A discarded file is not baselined, so it fails the next gate for having no "
            "recorded count -- which looks like a different bug one step later.\n"
            f"  To baseline a whole tree in one go:  check.py <dir> --record-baseline {dest}\n"
            "  To replace the baseline deliberately, including on purpose after deleting "
            "files:  --force-baseline\n")


def test_refuse_write() -> None:
    check("refuse: unreadable, said whole on stderr",
          _captured(baseline._refuse_write, "d.json", {}, [], 0, True), (True, "", _UNREADABLE))
    check("refuse: three dropped are all named",
          _captured(baseline._refuse_write, "d.json", {"x.py": {}}, ["a", "b", "c"], 4, False),
          (True, "", _dropping(4, 1, 3, "a, b, c")))
    check("refuse: four dropped name three and count the rest",
          _captured(baseline._refuse_write, "d.json", {"x.py": {}, "y.py": {}}, ["a", "b", "c", "d"], 6, False),
          (True, "", _dropping(6, 2, 4, "a, b, c, +1 more")))
    check("refuse: nothing dropped, nothing said",
          _captured(baseline._refuse_write, "d.json", {}, [], 2, False), (False, "", ""))


_NOT_RUNNING = ("  Their zeros are not measurements. Re-record where they run, "
                "or a later real count will read as a regression.\n")


def test_report_recorded() -> None:
    check("recorded: a new baseline, every tool ran",
          _captured(baseline._report_recorded, "d.json", {"a": {}, "b": {}}, ["ruff"],
                    {"ruff": "pass", "mypy": "warn", "smells": "fail"}, False, 0, []),
          (None, "recorded 2 file(s) from 1 tool(s), mode=per-file, to d.json\n", ""))
    check("recorded: replacing one, discarding, with tools that did not run",
          _captured(baseline._report_recorded, "d.json", {"a": {}}, ["mypy", "ruff", "smells"],
                    {"smells": "skip", "ruff": "pass", "mypy": "unavailable"}, True, 3, ["x", "y"]),
          (None, "recorded 1 file(s) from 3 tool(s), mode=per-file, to d.json\n"
                 "  replaced a baseline of 3 file(s), discarding 2 on --force-baseline\n"
                 "  WARNING: recorded while these tools were not running: "
                 "mypy (unavailable), smells (skip)\n" + _NOT_RUNNING, ""))
    check("recorded: replacing one, discarding nothing",
          _captured(baseline._report_recorded, "d.json", {}, [], {}, True, 0, []),
          (None, ("recorded 0 file(s) from 0 tool(s), mode=per-file, to d.json\n"
                  "  replaced a baseline of 0 file(s)\n"), ""))


def test_report_comparison() -> None:
    check("comparison: worse, each line on its stream",
          _captured(baseline._report_comparison, "/x/base.json", [("mypy", "did not run (error)")],
                    ["  a.py  ruff  errors  1 -> 2 (+1)"], 2, [("a.py", "ruff", "errors", 1, 2)]),
          (None, ("  a.py  ruff  errors  1 -> 2 (+1)\n"
                  "baseline: 2 other baselined file(s) not measured in this run, so not compared\n"),
           ("baseline: cannot compare mypy -- did not run (error). Excluded from the verdict "
            "rather than counted as zero.\n"
            "\nbaseline: WORSE than recorded --\n  a.py  ruff  errors  1 -> 2\n")))
    check("comparison: no change, said",
          _captured(baseline._report_comparison, "/x/base.json", [], [], 0, []),
          (None, "baseline: no change against base.json\n", ""))


def test_warnings_count_against_worse() -> None:
    was = {"a.sh": {"shellcheck": _entry(0, 1)}}
    check("warnings 1 -> 3: worse, the count named",
          baseline._deltas({"a.sh"}, {"a.sh": {"shellcheck": _entry(0, 3)}}, was, set()),
          ([("a.sh", "shellcheck", "warnings", 1, 3)], ["  a.sh  shellcheck  warnings  1 -> 3 (+2)"]))
    check("a warning fixed does not pay for an error added",
          baseline._deltas({"a.sh"}, {"a.sh": {"shellcheck": _entry(1, 0)}}, was, set()),
          ([("a.sh", "shellcheck", "errors", 0, 1)],
           ["  a.sh  shellcheck  errors  0 -> 1 (+1)", "  a.sh  shellcheck  warnings  1 -> 0 (-1)"]))
    check("fixable counts are never compared",
          baseline._deltas({"a.sh"}, {"a.sh": {"shellcheck": _entry(0, 1, 5, 5)}}, was, set()), ([], []))
    check("an entry with no warnings count counts them from zero, as a file with no entry does",
          baseline._deltas({"a.sh"}, {"a.sh": {"shellcheck": _entry(0, 2)}},
                           {"a.sh": {"shellcheck": {"errors": 0}}}, set()),
          ([("a.sh", "shellcheck", "warnings", 0, 2)], ["  a.sh  shellcheck  warnings  0 -> 2 (+2)"]))
    check("a file or tool the baseline lacks counts both from zero",
          baseline._deltas({"b.sh"}, {"b.sh": {"shellcheck": _entry(0, 2)}}, was, set()),
          ([("b.sh", "shellcheck", "warnings", 0, 2)], ["  b.sh  shellcheck  warnings  0 -> 2 (+2)"]))
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "base.json"
        doc: dict[str, Any] = {"files": {"a.sh": {"shellcheck": _entry(2, 3)}},
                               "totals": {"errors": 2, "warnings": 3, "total": 5}}
        lowered = baseline._lock_in(str(source), doc, "a.sh", {"a.sh": {"shellcheck": _entry(2, 1)}}, set())
        check("a warnings drop alone is locked in, the line naming it",
              (lowered, doc["files"]["a.sh"]["shellcheck"], doc["totals"]["warnings"], doc["totals"]["total"]),
              (["  a.sh  shellcheck  warnings  3 -> 1"], _entry(2, 1), 1, 3))
        check("  and written", json.loads(source.read_text(encoding="utf-8"))["files"]["a.sh"]["shellcheck"],
              _entry(2, 1))


_STUCK = ("\nbaseline: a.py was changed and still has 2 of its 2 recorded finding(s). A commit "
          "that changes a file with findings fixes at least one (mypy and phpstan are not "
          "counted: other files move them). If it cannot this time, say why in the commit "
          "message: a line 'LINT-DEBT-OK: <reason>'.\n")


def test_touched_verdict() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "base.json"
        now = {"a.py": {"ruff": _entry(2)}}
        check("touched: a document with no files: nothing to lower, passes silently",
              _captured(baseline._touched_verdict, str(source), {}, "a.py", now, set()), (True, "", ""))
        check("touched:   and writes nothing", source.exists(), False)
        doc: dict[str, Any] = {"files": {"a.py": {"ruff": _entry(2)}}}
        check("touched: not lowered is refused, on stderr",
              _captured(baseline._touched_verdict, str(source), doc, "a.py", now, set()),
              (False, "", _STUCK))
        check("touched: lowered passes, each line said",
              _captured(baseline._touched_verdict, str(source), doc, "a.py",
                        {"a.py": {"ruff": _entry(1)}}, set()),
              (True, "baseline: lowered in base.json, so the gain is kept:\n  a.py  ruff  errors  2 -> 1\n", ""))


def test_uncomparable() -> None:
    now = {"mypy": "1.2", "ruff": "0.5", "smells": "", "phpcs": "3.0"}
    status = {"smells": "pass", "eslint": "unavailable", "ruff": "fail", "mypy": "pass", "phpcs": "warn"}
    old = {"mypy": "1.1", "ruff": "0.5", "smells": "9"}
    with mock.patch.object(baseline, "_tool_version", now.__getitem__):
        got = _captured(baseline._uncomparable, status, old)
    check("uncomparable: each tool that did not run, then each whose version moved",
          got, ([("eslint", "did not run (unavailable)"), ("mypy", "version 1.1 -> 1.2")], "", ""))


# ── which paths, which files ──────────────────────────────────────────────────

def test_compared_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as elsewhere:
        top = Path(tmp)
        for d in ("sub", "subX", "sp "):
            (top / d).mkdir()
        (top / "f.py").write_text("", encoding="utf-8")
        def paths(target: Path, root: Path, now: dict[str, Any], was: dict[str, Any]) -> Any:
            got, out, err = _captured(baseline._compared_paths, str(target), root, now, was)
            return got if (out, err) == ("", "") else (got, out, err)

        check("paths: the root covers every baselined path",
              paths(top, top, {"n.py": {}}, {"a.py": {}, "sub/b.py": {}}),
              ({"n.py", "a.py", "sub/b.py"}, 0))
        check("paths: a directory covers what is under it, and no sibling sharing its name's start",
              paths(top / "sub", top, {}, {"a.py": {}, "sub/b.py": {}, "subx/c.py": {}}),
              ({"sub/b.py"}, 2))
        check("paths: a directory name ending in X keeps its X",
              paths(top / "subX", top, {}, {"subX/a.py": {}, "sub/b.py": {}}),
              ({"subX/a.py"}, 1))
        check("paths: a directory name ending in a space keeps its space",
              paths(top / "sp ", top, {}, {"sp /a.py": {}, "sp/b.py": {}}),
              ({"sp /a.py"}, 1))
        outside = Path(str(top / "sub")).as_posix()
        check("paths: a directory outside the root is its own path, leading slash kept",
              paths(top / "sub", Path(elsewhere), {},
                    {outside + "/x.py": {}, outside.lstrip("/") + "/y.py": {}}),
              ({outside + "/x.py"}, 1))
        check("paths: a file covers itself and what was measured",
              paths(top / "f.py", top, {"h.py": {}}, {"f.py": {}, "g.py": {}}),
              ({"f.py", "h.py"}, 1))
        (top / "s.sh").write_text("", encoding="utf-8")
        (top / "sub" / "t.sh").write_text("", encoding="utf-8")
        def lang_paths(target: Path, now: dict[str, Any], was: dict[str, Any], lang: str) -> Any:
            got, out, err = _captured(baseline._compared_paths, str(target), top, now, was, lang)
            return got if (out, err) == ("", "") else (got, out, err)

        was: dict[str, Any] = {"f.py": {}, "s.sh": {}, "sub/t.sh": {}, "gone.sh": {}}
        check("paths: --lang keeps only that language's baselined files, the rest unmeasured",
              lang_paths(top, {}, was, "shell"), ({"s.sh", "sub/t.sh"}, 2))
        check("paths: --lang auto keeps them all",
              lang_paths(top, {}, was, "auto"), (set(was), 0))
        check("paths: a file of another language than --lang is not compared",
              lang_paths(top / "f.py", {}, {"f.py": {}}, "shell"), (set(), 1))
        check("paths: a file of that language is",
              lang_paths(top / "s.sh", {}, {"s.sh": {}}, "shell"), ({"s.sh"}, 0))


def test_per_file_checks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        top = Path(tmp)
        for name in ("a.js", "keep.py", "skip.py", "notes.txt"):
            (top / name).write_text("", encoding="utf-8")
        (top / ".jp-tools-ignore").write_text("skip.py  # stood in for a vendored file\n", encoding="utf-8")
        calls: list[tuple[list[str], str]] = []

        def tools(names: list[str], target: str) -> list[dict[str, Any]]:
            calls.append((names, target))
            return [{"tool": names[0], "file": target}]

        with mock.patch.object(baseline, "_repo_root", lambda _t: top), \
                mock.patch.object(baseline, "_run_tools", tools):
            got = _captured(baseline.per_file_checks, str(top / "skip.py"))
            check("per-file: an ignored file is not run", (got, calls), (([], "", ""), []))
            got = _captured(baseline.per_file_checks, str(top / "notes.txt"))
            check("per-file: a file no tool takes runs nothing", (got, calls), (([], "", ""), []))
            keep = str(top / "keep.py")
            got = _captured(baseline.per_file_checks, str(top), "python")
            check("per-file: a language skips the files before it and runs the ones after",
                  (got, calls),
                  (([{"tool": "ruff", "file": keep}], "", ""), [(DEFAULT_TOOLS["python"], keep)]))


# ── recording ─────────────────────────────────────────────────────────────────

def test_record_baseline() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        top = Path(tmp)
        dest = top / "base.json"
        checks: list[dict[str, Any]] = [
            {"tool": "ruff", "status": "fail",
             "issues": [{"file": str(top / "a.py"), "severity": "error", "fixable": True}]},
            {"status": "unavailable", "issues": []},
        ]
        files = {"a.py": {"ruff": _entry(1, 0, 1)}}
        expected = {
            "baseline_version": baseline.BASELINE_VERSION,
            "mode": "per-file",
            "recorded": "%Y-%m-%d",
            "root": top.name,
            "tools": {"?": "v-?", "ruff": "v-ruff"},
            "status": {"ruff": "fail", "?": "unavailable"},
            "totals": _summarize(checks),
            "files": files,
        }
        text = json.dumps(expected, indent=1, sort_keys=True) + "\n"
        with mock.patch.object(baseline, "per_file_checks", lambda _t: checks), \
                mock.patch.object(baseline, "_repo_root", lambda _t: top), \
                mock.patch.object(baseline, "_tool_version", lambda t: f"v-{t}"), \
                mock.patch.object(baseline, "time", types.SimpleNamespace(strftime=lambda fmt: fmt)), \
                _default_codec("utf-16"):
            got = _captured(baseline.record_baseline, str(top), str(dest))
            check("record: a new baseline, said on stdout",
                  got, (0, f"recorded 1 file(s) from 2 tool(s), mode=per-file, to {dest}\n"
                           "  WARNING: recorded while these tools were not running: ? (unavailable)\n"
                           + _NOT_RUNNING, ""))
            check("record: the document, byte for byte, in UTF-8",
                  dest.read_bytes() if dest.exists() else None, text.encode("utf-8"))

            prior = '{"files": {"a.py": {}, "old.py": {}}}\n'
            dest.write_bytes(prior.encode("utf-8"))
            got = _captured(baseline.record_baseline, str(top), str(dest))
            check("record: by default a discarding write is refused",
                  got, (2, "", _dropping(2, 1, 1, "old.py", str(dest))))
            check("record:   and the file is left as it was", dest.read_bytes(), prior.encode("utf-8"))
            got = _captured(baseline.record_baseline, str(top), str(dest), force=True)
            check("record: forced, it says what it replaced and discarded",
                  got, (0, f"recorded 1 file(s) from 2 tool(s), mode=per-file, to {dest}\n"
                           "  replaced a baseline of 2 file(s), discarding 1 on --force-baseline\n"
                           "  WARNING: recorded while these tools were not running: ? (unavailable)\n"
                           + _NOT_RUNNING, ""))


if __name__ == "__main__":
    sys.exit(run(globals(), "baseline units"))
