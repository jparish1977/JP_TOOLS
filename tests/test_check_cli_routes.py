#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_cli_routes.py
Which way check.py's main() sends a target, with what arguments, and the
exact document and exit status of every early stop. In-process, with the
measuring functions replaced by recorders, so mutation testing sees each
argument and each key: a route that is right only in its exit code, or a
document right only in its shape, fails here.

WHAT IS CHECKED
    - A plain directory goes to check_directory(target, audit, root,
      ignores), exactly those; a directory named with --lang goes to
      check_target; neither runs the per-file baseline route.
    - _single: an ignored file stops with exit 0 and the entry that did it;
      an ignored DIRECTORY is not skipped (only files are matched); --lang
      replaces detection; --tools and --audit reach the tool list; and
      check_target gets the language, root and ignores it was given.
    - A file whose language is unknown: {"target", "skipped"} and exit 0
      with --skip-unsupported, {"error"} and exit 2 without.
    - main's stops, each document whole and each exit status as raised: a
      missing path, an unreadable ignore file, a directory with no source.
    - --smells-base reaches the smells check's base.
    - --pretty indents by 2; without it the document is one line.
    - _per_file_output with --audit runs each language's audit tools once
      over the directory, and a language with none adds nothing.

    python3 tests/test_check_cli_routes.py
"""

import contextlib
import io
import json
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest import mock

from _harness import check, load_script, run

check_mod = load_script("check.py")
IGNORES = [("vendor/*", "third party")]


def outcome(fn: Callable[[], Any]) -> tuple[object, str, str]:
    """(what FN returned, or the exit status it raised as is, or the name of
    what else it raised; stdout; stderr)."""
    out, err = io.StringIO(), io.StringIO()
    result: object
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            result = fn()
        except SystemExit as e:
            result = ("exit", e.code)
        except Exception as e:  # noqa: BLE001 -- reason: a crash is an outcome the check compares, named
            result = ("raised", type(e).__name__)
    return result, out.getvalue(), err.getvalue()


def main_with(*argv: str) -> tuple[object, str, str]:
    with mock.patch.object(sys, "argv", ["check.py", *argv]):
        return outcome(check_mod.main)


class Recorder:
    """Stands in for a measuring function: records each call, returns OUTPUT."""

    def __init__(self, output: object = None, raises: Exception | None = None) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.output = output
        self.raises = raises

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        if self.raises is not None:
            raise self.raises
        return self.output


def doc(name: str) -> dict[str, Any]:
    return {"target": name, "checks": [], "summary": {"errors": 0}}


@contextlib.contextmanager
def routes(root: Path) -> Iterator[dict[str, Recorder]]:
    """Every route main() can take replaced by a recorder; ROOT as the repo
    root and IGNORES as its ignore entries."""
    recs = {"check_directory": Recorder(doc("directory")),
            "check_target": Recorder(doc("target")),
            "_per_file_output": Recorder(doc("per-file"))}
    with mock.patch.object(check_mod, "_repo_root", lambda _t: root), \
            mock.patch.object(check_mod, "load_ignores", lambda _r: IGNORES), \
            mock.patch.object(check_mod, "check_directory", recs["check_directory"]), \
            mock.patch.object(check_mod, "check_target", recs["check_target"]), \
            mock.patch.object(check_mod, "_per_file_output", recs["_per_file_output"]):
        yield recs


@contextlib.contextmanager
def tree() -> Iterator[Path]:
    """A resolved scratch directory holding src/a.py, notes.txt and README."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (root / "notes.txt").write_text("x = 1\n", encoding="utf-8")
        (root / "README").write_text("# notes\n", encoding="utf-8")
        yield root


def test_a_plain_directory_goes_to_check_directory() -> None:
    with tree() as root, routes(root) as recs:
        src = str(root / "src")
        result, out, _ = main_with(src, "--audit")
        check("a plain directory: check_directory gets target, audit, root and ignores",
              recs["check_directory"].calls, [(src, True, root, IGNORES)])
        check("a plain directory: no other route runs",
              (recs["check_target"].calls, recs["_per_file_output"].calls), ([], []))
        check("a plain directory: its document, and exit", (out, result),
              (json.dumps(doc("directory")) + "\n", ("exit", 0)))


def test_a_directory_named_with_lang_goes_to_check_target() -> None:
    with tree() as root, routes(root) as recs:
        src = str(root / "src")
        main_with(src, "--lang", "python")
        check("a directory with --lang: check_target, not check_directory",
              ([c[:2] for c in recs["check_target"].calls], recs["check_directory"].calls),
              ([(src, "python")], []))


def test_no_source_in_a_directory() -> None:
    with tree() as root, routes(root) as recs:
        src = str(root / "src")
        recs["check_directory"].raises = check_mod.NoSourceError(src)
        result, out, _ = main_with(src)
        check("a directory with no source: the document, and exit", (out, result),
              (json.dumps({"error": f"No recognized source files in: {src}"}) + "\n",
               ("exit", 2)))


def test_a_missing_path() -> None:
    with tree() as root, routes(root):
        missing = str(root / "missing.py")
        result, out, _ = main_with(missing)
        check("a missing path: the document, and exit", (out, result),
              (json.dumps({"error": f"Path not found: {missing}"}) + "\n", ("exit", 2)))


