#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_js_tools_eslint.py
jpcheck.js_tools' eslint path, in-process, with node and the runner faked:

- _eslint_issues: every field read from eslint's JSON, and the default for
  each one missing; empty output is no issues.
- _eslint_source_files: a file is itself; a directory gives its .js, .mjs and
  .html files outside node_modules and vendor.
- _suppressions_in: each eslint-disable comment, its rule, line and reason,
  read as UTF-8 with undecodable bytes replaced, whatever the locale's codec.
- _run_node_runner: which node it runs, with exactly what arguments and
  options, and the report when node, the runner, the package or the lint is
  missing or fails.
- run_eslint: the report it builds from the runner's output.

    python tests/test_js_tools_eslint.py
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from _harness import check, run
from jpcheck import js_tools


def outcome(fn: Callable[..., Any], *args: Any) -> Any:
    """FN's result, or the name of what it raised: a crash is a wrong answer."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 -- reason: the exception's type is compared as the result
        return f"raised {type(e).__name__}"


# ---------------------------------------------------------------- _eslint_issues

ESLINT_JSON = json.dumps([
    {"filePath": "/src/a.js", "messages": [
        {"line": 3, "column": 7, "severity": 2, "ruleId": "no-undef",
         "message": "'x' is not defined.", "fix": {"range": [0, 1], "text": ""}},
        {"line": 9, "column": 2, "severity": 1, "ruleId": "no-console",
         "message": "Unexpected console statement."},
        {"line": 11, "column": 4, "severity": 3, "ruleId": "odd",
         "message": "an unknown severity"},
    ]},
    {"filePath": "/src/b.js"},
    {"messages": [{}]},
])


def test_eslint_issues_every_field_and_default() -> None:
    check("eslint issues: every field and every default", outcome(js_tools._eslint_issues, ESLINT_JSON), [
        {"file": "/src/a.js", "line": 3, "col": 7, "severity": "error", "rule": "no-undef",
         "message": "'x' is not defined.", "fixable": True},
        {"file": "/src/a.js", "line": 9, "col": 2, "severity": "warning", "rule": "no-console",
         "message": "Unexpected console statement.", "fixable": False},
        {"file": "/src/a.js", "line": 11, "col": 4, "severity": "warning", "rule": "odd",
         "message": "an unknown severity", "fixable": False},
        {"file": "", "line": 0, "col": 0, "severity": "warning", "rule": "",
         "message": "", "fixable": False},
    ])


def test_eslint_issues_empty_output() -> None:
    check("eslint issues: empty output is none", outcome(js_tools._eslint_issues, ""), [])


# ---------------------------------------------------------- _eslint_source_files

def test_eslint_source_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "proj"
        for rel in ("a.js", "b.mjs", "c.html", "d.txt", "e.css", "sub/f.js",
                    "node_modules/pkg/g.js", "vendor/h.js", "sub/vendor/i.mjs"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x\n", encoding="utf-8")
        got = outcome(js_tools._eslint_source_files, str(root))
        check("source files: a directory's own js, mjs and html",
              sorted(got) if isinstance(got, list) else got,
              sorted([root / "a.js", root / "b.mjs", root / "c.html", root / "sub" / "f.js"]))
        check("source files: a file is itself",
              js_tools._eslint_source_files(str(root / "d.txt")), [root / "d.txt"])


# --------------------------------------------------------------- _suppressions_in

SUPPRESSED = (
    b"const a = 1;\n"
    b"// eslint-disable-next-line no-console -- logs on purpose\n"
    b"/* eslint-disable no-alert -- needs X*/\n"
    b"foo(); // eslint-disable-line no-eval\n"
    b"// eslint-disable-line no-new -- keep -- both halves\n"
    b'x = "\xff"; // eslint-disable-line no-var -- bad byte\n'
)


def suppression(fp: Path, line: int, rule: str, message: str) -> dict[str, Any]:
    return {"file": str(fp), "line": line, "col": 0, "severity": "warning",
            "rule": f"suppressed:{rule}", "message": message, "fixable": False}


def test_suppressions_every_form() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fp = Path(tmp) / "s.js"
        fp.write_bytes(SUPPRESSED)
        check("suppressions: every form", outcome(js_tools._suppressions_in, fp), [
            suppression(fp, 2, "no-console", "Acknowledged suppression: no-console (logs on purpose)"),
            suppression(fp, 3, "no-alert", "Acknowledged suppression: no-alert (needs X)"),
            suppression(fp, 4, "no-eval", "Acknowledged suppression: no-eval"),
            suppression(fp, 5, "no-new", "Acknowledged suppression: no-new (keep -- both halves)"),
            suppression(fp, 6, "no-var", "Acknowledged suppression: no-var (bad byte)"),
        ])


def test_suppressions_read_as_utf8() -> None:
    """A locale whose codec is not UTF-8 (Windows' cp1252) does not change the reading."""
    with tempfile.TemporaryDirectory() as tmp:
        fp = Path(tmp) / "u.js"
        fp.write_text("// eslint-disable-line no-var -- caf\u00e9\n", encoding="utf-8")

        def cp1252_locale(encoding: str | None, stacklevel: int = 2) -> str:
            return "cp1252" if encoding is None else encoding

        # pathlib asks io.text_encoding for the codec of an encoding=None read
        with mock.patch.object(io, "text_encoding", cp1252_locale):
            got = outcome(js_tools._suppressions_in, fp)
            control = fp.read_text()
        check("suppressions: the cp1252 stand-in reads an encoding=None read", control,
              "// eslint-disable-line no-var -- cafÃ©\n")
        check("suppressions: read as UTF-8 whatever the locale", got,
              [suppression(fp, 1, "no-var", "Acknowledged suppression: no-var (caf\u00e9)")])


# --------------------------------------------------- _run_node_runner, run_eslint

class Runs:
    """A stand-in for subprocess.run that records each call and answers RESULT."""

    def __init__(self, returncode: int = 0, stdout: str = "[]", stderr: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.answer = (returncode, stdout, stderr)
        self.last: subprocess.CompletedProcess[str] | None = None

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        code, out, err = self.answer
        self.last = subprocess.CompletedProcess(argv, code, out, err)
        return self.last


@contextlib.contextmanager
def faked(on_path: dict[str, str], runs: Runs, runner: bool = True,
          package: bool = True) -> Iterator[Path]:
    """js_tools with PATH holding ON_PATH, a tools root with (or without) the
    eslint runner and the eslint package, and RUNS as subprocess.run."""
    saved = {n: getattr(js_tools, n) for n in ("shutil", "subprocess", "TOOLS_ROOT", "node_module_roots")}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "tools"
        (root / "node_modules").mkdir(parents=True)
        if package:
            (root / "node_modules" / "eslint").mkdir()
        if runner:
            (root / "jp_eslint.mjs").write_text("// runner\n", encoding="utf-8")
        fakes = {"shutil": SimpleNamespace(which=on_path.get),
                 "subprocess": SimpleNamespace(run=runs, CompletedProcess=subprocess.CompletedProcess),
                 "TOOLS_ROOT": root,
                 "node_module_roots": lambda: [root / "node_modules"]}
        for n, v in fakes.items():
            setattr(js_tools, n, v)
        try:
            yield root
        finally:
            for n, v in saved.items():
                setattr(js_tools, n, v)


def missing(note: str) -> dict[str, Any]:
    return {"tool": "eslint", "status": "unavailable", "issues": [], "note": f"'eslint' not found: {note}"}


def test_runner_node_missing() -> None:
    runs = Runs()
    with faked({}, runs):
        check("runner: node missing", js_tools._run_node_runner("eslint", "jp_eslint.mjs", "t.js"),
              missing("needs node, which is not on PATH"))
    check("runner: node missing runs nothing", runs.calls, [])


def test_runner_argv_and_options() -> None:
    runs = Runs()
    with faked({"node": "/bin/node"}, runs) as root:
        got = js_tools._run_node_runner("eslint", "jp_eslint.mjs", "t.js")
        env = {**os.environ, "JP_TOOLS_NODE_ROOTS": str(root / "node_modules")}
        check("runner: argv and options", (got is runs.last, runs.calls), (True, [
            (["/bin/node", str(root / "jp_eslint.mjs"), "t.js"],
             {"capture_output": True, "text": True, "check": False, "cwd": str(root), "env": env}),
        ]))


def test_runner_node_exe() -> None:
    runs = Runs()
    with faked({"node.exe": "C:/node/node.exe"}, runs) as root:
        js_tools._run_node_runner("eslint", "jp_eslint.mjs", "t.js")
        check("runner: node.exe when there is no node", [argv for argv, _ in runs.calls],
              [["C:/node/node.exe", str(root / "jp_eslint.mjs"), "t.js"]])


def test_runner_script_missing() -> None:
    runs = Runs()
    with faked({"node": "/bin/node"}, runs, runner=False):
        check("runner: runner script missing", js_tools._run_node_runner("eslint", "jp_eslint.mjs", "t.js"),
              missing("jp_eslint.mjs is missing from this checkout"))


def test_runner_package_missing() -> None:
    runs = Runs()
    with faked({"node": "/bin/node"}, runs, package=False):
        check("runner: package missing", js_tools._run_node_runner("eslint", "jp_eslint.mjs", "t.js"),
              missing("run: npm install in JP_TOOLS"))


def test_runner_failed() -> None:
    runs = Runs(returncode=1, stdout="", stderr="Error: boom\n")
    with faked({"node": "/bin/node"}, runs):
        check("runner: a runner that fails is reported",
              js_tools._run_node_runner("eslint", "jp_eslint.mjs", "t.js"),
              {"tool": "eslint", "status": "error", "issues": [], "note": "runner exited 1: Error: boom"})


def test_run_eslint_node_missing() -> None:
    with faked({}, Runs()):
        check("run_eslint: node missing names eslint", js_tools.run_eslint("t.js"),
              missing("needs node, which is not on PATH"))


def test_run_eslint_runner_missing() -> None:
    with faked({"node": "/bin/node"}, Runs(), runner=False):
        check("run_eslint: runner missing names jp_eslint.mjs", js_tools.run_eslint("t.js"),
              missing("jp_eslint.mjs is missing from this checkout"))


def test_run_eslint_issues() -> None:
    out = json.dumps([{"filePath": "/src/a.js", "messages": [
        {"line": 1, "column": 2, "severity": 1, "ruleId": "no-console", "message": "m"}]}])
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "t.js"
        target.write_text("// eslint-disable-line no-var -- why\n", encoding="utf-8")
        with faked({"node": "/bin/node"}, Runs(stdout=out)):
            got = outcome(js_tools.run_eslint, str(target))
        check("run_eslint: issues from the runner, then suppressions", got, {
            "tool": "eslint", "status": "warn", "issues": [
                {"file": "/src/a.js", "line": 1, "col": 2, "severity": "warning", "rule": "no-console",
                 "message": "m", "fixable": False},
                suppression(target, 1, "no-var", "Acknowledged suppression: no-var (why)"),
            ]})


def test_run_eslint_unparsed_with_stderr() -> None:
    with faked({"node": "/bin/node"}, Runs(stdout="not json", stderr="  it broke  \n")):
        check("run_eslint: unparsed output with stderr", js_tools.run_eslint("t.js"),
              {"tool": "eslint", "status": "error", "issues": [], "note": "it broke"})


def test_run_eslint_unparsed_no_stderr() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "t.js"
        target.write_text("// eslint-disable-line no-var\n", encoding="utf-8")
        with faked({"node": "/bin/node"}, Runs(stdout="not json")):
            got = outcome(js_tools.run_eslint, str(target))
        check("run_eslint: unparsed output, no stderr", got, {
            "tool": "eslint", "status": "warn",
            "issues": [suppression(target, 1, "no-var", "Acknowledged suppression: no-var")]})


if __name__ == "__main__":
    sys.exit(run(globals(), "js_tools eslint"))
