#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_load_by_path.py
Every root script that imports one of this repo's packages still loads when
another program loads it BY PATH, and check.py keeps the names other repos
read off it.

WHY
    Run as `python3 check.py`, Python puts the script's own directory first
    on sys.path, so `from jpcheck... import` resolves. Loaded by path
    (importlib's spec_from_file_location, as other repos' gates do), nothing
    does, and the import fails. The suites here all import through _harness,
    which puts the checkout on sys.path first, so none of them could see it.
    Each load here runs in a fresh isolated interpreter (-I) in an empty
    directory, so the checkout is on no path unless the script puts it there.

WHAT IS CHECKED
    - Each root script importing a package of this repo loads by path.
    - check.py loaded by path exposes lab_claim, is_project_file and
      _smell_measure, the names other repos read off it.
    - Controls: the scan finds check.py and spool-audit.py, and a planted
      script that imports its sibling package without the path line FAILS to
      load, so a pass here is not the loader being unable to fail.

    python3 tests/test_load_by_path.py
"""

import ast
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, run

# Loads argv[1] by path under a module name that is not a package here, then
# prints each name in argv[2:] and whether the loaded module has it.
LOADER = """
import importlib.util, sys
path, names = sys.argv[1], sys.argv[2:]
spec = importlib.util.spec_from_file_location("loaded_by_path", path)
module = importlib.util.module_from_spec(spec)
sys.modules["loaded_by_path"] = module
spec.loader.exec_module(module)
for name in names:
    print(name, hasattr(module, name))
"""

# Names other repos read off check.py when they load it as a module.
CHECK_EXPORTS = ("lab_claim", "is_project_file", "_smell_measure")


def own_packages(root: Path) -> set[str]:
    return {p.parent.name for p in root.glob("*/__init__.py")}


def package_importers(root: Path) -> list[Path]:
    """Root scripts that import one of ROOT's own packages."""
    packages = own_packages(root)
    found = []
    for script in sorted(root.glob("*.py")):
        tree = ast.parse(script.read_text(encoding="utf-8"))
        tops = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tops |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                tops.add(node.module.split(".")[0])
        if tops & packages:
            found.append(script)
    return found


def load(script: Path, *names: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as empty:
        return subprocess.run([sys.executable, "-I", "-c", LOADER, str(script), *names],
                              capture_output=True, text=True, check=False, cwd=empty)


def test_the_scan_finds_the_scripts() -> None:
    names = {p.name for p in package_importers(ROOT)}
    check_true("control: the scan finds check.py", "check.py" in names)
    check_true("control: the scan finds spool-audit.py", "spool-audit.py" in names)


def test_every_package_importer_loads_by_path() -> None:
    for script in package_importers(ROOT):
        r = load(script)
        last = (r.stderr.strip().splitlines() or [""])[-1]
        check(f"{script.name} loads by path", (r.returncode, last), (0, ""))


def test_check_keeps_the_names_other_repos_read() -> None:
    r = load(ROOT / "check.py", *CHECK_EXPORTS)
    check("check.py by path exposes what other repos read",
          r.stdout.split(), [w for n in CHECK_EXPORTS for w in (n, "True")])


def test_a_script_without_the_path_line_fails_to_load() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "sibling").mkdir()
        (d / "sibling" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
        (d / "tool.py").write_text("from sibling import VALUE\n", encoding="utf-8")
        r = load(d / "tool.py")
    check_true("control: the loader reports a script that cannot import its package",
               r.returncode != 0 and "No module named 'sibling'" in r.stderr)


if __name__ == "__main__":
    sys.exit(run(globals(), "load-by-path"))
