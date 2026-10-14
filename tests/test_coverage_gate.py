#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_coverage_gate.py
coverage-gate.py: a Clover report's line coverage against a threshold.

Under the threshold is FINDINGS; a report that cannot be read, or that counts
no statements, is NOT_RUN and never a pass. Then a REAL Clover report, from
PHPUnit with a coverage driver over a two-method class whose test calls one,
read at a threshold under it and one over it. SKIP, said, without php, this
checkout's vendor/bin/phpunit, or pcov/xdebug.

    python3 tests/test_coverage_gate.py
"""

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, load_script, run
from jpcheck.common import tool_roots
from jpexit import FINDINGS, NOT_RUN, OK
from process_check import check_exit

GATE = load_script("coverage-gate.py")


def clover(statements: object, covered: object) -> str:
    """A Clover report shaped like PHPUnit's: per-file metrics, then the project's."""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<coverage generated="0"><project timestamp="0">'
            '<file name="/src/A.php"><metrics statements="7" coveredstatements="0"/></file>'
            f'<metrics files="1" statements="{statements}" coveredstatements="{covered}"/>'
            '</project></coverage>\n')


def gate(text: str | None, *args: str) -> tuple[int, str]:
    """(exit, everything printed) for the gate over a report holding TEXT, or a missing one."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "clover.xml"
        if text is not None:
            report.write_text(text, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = GATE.main([str(report), *args])
        return code, out.getvalue()


def test_the_verdict() -> None:
    """The project's metrics are read, not a file's (A.php is at 0%)."""
    code, out = gate(clover(100, 96))
    check("96% at the default 95: exit", code, OK)
    check_true("96%: says the numbers", "96.00%" in out and "96 of 100" in out)
    check("exactly 95% at 95: exit", gate(clover(100, 95))[0], OK)
    check("90% at 95: exit", gate(clover(100, 90))[0], FINDINGS)
    check("90% at --fail-under 90: exit", gate(clover(100, 90), "--fail-under", "90")[0], OK)


def test_a_report_that_cannot_be_read_is_not_run() -> None:
    """An unreadable report, or one counting nothing, is never a pass."""
    for name, text in (("missing", None), ("not XML", "not xml\n"),
                       ("no project metrics", '<coverage><project timestamp="0"/></coverage>\n'),
                       ("statements not a number", clover("many", 3))):
        code, out = gate(text)
        check(f"{name}: exit", code, NOT_RUN)
        check_true(f"{name}: says so", "coverage-gate:" in out)
    code, out = gate(clover(0, 0))
    check("zero statements: exit", code, NOT_RUN)
    check_true("zero statements: refuses to call it covered", "no statements" in out)
    for name, statements, covered in (("more covered than counted", 10, 12),
                                      ("a negative covered count", 10, -1)):
        code, out = gate(clover(statements, covered))
        check(f"{name}: exit", code, NOT_RUN)
        check_true(f"{name}: says so", "not a coverage" in out)


def test_a_threshold_outside_0_to_100_is_refused() -> None:
    for bad in ("101", "-1", "lots"):
        code, _ = gate(clover(100, 96), "--fail-under", bad)
        check(f"--fail-under {bad}: exit", code, NOT_RUN)


PHP_CLASS = """<?php
declare(strict_types=1);
final class Pair
{
    public function first(int $a): int
    {
        $b = $a + 1;
        return $b * 2;
    }

    public function second(int $a): int
    {
        $b = $a - 1;
        return $b * 3;
    }
}
"""

PHP_TEST = """<?php
declare(strict_types=1);
use PHPUnit\\Framework\\TestCase;
require __DIR__ . '/../src/Pair.php';
final class PairTest extends TestCase
{
    public function testFirst(): void
    {
        self::assertSame(6, (new Pair())->first(2));
    }
}
"""

PHPUNIT_XML = """<?xml version="1.0"?>
<phpunit bootstrap="BOOT" colors="false">
  <testsuites><testsuite name="t"><directory>tests</directory></testsuite></testsuites>
  <source><include><directory>src</directory></include></source>
</phpunit>
"""


def _vendor() -> Path | None:
    """The vendor/ holding PHPUnit, found as check.py finds its PHP tools: this
    checkout first, then the main one when this is a worktree."""
    return next((root / "vendor" for root in tool_roots()
                 if (root / "vendor" / "bin" / "phpunit").exists()), None)


def _no_real_phpunit(vendor: Path | None) -> str | None:
    """Why the real-report case cannot run here, or None when it can."""
    if shutil.which("php") is None or vendor is None:
        return "php, or vendor/bin/phpunit in any JP_TOOLS checkout, is missing (composer install)"
    drivers = subprocess.run(["php", "-r", 'echo extension_loaded("pcov") || extension_loaded("xdebug") ? 1 : 0;'],
                             capture_output=True, text=True, check=False).stdout
    return None if drivers == "1" else "no PHP coverage driver (pcov or xdebug)"


def test_a_real_phpunit_clover_report() -> None:
    vendor = _vendor()
    why = _no_real_phpunit(vendor)
    if why or vendor is None:
        print(f"SKIP: the real-report case: {why}")
        return
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "src").mkdir()
        (d / "tests").mkdir()
        (d / "src" / "Pair.php").write_text(PHP_CLASS, encoding="utf-8")
        (d / "tests" / "PairTest.php").write_text(PHP_TEST, encoding="utf-8")
        boot = vendor / "autoload.php"
        (d / "phpunit.xml").write_text(PHPUNIT_XML.replace("BOOT", str(boot)), encoding="utf-8")
        report = d / "clover.xml"
        made = subprocess.run(["php", "-d", "xdebug.mode=coverage", str(vendor / "bin" / "phpunit"),
                               "--coverage-clover", str(report)],
                              cwd=d, capture_output=True, text=True, check=False)
        check_exit("phpunit wrote a Clover report", made, 0)
        check_true("  the report there", report.exists())
        if not report.exists():
            return
        code, out = gate(report.read_text(encoding="utf-8"), "--fail-under", "95")
        check("real report, half covered, at 95: exit", code, FINDINGS)
        check_true("real report: half the lines", "50.00%" in out)
        check("real report at 50: exit", gate(report.read_text(encoding="utf-8"), "--fail-under", "50")[0], OK)


if __name__ == "__main__":
    sys.exit(run(globals(), "coverage gate"))