def test_an_unreadable_ignore_file() -> None:
    def refuse(_root: Path) -> list[tuple[str, str]]:
        raise check_mod._IgnoreError("bad entry")

    with tree() as root, mock.patch.object(check_mod, "_repo_root", lambda _t: root), \
            mock.patch.object(check_mod, "load_ignores", refuse):
        result, out, _ = main_with(str(root / "src" / "a.py"))
        check("an unreadable ignore file: the document, and exit", (out, result),
              (json.dumps({"error": "bad entry"}) + "\n", ("exit", 2)))


def test_smells_base_reaches_the_smells_check() -> None:
    saved = dict(check_mod._SMELLS)
    check_mod._SMELLS["base"] = "before"
    try:
        with tree() as root, routes(root):
            main_with(str(root / "src" / "a.py"), "--smells-base", "REF")
            check("--smells-base REF: the smells base", check_mod._SMELLS.get("base"), "REF")
    finally:
        check_mod._SMELLS.clear()
        check_mod._SMELLS.update(saved)


def test_pretty_and_plain_output() -> None:
    with tree() as root, routes(root):
        a = str(root / "src" / "a.py")
        _, out, _ = main_with(a, "--pretty")
        check("--pretty: indented by 2", out, json.dumps(doc("target"), indent=2) + "\n")
        _, out, _ = main_with(a)
        check("no --pretty: one line", out, json.dumps(doc("target")) + "\n")


def single(root: Path, *argv: str) -> tuple[object, str, str]:
    args = check_mod._parser().parse_args(list(argv))
    target = str(Path(args.target).resolve())
    return outcome(lambda: check_mod._single(args, target, root, IGNORES))


def test_an_ignored_file_is_skipped() -> None:
    with tree() as root, routes(root) as recs:
        (root / "vendor").mkdir()
        lib = root / "vendor" / "lib.py"
        lib.write_text("x = 1\n", encoding="utf-8")
        result, out, _ = single(root, str(lib))
        check("an ignored file: the document naming the entry, and exit", (out, result),
              (json.dumps({"target": str(lib),
                           "skipped": "ignored by .jp-tools-ignore: vendor/* (third party)"}) + "\n",
               ("exit", 0)))
        check("an ignored file: nothing is run", recs["check_target"].calls, [])


def test_an_ignored_directory_is_not_skipped() -> None:
    with tree() as root, routes(root) as recs:
        (root / "vendor").mkdir()
        (root / "vendor" / "x").mkdir()
        vdir = str(root / "vendor" / "x")
        result, out, _ = single(root, vdir, "--lang", "python", "--tools", "ruff")
        check("a directory an entry matches: still checked, nothing printed",
              (result, out, recs["check_target"].calls),
              (doc("target"), "", [(vdir, "python", ["ruff"], root, IGNORES)]))


def test_lang_replaces_detection() -> None:
    with tree() as root, routes(root) as recs:
        notes = str(root / "notes.txt")
        result, _, _ = single(root, notes, "--lang", "python")
        check("--lang python on a .txt: check_target gets python, its tools, root and ignores",
              (result, recs["check_target"].calls),
              (doc("target"), [(notes, "python", ["ruff", "mypy", "no-cover", "suppress-reason",
                                                  "noqa", "smells"], root, IGNORES)]))


def test_tools_and_audit_reach_the_tool_list() -> None:
    with tree() as root, routes(root) as recs:
        a = str(root / "src" / "a.py")
        single(root, a, "--tools", "ruff,mypy", "--audit")
        check("--tools ruff,mypy --audit: the tools check_target gets",
              [c[2] for c in recs["check_target"].calls], [["ruff", "mypy", "pip-audit"]])


def test_an_unknown_language() -> None:
    with tree() as root, routes(root):
        readme = str(root / "README")
        result, out, _ = single(root, readme, "--skip-unsupported")
        check("no language, --skip-unsupported: the document, and exit", (out, result),
              (json.dumps({"target": readme, "skipped": "no language detected"}) + "\n",
               ("exit", 0)))
        result, out, _ = single(root, readme)
        check("no language: the document, and exit", (out, result),
              (json.dumps({"error": f"Cannot detect language for: {readme}"}) + "\n",
               ("exit", 2)))


def test_per_file_output_runs_each_languages_audit_once() -> None:
    with tree() as root:
        (root / "src" / "b.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        src = str(root / "src")
        audit = {"tool": "pip-audit", "status": "pass", "issues": []}
        run_tools = Recorder([audit])
        with mock.patch.object(check_mod, "per_file_checks", Recorder([])), \
                mock.patch.object(check_mod, "_run_tools", run_tools):
            args = check_mod._parser().parse_args([src, "--audit", "--baseline", "b.json"])
            result, _, _ = outcome(lambda: check_mod._per_file_output(args, src))
        check("--audit over python and shell: pip-audit once over the directory, shell adds none",
              (run_tools.calls, result),
              ([(["pip-audit"], src)],
               {"target": src, "language": "auto", "checks": [audit],
                "summary": check_mod._summarize([audit])}))


if __name__ == "__main__":
    sys.exit(run(globals(), "check.py routes"))
