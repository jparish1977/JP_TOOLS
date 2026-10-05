#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_project_config.py
A pyproject.toml or setup.cfg is a project's ruff or mypy config only when it
carries that tool's section, and mypy is handed the one found by path.

THE GAP THIS PINS DOWN
    check.py hands a tool no config, and lets it discover the project's own,
    when it finds a project-local config file. It counted ANY pyproject.toml
    (and, for mypy, any setup.cfg). ruff skips a pyproject.toml with no
    [tool.ruff] and mypy a setup.cfg with no [mypy], so a project that only
    declared its own name and dependencies would run on the tools' built-in
    defaults, with every rule configs/ruff.toml adds switched off.

AND mypy stops looking at the repository root (.git or .hg) where ruff does
not, so a [tool.mypy] above a repo is not that repo's config: counting it
handed mypy nothing, the same silent loss of the defaults one level up.

AND mypy looks for its config from its working directory, not from the file
it checks. A [tool.mypy] in a subdirectory of a repo, checked from the repo
root as the hook and CI do, was found here and never read by mypy, so mypy
ran on its built-in defaults. The config found is passed as --config-file.

The config choice is checked directly, and end to end through ruff and mypy.
Each end-to-end part skips when its tool is missing.

    python3 tests/test_check_project_config.py
"""

import json
import os
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
# warn_unreachable is not part of mypy's strict set, so only a project config
# that turns it on reports this, and the JP_TOOLS defaults do not.
UNREACHABLE = ("def f(x: int) -> int:\n"
               "    if isinstance(x, str):\n"
               "        return 1\n"
               "    return x\n")
WARN_UNREACHABLE = BARE_PYPROJECT + "\n[tool.mypy]\nwarn_unreachable = true\n"

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
    """ARGS name the JP_TOOLS shared config, not a project's."""
    return (len(args) == 2 and args[0] in ("--config", "--config-file")
            and Path(args[1]).parent == ROOT / "configs")


def uses_project(args: list[str], config: Path) -> bool:
    """ARGS hand mypy the project's own CONFIG by path."""
    return args == ["--config-file", str(config.resolve())]


def rules(target: Path, tool: str, cwd: Path) -> set[str]:
    """The rules check.py reports for TARGET with only TOOL, run from CWD.

    HOME is CWD, so no user-level config of the tool's can stand in for the
    one under test.
    """
    r = subprocess.run([sys.executable, str(CHECK_PY), str(target), "--tools", tool],
                       capture_output=True, text=True, check=False, cwd=cwd,
                       env={**os.environ, "HOME": str(cwd)})
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        # reason: not a check.py report: no rules listed, which fails the checks that expect some
        return set()
    return {str(i.get("rule")) for c in doc.get("checks", []) for i in c.get("issues", [])}


def ruff_cases(base: Path) -> None:
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


def mypy_cases(base: Path) -> None:
    print("\nmypy:")
    t = tree(base, {"setup.cfg": "[flake8]\nmax-line-length = 100\n"})
    check("a setup.cfg with no [mypy]: still the JP_TOOLS defaults",
          uses_defaults(_mypy_config_args(str(t))))
    t = tree(base, {"setup.cfg": "[mypy]\nstrict = True\n"})
    check("a setup.cfg with [mypy] is the project's own, passed by path",
          uses_project(_mypy_config_args(str(t)), t.parent / "setup.cfg"))
    t = tree(base, {"pyproject.toml": BARE_PYPROJECT})
    check("a pyproject.toml with no [tool.mypy]: still the JP_TOOLS defaults",
          uses_defaults(_mypy_config_args(str(t))))
    t = tree(base, {"pyproject.toml": BARE_PYPROJECT + "\n[tool.mypy]\nstrict = true\n"})
    check("a pyproject.toml with [tool.mypy] is the project's own, passed by path",
          uses_project(_mypy_config_args(str(t)), t.parent / "pyproject.toml"))
    t = tree(base, {"mypy.ini": "[mypy]\n"})
    check("a mypy.ini is the project's own, passed by path",
          uses_project(_mypy_config_args(str(t)), t.parent / "mypy.ini"))
    above = BARE_PYPROJECT + "\n[tool.mypy]\nstrict = true\n\n[tool.ruff]\nline-length = 90\n"
    t = tree(base, {"pyproject.toml": above, "sub/.git/HEAD": "ref: refs/heads/main\n",
                    "sub/m.py": "x = 1\n"})
    check("a [tool.mypy] ABOVE a git repo is not the repo's: still the JP_TOOLS defaults",
          uses_defaults(_mypy_config_args(str(t))))
    check("...while ruff, which walks past the repo root, still counts it",
          _ruff_config_args(str(t)) == [])
    t = tree(base, {"pyproject.toml": above, "sub/.hg/requires": "store\n",
                    "sub/m.py": "x = 1\n"})
    check("the same above an hg repo",
          uses_defaults(_mypy_config_args(str(t))))
    t = tree(base, {"pyproject.toml": above, "sub/m.py": "x = 1\n"})
    check("control: with no repo root between, mypy's walk reaches it",
          uses_project(_mypy_config_args(str(t)), t.parent.parent / "pyproject.toml"))
    t = tree(base, {"sub/.git/HEAD": "ref: refs/heads/main\n",
                    "sub/pyproject.toml": BARE_PYPROJECT + "\n[tool.mypy]\n",
                    "sub/m.py": "x = 1\n"})
    check("a [tool.mypy] AT the repo root is the project's own",
          uses_project(_mypy_config_args(str(t)), t.parent / "pyproject.toml"))


