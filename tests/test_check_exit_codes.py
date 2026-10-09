#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_exit_codes.py
check.py's exit code must say whether the checks RAN, not only what they found.

THE RULE THIS PINS DOWN
    An exit code counted from issues alone lets no tool status fail a run: a
    file with one ruff and one mypy error exits 1 with the tools on PATH and 0
    with them removed, both reported as "unavailable", and a CI job whose
    linters are not installed goes green having checked nothing. So a check
    that did not run is exit 2.

    Two relatives, each with a case below:
      - ruff's own return code counts: a config that fails to load (ruff exit
        2, empty stdout) is not a pass.
      - Directory mode merging per-file runners keeps "did not run" as well as
        "fail", so an unavailable eslint is not a pass.

Every case that expects 2 has a matching control: the same input, with the
tools present, gives a verdict. Without the controls these would pass on a
check.py that exited 2 for everything.

ruff and mypy are REQUIRED (requirements-dev.txt). Their absence is a failure
here rather than a skip, which is the rule this file exists to enforce.

No pytest, no dependencies.

    python tests/test_check_exit_codes.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

from _harness import CHECKOUT_COPY_IGNORE

CHECK = Path(__file__).resolve().parent.parent / "check.py"

CLEAN = "def f(x: int) -> int:\n    return x\n"
DIRTY = "import os\n\n\ndef f(x: int) -> str:\n    return x\n"

FAILURES: list[str] = []


def expect(label: str, got: object, want: object) -> None:
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


def run(args: list[str], cwd: Path, no_tools: bool = False) -> tuple[int, dict[str, Any], str]:
    env = dict(os.environ)
    if no_tools:
        # An empty directory as the whole PATH: every tool check.py looks up
        # by name is absent. check.py itself runs from sys.executable.
        empty = cwd / "_empty_path"
        empty.mkdir(exist_ok=True)
        env["PATH"] = str(empty)
    r = subprocess.run([sys.executable, str(CHECK), *args], cwd=cwd, env=env,
                       capture_output=True, text=True, check=False, timeout=300)
    try:
        doc = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        doc = {}
    return r.returncode, doc, r.stderr


def statuses(doc: dict[str, Any]) -> dict[str, str]:
    return {c["tool"]: c["status"] for c in doc.get("checks", [])}


def notes(doc: dict[str, Any]) -> dict[str, str]:
    return {c["tool"]: c["note"] for c in doc.get("checks", []) if "note" in c}


def test_tools_present(tmp: Path) -> None:
    (tmp / "clean.py").write_text(CLEAN)
    (tmp / "dirty.py").write_text(DIRTY)
    rc, doc, _ = run(["clean.py"], tmp)
    expect("clean file, tools present: exit", rc, 0)
    expect("clean file, tools present: ruff", statuses(doc).get("ruff"), "pass")
    rc, doc, _ = run(["dirty.py"], tmp)
    expect("dirty file, tools present: exit", rc, 1)


def test_tools_absent(tmp: Path) -> None:
    (tmp / "clean.py").write_text(CLEAN)
    (tmp / "dirty.py").write_text(DIRTY)
    for name in ("clean.py", "dirty.py"):
        rc, doc, err = run([name], tmp, no_tools=True)
        expect(f"{name}, tools absent: exit (was 0)", rc, 2)
        expect(f"{name}, tools absent: ruff", statuses(doc).get("ruff"), "unavailable")
        expect(f"{name}, tools absent: mypy", statuses(doc).get("mypy"), "unavailable")
        expect(f"{name}, tools absent: stderr names ruff", "ruff" in err, True)
        expect(f"{name}, tools absent: stderr names mypy", "mypy" in err, True)


