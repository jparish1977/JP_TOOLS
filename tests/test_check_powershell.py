#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_powershell.py
check.py's PowerShell arm, backed by PSScriptAnalyzer.

A .ps1, .psm1 or .psd1 file, or an extensionless one whose #! names pwsh, is
PowerShell. A Warning, Error or ParseError fails the file; Information is
reported and does not block. Nothing is excluded: a script that may use a
rule's construct says so with a SuppressMessageAttribute, and directive-reason
requires its Justification (PSScriptAnalyzer honours the attribute without one).

WHAT A MISSING TOOL MUST DO
    No pwsh, or no PSScriptAnalyzer module, is a check that did not run
    (exit 2), naming what to install, never a pass. Those cases, and how the
    report is read, run everywhere against a fake pwsh. The real-tool cases
    need pwsh and the module, and print SKIP without them; a pwsh that is
    installed but crashes fails here instead. Each pwsh run gets a cache
    directory of its own, so a corrupted shared startup profile cannot happen.

    python3 tests/test_check_powershell.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from _harness import ROOT, check, check_true, run
from fake_tools import install
from jpcheck.files import _detect_lang
from jpcheck.native_tools import _NO_MODULE, _NO_TARGET, run_psscriptanalyzer

CHECK_PY = ROOT / "check.py"
BARE = "Write-Host 'hi'\n"
JUSTIFIED = ("[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', "
             "Justification = 'prints progress for a person')]\nparam()\nWrite-Host 'hi'\n")
UNJUSTIFIED = ("[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '')]\n"
               "param()\nWrite-Host 'hi'\n")


def check_py(*args: str, env: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    r = subprocess.run([sys.executable, str(CHECK_PY), *args], capture_output=True,
                       text=True, check=False, env=env)
    try:
        return r.returncode, json.loads(r.stdout)
    except ValueError:
        return r.returncode, {"unparsed": r.stdout + r.stderr}  # reason: the check then names what came out


def checks(doc: dict[str, Any]) -> list[tuple[str, str, list[str]]]:
    return [(str(c["tool"]), str(c["status"]), [str(i["rule"]) for i in c["issues"]])
            for c in doc.get("checks", [])]


def test_detection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for name, body in (("a.ps1", BARE), ("m.psm1", BARE), ("m.psd1", "@{}\n"),
                           ("tool", "#!/usr/bin/env pwsh\nWrite-Output 1\n")):
            (d / name).write_text(body, encoding="utf-8")
            check(f"{name} is powershell", _detect_lang(str(d / name)), "powershell")


def test_a_missing_pwsh_did_not_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "a.ps1"
        f.write_text(BARE, encoding="utf-8")
        code, doc = check_py(str(f), "--tools", "psscriptanalyzer", env={"PATH": tmp})
    check("no pwsh: exit 2, psscriptanalyzer unavailable, naming pwsh",
          (code, [(t, s) for t, s, _ in checks(doc)], "pwsh" in json.dumps(doc)),
          (2, [("psscriptanalyzer", "unavailable")], True))


def fake_run(body: str) -> dict[str, Any]:
    """run_psscriptanalyzer against a fake pwsh whose body is BODY."""
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = Path(tmp)
        install(bin_dir, "pwsh", body)
        f = bin_dir / "a.ps1"
        f.write_text(BARE, encoding="utf-8")
        old = os.environ.copy()
        os.environ.update(PATH=str(bin_dir), FAKE_LOG=str(bin_dir / "log"))
        try:
            return run_psscriptanalyzer(str(f))
        finally:
            os.environ.clear()
            os.environ.update(old)


def test_how_the_report_is_read() -> None:
    r = fake_run(f"sys.exit({_NO_MODULE})\n")
    check("no module: unavailable, naming the Install-Module command",
          (r["status"], "Install-Module PSScriptAnalyzer" in str(r.get("note"))), ("unavailable", True))
    r = fake_run(f"sys.exit({_NO_TARGET})\n")
    check("a target pwsh cannot see: error, never a pass",
          (r["status"], "nothing was analysed" in str(r.get("note"))), ("error", True))
    found = [{"line": 3, "col": 1, "severity": "Warning", "rule": "PSAvoidUsingEmptyCatchBlock",
              "message": "m"},
             {"line": 5, "col": 2, "severity": "Information", "rule": "PSUseSingularNouns",
              "message": "m"},
             {"line": 7, "col": 3, "severity": "ParseError", "rule": "Parse", "message": "m"}]
    r = fake_run(f"print({json.dumps(json.dumps(found))})\n")
    check("findings: Warning and ParseError block, Information warns",
          (r["status"], [(i["rule"], i["severity"], i["line"]) for i in r["issues"]]),
          ("fail", [("PSAvoidUsingEmptyCatchBlock", "error", 3), ("PSUseSingularNouns", "warning", 5),
                    ("Parse", "error", 7)]))
    check("an empty report: pass", fake_run("print('[]')\n")["status"], "pass")
    r = fake_run("print('not json'); sys.exit(1)\n")
    check("a report that is not JSON: error, never a pass", r["status"], "error")


def test_pwsh_gets_a_cache_of_its_own() -> None:
    """Each pwsh run has a cache directory of its own, removed afterwards, so
    runs at once, or one killed mid-write, cannot corrupt the user's shared
    startup profile, which makes every pwsh after it crash."""
    with tempfile.TemporaryDirectory() as seen_dir:
        seen = Path(seen_dir) / "seen"
        os.environ["JP_TEST_SEEN"] = str(seen)
        try:
            r = fake_run("import os\nwith open(os.environ['JP_TEST_SEEN'], 'w') as fh:\n"
                         "    fh.write(os.environ.get('XDG_CACHE_HOME', ''))\nprint('[]')\n")
        finally:
            del os.environ["JP_TEST_SEEN"]
        cache = seen.read_text(encoding="utf-8") if seen.exists() else ""
    users = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    check("pwsh's cache: a directory of its own, not the user's, gone after the run",
          (r["status"], cache not in ("", users), bool(cache) and os.path.exists(cache)),
          ("pass", True, False))


def real_tool() -> tuple[str | None, str | None]:
    """(why the real-tool cases cannot run here, why pwsh is broken). Not
    installed is a skip; a pwsh that is installed but cannot run is a failure,
    never a skip reading as "the module is not installed"."""
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        return "pwsh is not installed", None
    with tempfile.TemporaryDirectory() as cache:
        r = subprocess.run([pwsh, "-NoProfile", "-Command", "Get-Module -ListAvailable PSScriptAnalyzer"],
                           capture_output=True, text=True, check=False,
                           env=dict(os.environ, XDG_CACHE_HOME=cache))
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["no output"])[0]
        return None, f"pwsh exit {r.returncode}: {tail[:200]}"
    return (None if "PSScriptAnalyzer" in r.stdout else "the PSScriptAnalyzer module is not installed"), None


