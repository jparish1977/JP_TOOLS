"""What a changed file's path tells mutmut-gate.py: the module mutmut keys its
functions by, whether tests other than its own read it, and whether it is a
suite, and which suites read it. Paths are as git names them, from the
checkout's root.
"""

import re

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


def readers(path: str, tests: dict[str, bytes]) -> set[str] | None:
    """The suites, among TESTS (each test file's text by its path), whose
    reach a change to the shared file PATH can move: a Python helper is read
    by the files with a line that says import and names its module as a word
    (every form of import, importlib's too; one that only looks like one
    counts as well, and costs only a re-trace), a data file by those that
    name it (by its file name). None when the change can move any test's
    reach: a conftest.py or a configuration file, a file nothing names, or
    one a file that is not a suite names (a helper, so whoever imports that
    reads it too). Matched as bytes: a test file that is not valid UTF-8 is
    still searched."""
    name = path.rpartition("/")[2]
    if name == "conftest.py" or path in CONFIG_FILES:
        return None
    if name.endswith(".py"):
        stem = re.escape(name.removesuffix(".py")).encode()
        reads = re.compile(rb"(?m)^(?=.*import).*\b" + stem + rb"\b")
        naming = {p for p, text in tests.items() if reads.search(text)}
    else:
        naming = {p for p, text in tests.items() if name.encode() in text}
    if not naming or not all(map(is_suite, naming)):
        return None
    return naming
