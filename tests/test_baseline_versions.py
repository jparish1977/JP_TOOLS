#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_baseline_versions.py
The tool versions a baseline records: one for every tool, read from the copy
the gate runs.

A baseline records each tool's version so a count that changed because the
TOOL changed is not read as the code getting worse. A tool with no version
command records "", and "" on both sides compares, so an upgrade that adds a
rule reads as a regression. shellcheck, the PHP tools, PSScriptAnalyzer and
the audit tools had none. And a version read from a different copy than the
gate runs is a guess: eslint is loaded from node_modules, which is not on
PATH, so asking PATH found nothing.

Each tool is stood in for by a stub that prints a version, put where its
runner looks for it. No real tool is needed.

    python tests/test_baseline_versions.py
"""

import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest import mock

from _harness import check, check_true, run
from jpcheck import baseline
from jpcheck.registry import AUDIT_TOOLS, DEFAULT_TOOLS


def _stub(directory: Path, name: str, says: str) -> str:
    """An executable NAME in DIRECTORY that prints SAYS, as a tool's --version would."""
    path = directory / name
    path.write_text(f"#!{sys.executable}\nprint({says!r})\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_every_tool_has_a_version_source() -> None:
    """The next tool added to the registry cannot repeat shellcheck's gap:
    it needs a version command, a node package, or to be named as JP_TOOLS'
    own check."""
    tools = {t for table in (DEFAULT_TOOLS, AUDIT_TOOLS) for names in table.values() for t in names}
    sources = [set(baseline._VERSION_CMD), set(baseline._NODE_PACKAGES), baseline._INTERNAL_CHECKS]
    check("every registry tool has a version source",
          sorted(t for t in tools if not any(t in s for s in sources)), [])
    check("and no tool has two",
          sorted(t for t in tools if sum(t in s for s in sources) > 1), [])
    check("no source names a tool the registry does not run",
          sorted(set().union(*sources) - tools), [])


def test_path_tools_read_the_binary_on_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bindir = Path(tmp)
        _stub(bindir, "shellcheck", "ShellCheck - shell script analysis tool\nversion: 0.42.1")
        with mock.patch.dict(os.environ, {"PATH": str(bindir)}):
            check("shellcheck's version is recorded", baseline._tool_version("shellcheck"), "0.42.1")
            check("a tool not installed is '' (cannot tell)", baseline._tool_version("ruff"), "")


def test_php_tools_are_asked_through_php() -> None:
    """phpstan runs as `php vendor/bin/phpstan`; its version is asked the same way."""
    with tempfile.TemporaryDirectory() as tmp:
        php = _stub(Path(tmp), "php", "PHPStan - PHP Static Analysis Tool 2.71.3")
        with mock.patch.object(baseline, "_php_cmd", return_value=php), \
             mock.patch.object(baseline, "_php_bin", return_value="/vendor/bin/phpstan"):
            check("phpstan's version, via php", baseline._tool_version("phpstan"), "2.71.3")
        with mock.patch.object(baseline, "_php_cmd", return_value=None):
            check("no php is '' (cannot tell)", baseline._tool_version("phpstan"), "")


def _which(found: dict[str, str]) -> mock.Mock:
    """shutil.which as a box where only FOUND's names are installed."""
    return mock.Mock(side_effect=found.get)


def test_composer_is_asked_the_way_its_audit_runs() -> None:
    """composer-audit's version command is its audit command with
    `audit --format=json` replaced by --version, so each way run_composer_audit
    can invoke composer is pinned whole."""
    which = "jpcheck.baseline.shutil.which"
    with mock.patch.object(baseline, "_php_cmd", return_value="/bin/php"):
        with mock.patch(which, _which({"composer": "/bin/composer"})):
            check("composer on PATH: run directly", baseline._composer(), ["/bin/composer", "--version"])
        with mock.patch(which, _which({"composer.bat": "C:/composer.bat"})):
            check("only a composer.bat: through php", baseline._composer(),
                  ["/bin/php", "C:/composer.bat", "--version"])
        with mock.patch(which, _which({})):
            check("php only: php composer", baseline._composer(), ["/bin/php", "composer", "--version"])
    with mock.patch.object(baseline, "_php_cmd", return_value=None):
        with mock.patch(which, _which({"composer.bat": "C:/composer.bat"})):
            check("a composer.bat and no php: the .bat itself", baseline._composer(),
                  ["C:/composer.bat", "--version"])
        with mock.patch(which, _which({})):
            check("neither: no command", baseline._composer(), None)
    with tempfile.TemporaryDirectory() as tmp:
        _stub(Path(tmp), "composer", "Composer version 2.8.4 2024-12-11 11:57:47")
        with mock.patch.dict(os.environ, {"PATH": tmp}), \
             mock.patch.object(baseline, "_php_cmd", return_value=None):
            check("composer-audit's version, read from that run",
                  baseline._tool_version("composer-audit"), "2.8.4")


def test_prettier_is_the_node_modules_copy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        exe = _stub(Path(tmp), "prettier", "3.6.2")
        with mock.patch.object(baseline, "_node_bin", return_value=exe):
            check("prettier's version, from the copy _node_bin finds",
                  baseline._tool_version("prettier"), "3.6.2")
        with mock.patch.object(baseline, "_node_bin", return_value=None):
            check("no prettier is '' (cannot tell)", baseline._tool_version("prettier"), "")


def test_psscriptanalyzer_is_asked_through_pwsh() -> None:
    with mock.patch.object(baseline, "_pwsh", return_value="/bin/pwsh"):
        check("PSScriptAnalyzer: pwsh, no profile, the highest installed module",
              baseline._pssa(), ["/bin/pwsh", "-NoProfile", "-NonInteractive", "-Command",
                                 baseline._PSSA_VERSION])
    check_true("  which is the one Import-Module loads: sorted descending, first",
               "Sort-Object Version -Descending" in baseline._PSSA_VERSION
               and "Select-Object -First 1" in baseline._PSSA_VERSION)
    with tempfile.TemporaryDirectory() as tmp:
        pwsh = _stub(Path(tmp), "pwsh", "1.25.0")
        with mock.patch.object(baseline, "_pwsh", return_value=pwsh):
            check("its version, as pwsh prints it", baseline._tool_version("psscriptanalyzer"), "1.25.0")
    with mock.patch.object(baseline, "_pwsh", return_value=None):
        check("no pwsh is '' (cannot tell)", baseline._tool_version("psscriptanalyzer"), "")


def test_node_packages_read_the_copy_the_runner_loads() -> None:
    """eslint is imported from the first tool root's node_modules that has it,
    which is not on PATH: that copy's package.json is its version."""
    with tempfile.TemporaryDirectory() as tmp:
        first, second = Path(tmp) / "a" / "node_modules", Path(tmp) / "b" / "node_modules"
        (second / "eslint").mkdir(parents=True)
        (second / "eslint" / "package.json").write_text(json.dumps({"version": "8.1.0"}),
                                                        encoding="utf-8")
        (first / "eslint").mkdir(parents=True)
        (first / "eslint" / "package.json").write_text(json.dumps({"version": "9.30.1"}),
                                                       encoding="utf-8")
        with mock.patch.dict(os.environ, {"PATH": tmp}), \
             mock.patch.object(baseline, "node_module_roots", return_value=[first, second]):
            check("eslint's version is the first root's copy", baseline._tool_version("eslint"),
                  "9.30.1")
            (first / "eslint" / "package.json").write_text("{not json", encoding="utf-8")
            check("an unreadable package.json is '' (cannot tell)",
                  baseline._tool_version("eslint"), "")
            check("a package nowhere is ''", baseline._tool_version("stylelint"), "")


def test_internal_checks_record_no_version() -> None:
    check_true("JP_TOOLS' own checks record ''",
               all(baseline._tool_version(t) == "" for t in baseline._INTERNAL_CHECKS))


if __name__ == "__main__":
    sys.exit(run(globals(), "baseline versions"))
