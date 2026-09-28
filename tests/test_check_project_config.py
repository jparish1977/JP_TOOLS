#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_project_config.py
A pyproject.toml or setup.cfg is a project's ruff or mypy config only when it
carries that tool's section.

THE GAP THIS PINS DOWN
    check.py hands a tool no config, and lets it discover the project's own,
    when it finds a project-local config file. It counted ANY pyproject.toml
    (and, for mypy, any setup.cfg). ruff skips a pyproject.toml with no
    [tool.ruff] and mypy a setup.cfg with no [mypy], so a project that only
    declared its own name and dependencies would run on the tools' built-in
    defaults, with every rule configs/ruff.toml adds switched off.

The config choice is checked directly, and once end to end through ruff.
The end-to-end part skips when ruff is missing.

    python3 tests/test_check_project_config.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT
from jpcheck.python_tools import _mypy_config_args, _ruff_config_args

CHECK_PY = ROOT / "check.py"

BARE_PYPROJECT = '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
# N802 and C416 are on in configs/ruff.toml and off in ruff's built-in defaults.
CONFIG_ONLY_FINDINGS = ("import os\n\n\n"
                        "def BadName():\n"
                        "    return os.sep, [x for x in list(range(3))]\n")

fails = 0
checks = 0


def check(what: str, ok: bool, detail: str = "") -> None:
    global fails, checks
    checks += 1
    if not ok:
        fails += 1
    print(f"  [{' ok ' if ok else 'FAIL'}] {what}")
    if not ok and detail:
        for line in detail.strip().splitlines()[:12]:
            print(f"         {line}")


def tree(base: Path, files: dict[str, str]) -> Path:
    """Write FILES under a fresh directory in BASE and return the target file."""
    d = Path(tempfile.mkdtemp(dir=base))
    for name, text in files.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")
    target = d / "sub" / "m.py" if "sub/m.py" in files else d / "m.py"
    if not target.exists():
        target.write_text("x = 1\n", encoding="utf-8")
    return target


def uses_defaults(args: list[str]) -> bool:
    return len(args) == 2 and args[0] in ("--config", "--config-file")


def ruff_rules(target: Path) -> set[str]:
    r = subprocess.run([sys.executable, str(CHECK_PY), str(target), "--tools", "ruff"],
                       capture_output=True, text=True, check=False, cwd=target.parent)
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        # reason: not a check.py report: no rules listed, which fails the checks that expect some
        return set()
    return {str(i.get("rule")) for c in doc.get("checks", []) for i in c.get("issues", [])}


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        print("ruff:")
        t = tree(base, {})
        check("no config file: the JP_TOOLS defaults",
              uses_defaults(_ruff_config_args(str(t))))
        t = tree(base, {"ruff.toml": "line-length = 90\n"})
        check("a ruff.toml is the project's own",
              _ruff_config_args(str(t)) == [])
        t = tree(base, {"pyproject.toml": BARE_PYPROJECT})
        check("a pyproject.toml with no [tool.ruff]: still the JP_TOOLS defaults",
              uses_defaults(_ruff_config_args(str(t))))
        t = tree(base, {"pyproject.toml": BARE_PYPROJECT + "\n[tool.ruff]\nline-length = 90\n"})
        check("a pyproject.toml with [tool.ruff] is the project's own",
              _ruff_config_args(str(t)) == [])
        t = tree(base, {"pyproject.toml": BARE_PYPROJECT + "\n[tool.ruff.lint]\nselect = ['E']\n"})
        check("[tool.ruff.lint] alone counts as [tool.ruff]",
              _ruff_config_args(str(t)) == [])
        t = tree(base, {"pyproject.toml": "[project\nname = \n"})
        check("a pyproject.toml that does not parse is left to ruff, which reports it",
              _ruff_config_args(str(t)) == [])
        t = tree(base, {"ruff.toml": "line-length = 90\n",
                        "sub/pyproject.toml": BARE_PYPROJECT, "sub/m.py": "x = 1\n"})
        check("a bare pyproject.toml does not stop the walk: the ruff.toml above it counts",
              _ruff_config_args(str(t)) == [])

        print("\nmypy:")
        t = tree(base, {"setup.cfg": "[flake8]\nmax-line-length = 100\n"})
        check("a setup.cfg with no [mypy]: still the JP_TOOLS defaults",
              uses_defaults(_mypy_config_args(str(t))))
        t = tree(base, {"setup.cfg": "[mypy]\nstrict = True\n"})
        check("a setup.cfg with [mypy] is the project's own",
              _mypy_config_args(str(t)) == [])
        t = tree(base, {"pyproject.toml": BARE_PYPROJECT})
        check("a pyproject.toml with no [tool.mypy]: still the JP_TOOLS defaults",
              uses_defaults(_mypy_config_args(str(t))))
        t = tree(base, {"pyproject.toml": BARE_PYPROJECT + "\n[tool.mypy]\nstrict = true\n"})
        check("a pyproject.toml with [tool.mypy] is the project's own",
              _mypy_config_args(str(t)) == [])
        t = tree(base, {"mypy.ini": "[mypy]\n"})
        check("a mypy.ini is the project's own",
              _mypy_config_args(str(t)) == [])

        print("\nEnd to end:")
        if not shutil.which("ruff"):
            print("  SKIP: ruff is not installed")
        else:
            t = tree(base, {"m.py": CONFIG_ONLY_FINDINGS})
            alone = ruff_rules(t)
            t = tree(base, {"m.py": CONFIG_ONLY_FINDINGS, "pyproject.toml": BARE_PYPROJECT})
            beside = ruff_rules(t)
            check("control: the JP_TOOLS-only rules fire with no pyproject.toml",
                  {"N802", "C416"} <= alone, str(sorted(alone)))
            check("and still fire beside a pyproject.toml that only declares the project",
                  {"N802", "C416"} <= beside, str(sorted(beside)))

    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
