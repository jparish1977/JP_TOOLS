#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_pins.py
check-pins.py: every gate tool declared in requirements-dev.txt, composer.json
and package.json is pinned to one version and installed at it.

Each arm against a fixture: a match is OK; a range, a missing package or
another installed version is FINDINGS; a missing vendor/ or node_modules/ is
NOT_RUN, never a pass; a composer dev- branch is printed as not checked.
Then JP_TOOLS' own three manifests, against installs made from them, so a
range put back into any of them fails here on every box.

    python3 tests/test_check_pins.py
"""

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, load_script, run
from jpexit import FINDINGS, NOT_RUN, OK

PINS = load_script("check-pins.py")


def tree(d: Path, requirements: str = "", require_dev: dict[str, str] | None = None,
         deps: dict[str, str] | None = None) -> None:
    (d / "requirements-dev.txt").write_text(requirements, encoding="utf-8")
    (d / "composer.json").write_text(json.dumps({"require-dev": require_dev or {}}), encoding="utf-8")
    (d / "package.json").write_text(json.dumps({"dependencies": deps or {}}), encoding="utf-8")


def vendor(d: Path, versions: dict[str, str]) -> None:
    """vendor/composer/installed.json as Composer 2 writes it."""
    (d / "vendor" / "composer").mkdir(parents=True)
    packages = [{"name": n, "version": v} for n, v in versions.items()]
    (d / "vendor" / "composer" / "installed.json").write_text(json.dumps({"packages": packages}), encoding="utf-8")


def node_modules(d: Path, versions: dict[str, str]) -> None:
    for name, version in versions.items():
        (d / "node_modules" / name).mkdir(parents=True)
        (d / "node_modules" / name / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")


def pins(root: Path, *arms: str, python: dict[str, str] | None = None,
         installs: Path | None = None) -> tuple[int, str]:
    """(exit, everything printed) for check-pins over ROOT's manifests."""
    out = io.StringIO()
    have = python or {}
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = PINS.main(list(arms), root=root, roots=(installs or root,), installed=have.get)
    return code, out.getvalue()


def test_python() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        tree(d, "# a comment\nruff==0.16.1  # trailing\n\nmypy==2.3.0\n")
        check("python, both at their pins: exit",
              pins(d, "python", python={"ruff": "0.16.1", "mypy": "2.3.0"})[0], OK)
        code, out = pins(d, "python", python={"ruff": "0.16.0", "mypy": "2.3.0"})
        check("python, another version installed: exit", code, FINDINGS)
        check_true("python, another version: names both", "ruff is pinned 0.16.1, installed 0.16.0" in out)
        code, out = pins(d, "python", python={"ruff": "0.16.1"})
        check("python, a pin not installed: exit", code, FINDINGS)
        check_true("python, not installed: says so", "mypy==2.3.0 is not installed" in out)
        for name, line in (("a range", "ruff>=0.16\n"), ("no version", "ruff\n"),
                           ("a wildcard", "ruff==0.16.*\n"), ("an option line", "-r other.txt\n"),
                           ("a version with no ==", "ruff 0.16.1\n")):
            tree(d, line)
            code, out = pins(d, "python", python={"ruff": "0.16.1"})
            check(f"python, {name}: exit", code, FINDINGS)
            check_true(f"python, {name}: not pinned", "is not pinned to one version" in out)


