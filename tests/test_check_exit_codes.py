#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_exit_codes.py
check.py's exit code must say whether the checks RAN, not only what they found.

THE BUG THIS PINS DOWN (#29)
    The exit code was `1 if errors else 0`, with errors counted from issues, so
    no tool status could ever fail a run. Measured 2026-09-14: a file with one
    ruff and one mypy error exited 1 with the tools on PATH and 0 with them
    removed, reporting both as "unavailable". A CI job whose linters were not
    installed went green having checked nothing.

    Two relatives fixed alongside, each with a case below:
      - run_ruff discarded ruff's return code, so a config that fails to load
        (ruff exit 2, empty stdout) read as "pass".
      - Directory mode merged per-file runners by keeping only "fail", so an
        unavailable eslint read as "pass".

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
from pathlib import Path
from typing import Any

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
    # Control: the same file under JP_TOOLS' own config is dirty, so the "error"
    # above is the config failing, not a clean file.
    control = tmp / "control"
    control.mkdir()
    (control / "sample.py").write_text(DIRTY)
    rc, doc, _ = run(["sample.py", "--tools", "ruff"], control)
    expect("control, same file, loadable config: ruff", statuses(doc).get("ruff"), "fail")
    expect("control, same file, loadable config: exit", rc, 1)


def test_directory_per_file_merge(tmp: Path) -> None:
    tree = tmp / "jsrepo"
    tree.mkdir()
    (tree / "app.js").write_text("const x = 1;\nexport default x;\n")
    rc, doc, _ = run(["."], tree, no_tools=True)
    got = statuses(doc).get("eslint")
    expect("directory, no node: eslint did not report pass (was pass)",
           got not in (None, "pass"), True)
    expect("directory, no node: exit (was 0)", rc, 2)


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
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print(f"all check.py exit-code tests passed ({len(tests)} groups)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
