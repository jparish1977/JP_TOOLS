"""What a changed file's path tells mutmut-gate.py: the module mutmut keys its
functions by, whether tests other than its own read it, and whether it is a
suite, and which suites read it. Paths are as git names them, from the
checkout's root.

Which files are suites, and where they live, is the checkout's own pytest
layout (suite_layout): its python_files and testpaths as pytest reads them, so
a repo whose suites are dtchecks/*_test.py is judged by its layout, not by
JP_TOOLS' tests/test_*.py.
"""

import configparser
import fnmatch
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

# The files pytest reads its configuration from, in the order it looks, each
# with the section that holds it; pyproject.toml's is a TOML table.
PYTEST_CONFIGS = (("pytest.ini", "pytest"), (".pytest.ini", "pytest"),
                  ("pyproject.toml", "tool.pytest.ini_options"), ("tox.ini", "pytest"),
                  ("setup.cfg", "tool:pytest"))
# At the root, each of them: testpaths, python_files and python_functions
# decide what pytest collects at all.
CONFIG_FILES = tuple(name for name, _ in PYTEST_CONFIGS)
# pytest's own python_files, for a checkout that sets none.
DEFAULT_PYTHON_FILES = ("test_*.py", "*_test.py")
# The options read from that configuration.
_OPTIONS = ("python_files", "testpaths")
# Every configuration file is read as UTF-8, whatever the locale.
_UTF8 = "utf-8"


@dataclass(frozen=True)
class SuiteLayout:
    """Which files are pytest suites (by name) and which directories hold them."""
    python_files: tuple[str, ...]
    dirs: frozenset[str]


def _words(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else str(value).split()


def pytest_options(root: Path) -> dict[str, list[str]]:
    """The first pytest configuration found at ROOT, as pytest finds it: its
    python_files and testpaths, each a list of words; {} when none has them.
    A pyproject.toml without pytest's table, and a setup.cfg or tox.ini
    without its section, are passed over, as pytest passes them."""
    for name, section in PYTEST_CONFIGS:
        path = root / name
        if not path.is_file():
            continue
        if name == "pyproject.toml":
            table: object = tomllib.loads(path.read_text(encoding=_UTF8))
            for key in section.split("."):
                table = table.get(key) if isinstance(table, dict) else None
            if not isinstance(table, dict) or not table:
                continue
            return {k: _words(table[k]) for k in _OPTIONS if k in table}
        ini = configparser.ConfigParser(interpolation=None)
        ini.read(path, encoding=_UTF8)
        if not ini.has_section(section):
            continue
        return {k: ini.get(section, k).split() for k in _OPTIONS if ini.has_option(section, k)}
    return {}


def suite_layout(root: Path, tracked: list[str]) -> SuiteLayout:
    """ROOT's test layout: python_files from its pytest configuration (pytest's
    default without one), and as test directories each testpaths entry (a
    file's own directory) and every directory holding one of TRACKED's suites."""
    options = pytest_options(root)
    patterns = tuple(options.get("python_files", DEFAULT_PYTHON_FILES))
    dirs = {p.rstrip("/") if (root / p).is_dir() else p.rpartition("/")[0] for p in options.get("testpaths", [])}
    dirs |= {p.rpartition("/")[0] for p in tracked if _named_as_suite(p, patterns)}
    return SuiteLayout(patterns, frozenset(dirs))


def _named_as_suite(path: str, patterns: tuple[str, ...]) -> bool:
    name = path.rpartition("/")[2]
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def _in_test_dir(path: str, layout: SuiteLayout) -> bool:
    return any(d == "" or path.startswith(d + "/") for d in layout.dirs)


def files_in_test_dirs(tracked: list[str], layout: SuiteLayout) -> list[str]:
    """The Python files among TRACKED in LAYOUT's test directories: the ones
    readers() searches for who reads a shared file."""
    return [p for p in tracked if p.endswith(".py") and _in_test_dir(p, layout)]


def module(path: str) -> str | None:
    """The module mutmut keys a source file's functions by: jpcheck/lab_use.py
    is jpcheck.lab_use, a package's __init__.py the package, mutmut-gate.py
    mutmut-gate. None for a file that is not Python."""
    if not path.endswith(".py"):
        return None
    return path.removesuffix(".py").removesuffix("/__init__").replace("/", ".")


def shared_by_tests(path: str, layout: SuiteLayout) -> bool:
    """A file whose change can move the reach of tests that are not in it:
    a conftest.py anywhere; at the root, each of CONFIG_FILES; and in one of
    LAYOUT's test directories, at any depth, anything but a suite and the
    mutant specs (helpers, case tables, fixtures). A suite at the root makes
    every file a test directory's: slower, never wrong."""
    name = path.rpartition("/")[2]
    if name == "conftest.py" or path in CONFIG_FILES:
        return True
    if path.startswith("tests/mutants/") or _named_as_suite(path, layout.python_files):
        return False
    return _in_test_dir(path, layout)


def is_suite(path: str, layout: SuiteLayout) -> bool:
    """A file LAYOUT's python_files names, in one of its test directories."""
    return _named_as_suite(path, layout.python_files) and _in_test_dir(path, layout)


def readers(path: str, tests: dict[str, bytes], layout: SuiteLayout) -> set[str] | None:
    """The suites, among TESTS (each test file's text by its path), whose
    reach a change to the shared file PATH can move: a Python helper is read
    by the files that import at all and name its module as a word anywhere
    (every form of import, importlib's and a parenthesised one across lines
    too; one that only looks like one counts as well, and costs only a
    re-trace), a data file by those that
    name it (by its file name). None when the change can move any test's
    reach: a conftest.py or a configuration file, a file nothing names, or
    one a file that is not a suite names (a helper, so whoever imports that
    reads it too). Matched as bytes: a test file that is not valid UTF-8 is
    still searched."""
    name = path.rpartition("/")[2]
    if name == "conftest.py" or path in CONFIG_FILES:
        return None
    if name.endswith(".py"):
        stem = re.compile(rb"\b" + re.escape(name.removesuffix(".py")).encode() + rb"\b")
        naming = {p for p, text in tests.items() if b"import" in text and stem.search(text)}
    else:
        naming = {p for p, text in tests.items() if name.encode() in text}
    if not naming or not all(is_suite(p, layout) for p in naming):
        return None
    return naming