def test_a_crashing_pwsh_is_not_a_skip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        install(Path(tmp), "pwsh", "sys.stderr.write('Unhandled exception.\\n'); sys.exit(134)\n")
        old = os.environ.copy()
        os.environ.update(PATH=tmp, FAKE_LOG=str(Path(tmp) / "log"))
        try:
            got = real_tool()
        finally:
            os.environ.clear()
            os.environ.update(old)
    check("a pwsh that crashes: broken, not skipped", got,
          (None, "pwsh exit 134: Unhandled exception."))


def test_the_real_tool() -> None:
    why, broken = real_tool()
    check("the real tool: an installed pwsh runs", broken, None)
    if why or broken:
        if why:
            print(f"SKIP: the real-tool cases: {why}")
        return
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        results = {}
        for name, body in (("bare.ps1", BARE), ("justified.ps1", JUSTIFIED), ("unjustified.ps1", UNJUSTIFIED),
                           ("bare[1].ps1", BARE)):
            (d / name).write_text(body, encoding="utf-8")
            code, doc = check_py(str(d / name))
            results[name] = (code, checks(doc))
        gone = run_psscriptanalyzer(str(d / "not-there.ps1"))["status"]
    check("the real tool, a file that is not there: error", gone, "error")
    check("a bare Write-Host: exit 1, PSAvoidUsingWriteHost",
          results["bare.ps1"], (1, [("psscriptanalyzer", "fail", ["PSAvoidUsingWriteHost"]),
                                    ("directive-reason", "pass", [])]))
    check("a name with wildcard characters is analysed, not matched as a pattern: exit 1",
          results["bare[1].ps1"], results["bare.ps1"])
    check("suppressed with a Justification: exit 0, clean",
          results["justified.ps1"], (0, [("psscriptanalyzer", "pass", []), ("directive-reason", "pass", [])]))
    check_true("suppressed with no Justification: PSScriptAnalyzer passes it, directive-reason refuses it",
               results["unjustified.ps1"] == (1, [("psscriptanalyzer", "pass", []),
                                                  ("directive-reason", "fail", ["directive-no-reason"])]))


if __name__ == "__main__":
    sys.exit(run(globals(), "check.py powershell"))
