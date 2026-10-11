"""pytest's view of the suites, for mutmut.

The suites run as `python tests/test_x.py` and report through check(), which
records a failure in FAILURES and returns. mutmut runs them under pytest,
where a recorded failure raises nothing, so every test would pass and every
mutant would read as survived. Two adaptations:

- A test that recorded a check() failure fails, and FAILURES is cleared so
  the next test starts empty.
- A suite with no test_* functions, which runs its cases from main(), is one
  test that calls main() in this process and fails unless it returns 0. In
  process, not as a subprocess: mutmut learns which tests reach which code by
  recording calls in the test process, and a subprocess's calls are lost.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from _harness import FAILURES


def _fail_on_recorded(before: int) -> None:
    recorded = FAILURES[before:]
    del FAILURES[before:]
    if recorded:
        pytest.fail("; ".join(recorded), pytrace=False)


@pytest.fixture(autouse=True)
def _a_failed_check_fails_the_test() -> Iterator[None]:
    before = len(FAILURES)
    yield
    _fail_on_recorded(before)


def pytest_configure(config: pytest.Config) -> None:
    """mutmut's stats pass marks itself MUTANT_UNDER_TEST=stats, in this
    process and in the environment every child inherits. A child that runs one
    of our scripts then records each call by loading mutmut's config from its
    own working directory, a scratch repo with none, and crashes. The child's
    calls are lost to mutmut anyway, so the marker is kept in this process
    only (mutmut holds its own copy) and children run unmutated. A mutant's
    own name, set for the runs that test it, is left for children to inherit:
    that is how a test driving a script as a subprocess kills a mutant."""
    if os.environ.get("MUTANT_UNDER_TEST") == "stats":
        del os.environ["MUTANT_UNDER_TEST"]


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    """A fresh directory per test, which test_check_exit_codes.py's own runner
    passes its tests as `tmp`."""
    return tmp_path


class _Main(pytest.Item):
    """The whole of a main()-driven suite, as one test."""

    def runtest(self) -> None:
        before = len(FAILURES)
        assert isinstance(self.parent, pytest.Module)
        rc = self.parent.obj.main()
        _fail_on_recorded(before)
        if rc:
            pytest.fail(f"main() returned {rc}", pytrace=False)

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, 0, f"{self.path.name}::main"


class _SuiteModule(pytest.Module):
    def collect(self) -> Any:
        items = list(super().collect())
        if items or not callable(getattr(self.obj, "main", None)):
            return items
        return [_Main.from_parent(self, name="main")]


def pytest_pycollect_makemodule(module_path: Path, parent: Any) -> pytest.Module:
    return _SuiteModule.from_parent(parent, path=module_path)
