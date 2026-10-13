#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_runs_check_target.py
jpcheck.runs.check_target on a directory named with --lang or --tools, with
fake runners in place of the real tools, so each file and each argument a
tool is handed is seen, in-process.

WHAT IS CHECKED
    - Its document: target, language, checks and summary, and nothing else.
    - A dir-capable tool is handed the directory itself, and a finding in a
      file .jp-tools-ignore names is dropped from it (the root and the
      ignore entries reach that filter).
    - A per-file tool runs once on each file of the named language, an
      ignored file left out; with no language named (--tools alone), on the
      files of each language the tool serves.
    - A name with no runner is a check that did not run, named.

    python3 tests/test_runs_check_target.py
"""

import contextlib
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest import mock

from _harness import check, run
from jpcheck import runs
from jpcheck.registry import TOOL_RUNNERS, _summarize

IGNORES = [("proj/vendor/*", "third party")]


class Fake:
    """A runner: records each path it is handed, returns ISSUES as its findings."""

    def __init__(self, tool: str, issues: list[dict[str, Any]] | None = None) -> None:
        self.tool = tool
        self.issues = issues or []
        self.seen: list[str] = []

    def __call__(self, path: str) -> dict[str, Any]:
        self.seen.append(path)
        return {"tool": self.tool, "status": "pass", "issues": [dict(i) for i in self.issues]}


@contextlib.contextmanager
def project() -> Iterator[tuple[Path, Path]]:
    """(resolved root, root/proj) with proj holding a.py, b.sh and an ignored
    vendor/ holding c.py and d.sh."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        proj = root / "proj"
        (proj / "vendor").mkdir(parents=True)
        for rel, text in (("a.py", "x = 1\n"), ("b.sh", "#!/bin/sh\necho hi\n"),
                          ("vendor/c.py", "x = 1\n"), ("vendor/d.sh", "#!/bin/sh\necho hi\n")):
            (proj / rel).write_text(text, encoding="utf-8")
        yield root, proj


def outcome(fn: Callable[[], Any]) -> object:
    """What FN returned, or the name of what it raised."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 -- reason: a crash is an outcome the check compares, named
        return ("raised", type(e).__name__)


def test_a_dir_capable_tool_gets_the_directory() -> None:
    with project() as (root, proj):
        kept = {"file": str(proj / "a.py"), "severity": "warning", "line": 1}
        gone = {"file": str(proj / "vendor" / "c.py"), "severity": "error", "line": 1}
        ruff = Fake("ruff", [kept, gone])
        with mock.patch.dict(TOOL_RUNNERS, {"ruff": ruff}):
            result = outcome(lambda: runs.check_target(str(proj), "python", ["ruff", "no-such"],
                                                       root, IGNORES))
        # warn: judged afresh from the warning left, the dropped error gone
        checks: list[dict[str, Any]] = [{"tool": "ruff", "status": "warn", "issues": [kept]},
                  {"tool": "no-such", "status": "unknown", "issues": [],
                   "note": "No runner for 'no-such'"}]
        check("a dir-capable tool: handed the directory, once", ruff.seen, [str(proj)])
        check("the document: an ignored file's finding dropped, the unknown tool named",
              result, {"target": str(proj), "language": "python", "checks": checks,
                       "summary": _summarize(checks)})


def test_a_per_file_tool_runs_on_its_languages_files() -> None:
    with project() as (root, proj):
        shellcheck = Fake("shellcheck")
        with mock.patch.dict(TOOL_RUNNERS, {"shellcheck": shellcheck}):
            result = outcome(lambda: runs.check_target(str(proj), "shell", ["shellcheck"],
                                                       root, IGNORES))
        check("--lang shell: shellcheck on b.sh alone, the ignored d.sh left out",
              (shellcheck.seen, result),
              ([str(proj / "b.sh")],
               {"target": str(proj), "language": "shell",
                "checks": [{"tool": "shellcheck", "status": "pass", "issues": []}],
                "summary": _summarize([])}))


def test_no_language_named_runs_on_every_language_the_tool_serves() -> None:
    with project() as (root, proj):
        shellcheck = Fake("shellcheck")
        with mock.patch.dict(TOOL_RUNNERS, {"shellcheck": shellcheck}):
            outcome(lambda: runs.check_target(str(proj), "unknown", ["shellcheck"],
                                              root, IGNORES))
        check("--tools shellcheck, no language: the shell files, not the python ones",
              shellcheck.seen, [str(proj / "b.sh")])


if __name__ == "__main__":
    sys.exit(run(globals(), "runs check_target"))