def test_ruff_config_fails_to_load(tmp: Path) -> None:
    broken = tmp / "broken"
    broken.mkdir()
    (broken / "ruff.toml").write_text('extend = "./does-not-exist.toml"\n')
    (broken / "sample.py").write_text(DIRTY)
    rc, doc, _ = run(["sample.py", "--tools", "ruff"], broken)
    expect("broken ruff.toml: ruff status (was pass)", statuses(doc).get("ruff"), "error")
    expect("broken ruff.toml: exit (was 0)", rc, 2)
    expect("broken ruff.toml: the note gives ruff's exit and its reason",
           notes(doc).get("ruff", "").startswith("exit 2: ") and len(notes(doc)["ruff"]) > 8, True)
    # Control: the same file under JP_TOOLS' own config is dirty, so the "error"
    # above is the config failing, not a clean file.
    control = tmp / "control"
    control.mkdir()
    (control / "sample.py").write_text(DIRTY)
    rc, doc, _ = run(["sample.py", "--tools", "ruff"], control)
    expect("control, same file, loadable config: ruff", statuses(doc).get("ruff"), "fail")
    expect("control, same file, loadable config: exit", rc, 1)


def test_mypy_that_cannot_run_says_why(tmp: Path) -> None:
    broken = tmp / "mypy-broken"
    broken.mkdir()
    (broken / ".git").mkdir()
    (broken / "mypy.ini").write_text("[mypy]\nplugins = no_such_plugin_anywhere\n")
    (broken / "sample.py").write_text(CLEAN)
    rc, doc, err = run(["sample.py", "--tools", "mypy"], broken)
    expect("mypy with a plugin that does not exist: status", statuses(doc).get("mypy"), "error")
    expect("mypy that cannot run: exit", rc, 2)
    note = notes(doc).get("mypy", "")
    expect("mypy that cannot run: the note names the plugin mypy could not load",
           note.startswith("exit ") and "no_such_plugin_anywhere" in note, True)
    expect("mypy that cannot run: stderr carries the note", "no_such_plugin_anywhere" in err, True)
    # Control: the same file without the broken config passes, with no note.
    (broken / "mypy.ini").unlink()
    rc, doc, _ = run(["sample.py", "--tools", "mypy"], broken)
    expect("control, same file, no broken config: mypy", statuses(doc).get("mypy"), "pass")
    expect("control: a check that ran carries no note", "mypy" in notes(doc), False)


def test_the_note_reads_stderr_first(tmp: Path) -> None:
    # A tool that crashes can leave partial output on stdout and its reason
    # on stderr; the reason is the note.
    fake = tmp / "fakebin"
    fake.mkdir()
    (fake / "ruff").write_text(f"#!{sys.executable}\nimport sys\n"
                               "print('partial output')\n"
                               "print('the real reason', file=sys.stderr)\n"
                               "sys.exit(2)\n")
    (fake / "ruff").chmod(0o755)
    (tmp / "sample.py").write_text(CLEAN)
    env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}")
    r = subprocess.run([sys.executable, str(CHECK), "sample.py", "--tools", "ruff"], cwd=tmp,
                       env=env, capture_output=True, text=True, check=False, timeout=300)
    doc = json.loads(r.stdout) if r.stdout.strip() else {}
    expect("a crash with output on both streams: the note is stderr's",
           notes(doc).get("ruff"), "exit 2: the real reason")
    # A crash that ends on a hint: the note is the line naming the error.
    (fake / "ruff").write_text(f"#!{sys.executable}\nimport sys\n"
                               "print('RuntimeError: the cache was half written', file=sys.stderr)\n"
                               "print('note: rerun with a flag for more', file=sys.stderr)\n"
                               "sys.exit(2)\n")
    r = subprocess.run([sys.executable, str(CHECK), "sample.py", "--tools", "ruff"], cwd=tmp,
                       env=env, capture_output=True, text=True, check=False, timeout=300)
    doc = json.loads(r.stdout) if r.stdout.strip() else {}
    expect("a crash ending on a hint: the note is the error line",
           notes(doc).get("ruff"), "exit 2: RuntimeError: the cache was half written")


def test_directory_per_file_merge(tmp: Path) -> None:
    tree = tmp / "jsrepo"
    tree.mkdir()
    (tree / "app.js").write_text("const x = 1;\nexport default x;\n")
    rc, doc, _ = run(["."], tree, no_tools=True)
    got = statuses(doc).get("eslint")
    expect("directory, no node: eslint did not report pass (was pass)",
           got not in (None, "pass"), True)
    expect("directory, no node: exit (was 0)", rc, 2)


FAKE_NODE = """#!{python}
import os, sys
sys.stdout.write(os.environ.get("FAKE_NODE_OUT", ""))
sys.stderr.write(os.environ.get("FAKE_NODE_ERR", ""))
sys.exit(int(os.environ.get("FAKE_NODE_RC", "0")))
"""


