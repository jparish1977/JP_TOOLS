"""Whether a pyproject.toml change can move what mutmut's tests judge.

mutmut-gate.py counts a change to a file every test reads as a change to
every test: all reach is traced again and every verdict judged again. For
pyproject.toml that is right only for the parts the tests and mutmut run
under: pytest's own tables ([tool.pytest*]: what is collected, how it runs),
mutmut's table ([tool.mutmut]: arguments, timeouts, what is copied) and
[build-system]. A version, a description or a dependency list moves no test.
Nor does [tool.mutmut]'s source_paths: a path added only adds mutants, all of
them new and so tested anyway, and one taken away only removes some.
"""

import tomllib

# [tool.mutmut]'s one key that moves no test's verdict.
UNMOVING = "source_paths"


def test_config(text: str) -> dict[str, object]:
    """The parts of the pyproject.toml TEXT the tests and mutmut run under."""
    doc = tomllib.loads(text)
    tool = doc.get("tool", {})
    return {"pytest": {k: v for k, v in tool.items() if k.startswith("pytest")},
            "mutmut": {k: v for k, v in tool.get("mutmut", {}).items() if k != UNMOVING},
            "build-system": doc.get("build-system")}


def moves_tests(old: str | None, new: str) -> bool:
    """Whether the change from pyproject.toml OLD (None: there was none) to NEW
    can move what the tests judge; a file that does not parse can."""
    if old is None:
        return True
    try:
        return test_config(old) != test_config(new)
    except tomllib.TOMLDecodeError:  # reason: a file that does not parse could say anything; every test counts
        return True
