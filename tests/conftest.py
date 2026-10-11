"""pytest's view of the suites, for mutmut.

The suites run as `python tests/test_x.py` and report through check(), which
records a failure in FAILURES and returns. mutmut runs them under pytest,
where a recorded failure raises nothing, so every test would pass and every
mutant would read as survived. This fails the test a check() failure was
recorded in, and clears it, so the next test starts empty.
"""

from collections.abc import Iterator

import pytest

from _harness import FAILURES


@pytest.fixture(autouse=True)
def _a_failed_check_fails_the_test() -> Iterator[None]:
    before = len(FAILURES)
    yield
    recorded = FAILURES[before:]
    del FAILURES[before:]
    if recorded:
        pytest.fail("; ".join(recorded), pytrace=False)