def test_php() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        tree(d, require_dev={"phpstan/phpstan": "2.2.8", "nikic/php-parser": "5.8.0",
                             "iteration8/core": "dev-master"})
        check("php, no vendor/: exit", pins(d, "php")[0], NOT_RUN)
        vendor(d, {"phpstan/phpstan": "2.2.8", "nikic/php-parser": "v5.8.0", "iteration8/core": "dev-master"})
        code, out = pins(d, "php")
        check("php, at their pins (a v prefix is the same version): exit", code, OK)
        check_true("php, a dev- branch is printed as not checked",
                   "NOT CHECKED iteration8/core dev-master" in out)
        tree(d, require_dev={"phpstan/phpstan": "^2.0"})
        code, out = pins(d, "php")
        check("php, a caret range: exit", code, FINDINGS)
        check_true("php, a caret range: not pinned", "phpstan/phpstan ^2.0 is not pinned" in out)
        for name, pin in (("a wildcard", "2.2.*"), ("an x wildcard", "2.2.x"), ("a range", ">=2.2")):
            tree(d, require_dev={"phpstan/phpstan": pin})
            code, out = pins(d, "php")
            check(f"php, {name}: exit", code, FINDINGS)
            check_true(f"php, {name}: not pinned", "is not pinned to one version" in out)
        tree(d, require_dev={"phpstan/phpstan": "2.2.7", "rector/rector": "2.6.2"})
        code, out = pins(d, "php")
        check("php, another version and a missing package: exit", code, FINDINGS)
        check_true("php, another version: names both", "phpstan/phpstan is pinned 2.2.7, installed 2.2.8" in out)
        check_true("php, missing: says so", "rector/rector 2.6.2 is not installed" in out)


def test_js() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        tree(d, deps={"eslint": "10.10.0", "@typescript-eslint/parser": "8.70.0"})
        check("js, no node_modules/: exit", pins(d, "js")[0], NOT_RUN)
        node_modules(d, {"eslint": "10.10.0", "@typescript-eslint/parser": "8.70.0"})
        check("js, at their pins (a scoped name too): exit", pins(d, "js")[0], OK)
        for name, pin in (("a caret range", "^10.0.0"), ("a tilde range", "~10.10.0"), ("latest", "latest"),
                          ("a v prefix npm does not write", "v10.10.0")):
            tree(d, deps={"eslint": pin})
            code, out = pins(d, "js")
            check(f"js, {name}: exit", code, FINDINGS)
            check_true(f"js, {name}: not pinned", "is not pinned to one version" in out)
        tree(d, deps={"eslint": "10.9.0", "prettier": "3.9.6"})
        code, out = pins(d, "js")
        check("js, another version and a missing package: exit", code, FINDINGS)
        check_true("js, another version: names both", "eslint is pinned 10.9.0, installed 10.10.0" in out)
        check_true("js, missing: says so", "prettier 3.9.6 is not installed" in out)


def test_arms_and_exit_precedence() -> None:
    """No arm named means every arm; one arm not run makes the whole run NOT_RUN,
    and its findings are still printed."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        tree(d, "ruff==1.0\n", require_dev={"phpstan/phpstan": "2.2.8"}, deps={"eslint": "10.10.0"})
        node_modules(d, {"eslint": "10.10.0"})
        code, out = pins(d, python={"ruff": "0.9"})
        check("every arm, php not installed, a python finding: exit", code, NOT_RUN)
        check_true("every arm: the python finding is printed", "ruff is pinned 1.0, installed 0.9" in out)
        check_true("every arm: php says it was not run", "php: NOT RUN" in out)
        check("an arm that does not exist: exit", pins(d, "ruby")[0], NOT_RUN)
        (d / "composer.json").write_text("{not json", encoding="utf-8")
        vendor(d, {})
        check("php, a composer.json that is not JSON: exit", pins(d, "php")[0], NOT_RUN)


def test_jp_tools_own_manifests_are_pinned() -> None:
    """JP_TOOLS' own manifests against installs made from their pins: OK only
    when every entry is one exact version, whatever this box has installed."""
    composer = json.loads((ROOT / "composer.json").read_text(encoding="utf-8"))["require-dev"]
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["dependencies"]
    requirements = {name: spec.removeprefix("==") for name, spec in PINS.python_pins(ROOT)}
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        vendor(d, composer)
        node_modules(d, package)
        code, out = pins(ROOT, python=requirements, installs=d)
        check("JP_TOOLS' own manifests, each pinned to one version: exit", code, OK)
        if code != OK:
            print(out)


if __name__ == "__main__":
    sys.exit(run(globals(), "check-pins"))