def tree_with_packages(tmp: Path) -> Path:
    """A copy of this checkout whose node_modules holds (empty) eslint and stylelint.

    check.py reports a JS tool whose package is in no node_modules as not
    installed before starting node. These cases are about a runner that WAS
    started and failed, so the package must look present, on any box: CI's
    Python job has no node_modules, and a worktree run with only the fake node
    on PATH has no git to find the main checkout's.
    """
    tree = tmp / "_tree"
    if not tree.exists():
        shutil.copytree(CHECK.parent, tree, symlinks=True, ignore=CHECKOUT_COPY_IGNORE)
        for package in ("eslint", "stylelint"):
            (tree / "node_modules" / package).mkdir(parents=True)
    return tree


def run_with_fake_node(tmp: Path, target: str, tool: str, out: str, err: str,
                       rc: int) -> tuple[int, dict[str, Any]]:
    # A fake `node` alone on PATH, so the runner script is "found" and its
    # outcome is exactly what the fake says, whatever this box has installed.
    bindir = tmp / "_fakebin"
    bindir.mkdir(exist_ok=True)
    node = bindir / "node"
    node.write_text(FAKE_NODE.format(python=sys.executable))
    node.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir), FAKE_NODE_OUT=out,
               FAKE_NODE_ERR=err, FAKE_NODE_RC=str(rc))
    r = subprocess.run([sys.executable, str(tree_with_packages(tmp) / "check.py"), target,
                        "--tools", tool],
                       cwd=tmp, env=env, capture_output=True, text=True,
                       check=False, timeout=120)
    try:
        doc = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        doc = {}
    return r.returncode, doc


def test_node_runner_cannot_load(tmp: Path) -> None:
    (tmp / "bad.js").write_text("var x = 1\nconsole.log(x == 2)\n")
    (tmp / "bad.css").write_text("a { color: #FFF }\n")
    missing = ("Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'eslint' "
               "imported from jp_eslint.mjs\n")
    for tool, target in (("eslint", "bad.js"), ("stylelint", "bad.css")):
        rc, doc = run_with_fake_node(tmp, target, tool, "", missing, 1)
        expect(f"{tool}, runner cannot load: status (was pass)", statuses(doc).get(tool), "error")
        expect(f"{tool}, runner cannot load: exit (was 0)", rc, 2)
        # Control: the same fake, exiting 0 with an empty result, is a pass.
        rc, doc = run_with_fake_node(tmp, target, tool, "[]", "", 0)
        expect(f"{tool}, runner linted, nothing found: status", statuses(doc).get(tool), "pass")
        expect(f"{tool}, runner linted, nothing found: exit", rc, 0)


def test_skip_unsupported_unchanged(tmp: Path) -> None:
    # The pre-commit hook passes every staged file with --skip-unsupported and
    # blocks on any non-zero exit. A README must still exit 0 with no tools.
    (tmp / "README.txt").write_text("hello\n")
    rc, _, _ = run(["README.txt", "--skip-unsupported"], tmp, no_tools=True)
    expect("--skip-unsupported on a non-source file, tools absent: exit", rc, 0)


def main() -> int:
    missing = [t for t in ("ruff", "mypy") if not shutil.which(t)]
    if missing:
        print(f"FAILED: {', '.join(missing)} not on PATH. They are required "
              f"(pip install -r requirements-dev.txt); a skip here would be the "
              f"confident nothing this suite exists to refuse.")
        return 1
    tests = [fn for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    for fn in tests:
        # A test that raises is its own failure and the rest still run, as in
        # tests/_harness.py: a crash must not hide the checks after it.
        with tempfile.TemporaryDirectory() as d:
            try:
                fn(Path(d))
            except Exception as e:  # noqa: BLE001 -- reason: a crash is that test's failure, the run goes on
                traceback.print_exc()
                FAILURES.append(f"{fn.__name__} raised {type(e).__name__}: {e}")
    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for f in FAILURES:
            # "FAIL <label>: ...", as tests/_harness.py prints: a mutation
            # runner counts a mutant caught only on such a line.
            print(f"FAIL {f}")
        return 1
    print(f"all check.py exit-code tests passed ({len(tests)} groups)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