def ruff_end_to_end(base: Path) -> None:
    if not shutil.which("ruff"):
        print("  SKIP: ruff is not installed")
        return
    t = tree(base, {"m.py": CONFIG_ONLY_FINDINGS})
    alone = rules(t, "ruff", t.parent)
    t = tree(base, {"m.py": CONFIG_ONLY_FINDINGS, "pyproject.toml": BARE_PYPROJECT})
    beside = rules(t, "ruff", t.parent)
    check("control: the JP_TOOLS-only rules fire with no pyproject.toml",
          {"N802", "C416"} <= alone, str(sorted(alone)))
    check("and still fire beside a pyproject.toml that only declares the project",
          {"N802", "C416"} <= beside, str(sorted(beside)))


def ruff_status(target: Path, env: dict[str, str]) -> str:
    """check.py's ruff status for TARGET, run from its directory with ENV."""
    r = subprocess.run([sys.executable, str(CHECK_PY), str(target), "--tools", "ruff"],
                       capture_output=True, text=True, check=False, cwd=target.parent, env=env)
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        # reason: not a check.py report: no status, which fails the checks that expect one
        return ""
    return next((str(c.get("status")) for c in doc.get("checks", []) if c.get("tool") == "ruff"), "")


def ruff_extend_end_to_end(base: Path) -> None:
    """A project ruff.toml that extends JP_TOOLS through JP_TOOLS_ROOT, with no
    JP_TOOLS beside it: the layout of a git worktree of the project."""
    if not shutil.which("ruff"):
        print("  SKIP: ruff is not installed")
        return
    t = tree(base, {"m.py": CONFIG_ONLY_FINDINGS,
                    "ruff.toml": 'extend = "${JP_TOOLS_ROOT:-../JP_TOOLS}/configs/ruff.toml"\n'})
    found = rules(t, "ruff", t.parent)
    check("extending through JP_TOOLS_ROOT loads the JP_TOOLS rules with no JP_TOOLS beside",
          {"N802", "C416"} <= found, str(sorted(found)))
    wrong = {**os.environ, "HOME": str(t.parent), "JP_TOOLS_ROOT": str(base / "no-such-jp-tools")}
    check("check.py sets JP_TOOLS_ROOT to its own checkout over the caller's",
          ruff_status(t, wrong) == "fail", ruff_status(t, wrong))
    t = tree(base, {"m.py": CONFIG_ONLY_FINDINGS,
                    "ruff.toml": 'extend = "../JP_TOOLS/configs/ruff.toml"\n'})
    status = ruff_status(t, {**os.environ, "HOME": str(t.parent)})
    check("control: the plain relative extend cannot load there, and says so",
          status == "error", status)


def mypy_end_to_end(base: Path) -> None:
    if not shutil.which("mypy"):
        print("  SKIP: mypy is not installed")
        return
    t = tree(base, {".git/HEAD": "ref: refs/heads/main\n",
                    "sub/pyproject.toml": WARN_UNREACHABLE, "sub/m.py": UNREACHABLE})
    repo = t.parent.parent
    check("control: run from the project's own directory, its [tool.mypy] is read",
          "mypy:unreachable" in rules(t, "mypy", t.parent))
    found = rules(t, "mypy", repo)
    check("run from the repo root, as the hook and CI run, it is still read",
          "mypy:unreachable" in found, str(sorted(found)))
    t = tree(base, {".git/HEAD": "ref: refs/heads/main\n", "sub/m.py": UNREACHABLE})
    found = rules(t, "mypy", t.parent.parent)
    check("control: with no project config the JP_TOOLS defaults do not report it",
          "mypy:unreachable" not in found, str(sorted(found)))


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        ruff_cases(base)
        mypy_cases(base)
        print("\nEnd to end:")
        ruff_end_to_end(base)
        ruff_extend_end_to_end(base)
        mypy_end_to_end(base)

    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
