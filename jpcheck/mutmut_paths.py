"""What a changed file's path tells mutmut-gate.py: the module mutmut keys its
functions by, whether tests other than its own read it, and whether it is a
suite. Paths are as git names them, from the checkout's root.
"""

# The files at the root pytest reads its configuration from: testpaths and
# python_functions decide what it collects at all.
CONFIG_FILES = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")


def module(path: str) -> str | None:
    """The module mutmut keys a source file's functions by: jpcheck/lab_use.py
    is jpcheck.lab_use, a package's __init__.py the package, mutmut-gate.py
    mutmut-gate. None for a file that is not Python."""
    if not path.endswith(".py"):
        return None
    return path.removesuffix(".py").removesuffix("/__init__").replace("/", ".")


def shared_by_tests(path: str) -> bool:
    """A file whose change can move the reach of tests that are not in it:
    a conftest.py anywhere; at the root, each of CONFIG_FILES; and under
    tests/ anything but a test_*.py suite and the mutant specs (helpers, case
    tables, fixtures)."""
    name = path.rpartition("/")[2]
    if name == "conftest.py" or path in CONFIG_FILES:
        return True
    if not path.startswith("tests/") or path.startswith("tests/mutants/"):
        return False
    return not (name.startswith("test_") and name.endswith(".py"))


def is_suite(path: str) -> bool:
    """A test_*.py file under tests/, at any depth."""
    name = path.rpartition("/")[2]
    return path.startswith("tests/") and name.startswith("test_") and name.endswith(".py")
