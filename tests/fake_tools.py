"""Fake external tools on PATH, for suites that drive a script without the real tool.

A fake tool is a small Python script. It appends its argv, as one JSON line,
to the file named by FAKE_LOG (LOG_ARGV does that), then does just enough of
the real tool's job for the script under test to carry on.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

# The first lines of every fake: record the tool's name and arguments.
LOG_ARGV = """\
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + "\\n")
"""


def install(bin_dir: Path, name: str, body: str) -> None:
    tool = bin_dir / name
    tool.write_text(f"#!{sys.executable}\n{LOG_ARGV}{body}")
    tool.chmod(0o755)


@contextlib.contextmanager
def fake_tools(keep_path: bool = False, **tools: str) -> Iterator[tuple[Path, Path]]:
    """(work dir, log) with the named fake TOOLS on PATH.

    PATH is replaced, not extended, so a real tool on the box cannot answer
    in place of the fake one; KEEP_PATH puts the fakes in front of the real
    PATH instead, for a test that also needs real tools such as git. Pass
    names that are not identifiers, such as 7z, as `**{"7z": BODY}`.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bin_dir = root / "bin"
        work = root / "work"
        bin_dir.mkdir()
        work.mkdir()
        for name, body in tools.items():
            install(bin_dir, name, body)
        log = root / "log"
        log.touch()
        path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}" if keep_path else str(bin_dir)
        with env(PATH=path, FAKE_LOG=str(log)):
            yield work, log


@contextlib.contextmanager
def env(**values: str) -> Iterator[None]:
    """Set environment VALUES for the duration, then put back what was there."""
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def calls(log: Path) -> list[list[str]]:
    """Every recorded run: [tool name, *arguments]."""
    return [json.loads(line) for line in log.read_text().splitlines()]


def run_main(main: Callable[[list[str]], int], *args: str) -> tuple[int, str]:
    """(exit status, everything printed to stdout and stderr) for main(ARGS)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = main(list(args))
    return code, out.getvalue()
