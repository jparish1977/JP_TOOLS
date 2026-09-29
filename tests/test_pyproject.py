#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_pyproject.py
pyproject.toml declares the project as METHODOLOGY §7 item 10 asks, and stays
true as the code changes.

    - [project] carries name, version, requires-python and a dependencies list.
    - It is not JP_TOOLS' ruff or mypy config: check.py still hands both tools
      the shared configs/ files for this repo.
    - requires-python's floor is configs/ruff.toml's target-version, so a fix
      ruff writes cannot need a newer Python than the one declared.
    - Every import that is neither standard library nor this checkout's own is
      declared: in dependencies or optional-dependencies, or, when only tests
      import it, in requirements-dev.txt, where the test and gate tools are
      pinned. An import name is mapped to its distribution by
      importlib.metadata (yaml is PyYAML), else read as the name, and compared
      case-insensitively with "_" read as "-".

The import scan is checked against a planted tree first, so it cannot pass by
finding nothing.

    python3 tests/test_pyproject.py
"""

import ast
import re
import subprocess
import sys
import tempfile
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

from _harness import ROOT, check, check_true, run
from jpcheck.python_tools import _mypy_config_args, _ruff_config_args

PYPROJECT = ROOT / "pyproject.toml"


def project_table() -> dict[str, object]:
    """pyproject.toml's [project] table, or {} when there is none."""
    with PYPROJECT.open("rb") as fh:
        table = tomllib.load(fh).get("project")
    return table if isinstance(table, dict) else {}


def strings(value: object) -> list[str]:
    """VALUE's string items when it is a list, else none."""
    return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []


def normalise(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def declared(project: dict[str, object]) -> set[str]:
    """Distribution names in dependencies and every optional-dependencies group."""
    specs = strings(project.get("dependencies"))
    groups = project.get("optional-dependencies")
    if isinstance(groups, dict):
        for group in groups.values():
            specs.extend(strings(group))
    return {normalise(re.split(r"[<>=!~\[; ]", s, maxsplit=1)[0]) for s in specs}


def declared_dev() -> set[str]:
    """Distribution names pinned in requirements-dev.txt."""
    lines = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
    return {normalise(re.split(r"[<>=!~\[; ]", ln.strip(), maxsplit=1)[0])
            for ln in lines if ln.strip() and not ln.lstrip().startswith("#")}


def distribution(import_name: str) -> str:
    """The distribution that provides IMPORT_NAME, normalised; the name itself if unknown."""
    return normalise(packages_distributions().get(import_name, [import_name])[0])


def top_level_imports(source: str, filename: str) -> set[str]:
    """The first dotted part of every absolute import in SOURCE."""
    names: list[str] = []
    for node in ast.walk(ast.parse(source, filename)):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return {n.split(".")[0] for n in names}


def imported(root: Path, files: list[str]) -> dict[str, set[str]]:
    """Top-level import names in FILES that are not stdlib and not first-party, with where."""
    first_party = {Path(f).stem for f in files}
    first_party |= {p.name for p in root.iterdir() if (p / "__init__.py").exists()}
    found: dict[str, set[str]] = {}
    for f in files:
        for top in top_level_imports((root / f).read_text(encoding="utf-8"), f):
            if top not in sys.stdlib_module_names and top not in first_party:
                found.setdefault(distribution(top), set()).add(f)
    return found


def tracked_python() -> list[str]:
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"],
                       capture_output=True, text=True, check=True)
    return r.stdout.split()


def test_project_table() -> None:
    project = project_table()
    check_true("pyproject.toml has a [project] table", bool(project))
    for key in ("name", "version", "requires-python"):
        check_true(f"[project] declares {key}", bool(project.get(key)))
    check("dependencies is a list, empty when stdlib only",
          type(project.get("dependencies")), list)


def test_not_the_tool_config() -> None:
    target = str(ROOT / "check.py")
    shared = str(ROOT / "configs")
    ruff = _ruff_config_args(target)
    mypy = _mypy_config_args(target)
    check_true("ruff still gets configs/ruff.toml for this repo",
               len(ruff) == 2 and ruff[1].startswith(shared))
    check_true("mypy still gets configs/mypy.ini for this repo",
               len(mypy) == 2 and mypy[1].startswith(shared))


def test_python_floor_matches_ruff_target() -> None:
    with (ROOT / "configs" / "ruff.toml").open("rb") as fh:
        target = tomllib.load(fh)["target-version"]
    floor = project_table().get("requires-python")
    check("requires-python is ruff's target-version",
          floor, f">=3.{target.removeprefix('py3')}")


def test_scan_finds_a_planted_import() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pkg").mkdir()
        (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (root / "helper.py").write_text("X = 1\n", encoding="utf-8")
        (root / "tool.py").write_text(
            "import json\nimport pkg.sub\nfrom helper import X\n"
            "from . import sibling\nimport Not_Real.thing\n", encoding="utf-8")
        found = imported(root, ["pkg/__init__.py", "helper.py", "tool.py"])
    check("control: only the planted third-party import is reported",
          sorted(found), ["not-real"])


def test_every_import_is_declared() -> None:
    found = imported(ROOT, tracked_python())
    check_true("control: the scan finds autoit-extract.py's optional import",
               "autoit-ripper" in found)
    runtime, dev = declared(project_table()), declared_dev()
    missing = {name: sorted(where) for name, where in found.items()
               if name not in runtime
               and not (name in dev and all(f.startswith("tests/") for f in where))}
    check("every third-party import is declared in pyproject.toml", missing, {})
    check("the tests' yaml resolves to PyYAML, declared in requirements-dev.txt",
          ("pyyaml" in found, "pyyaml" in dev, "pyyaml" in runtime), (True, True, False))


def test_a_dev_dependency_does_not_cover_product_code() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "tests").mkdir()
        (root / "tests" / "t.py").write_text("import yaml\n", encoding="utf-8")
        (root / "tool.py").write_text("import yaml\n", encoding="utf-8")
        found = imported(root, ["tests/t.py", "tool.py"])
    where = found.get("pyyaml", set())
    check_true("control: product code importing a dev-only package is not covered",
               "pyyaml" in declared_dev() and not all(f.startswith("tests/") for f in where))


if __name__ == "__main__":
    sys.exit(run(globals(), "pyproject"))
