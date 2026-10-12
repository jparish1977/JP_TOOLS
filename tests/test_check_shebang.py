#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_shebang.py
check.py finds Python by its #! line in a file with no suffix.

THE GAP THIS PINS DOWN
    Detected by suffix alone, `bin/tool` with `#!/usr/bin/env python3` reads
    as "Cannot detect language" (exit 2), and the pre-commit hook, which passes
    --skip-unsupported, lets it through unchecked. ruff and mypy both check an
    extensionless file when it is named, so the gap is detection alone.

    Directory mode has the same hole one level down: ruff and mypy run once on
    the directory and discover files by suffix, so a detected script never
    reaches them unless it is named to them.

    A .sh or .bash suffix says only "a script": a Python program named
    convert.sh is Python by its #! line, so the Python rules run on it and
    shellcheck does not refuse it; one naming a shell, or none, stays shell.

Skips when ruff or mypy is missing, because then check.py cannot fail a file.

    python3 tests/test_check_shebang.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT

CHECK_PY = ROOT / "check.py"

DIRTY = "#!/usr/bin/env python3\nimport os\n"          # F401
CLEAN = "x = 1\n"

fails = 0
checks = 0


def check(what: str, ok: bool, detail: str = "") -> None:
    global fails, checks
    checks += 1
    if not ok:
        fails += 1
    print(f"  [{' ok ' if ok else 'FAIL'}] {what}")
    if not ok and detail:
        for line in detail.strip().splitlines()[:12]:
            print(f"         {line}")


def run(*args: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(CHECK_PY), *args],
                       capture_output=True, text=True, check=False)
    return r.returncode, r.stdout


def issues_for(out: str, tool: str) -> list[str]:
    try:
        doc = json.loads(out)
    except ValueError:
        # reason: not a check.py report: no issues listed, which fails the check that expects some
        return []
    return [str(i.get("file", "")) for c in doc.get("checks", [])
            if c.get("tool") == tool for i in c.get("issues", [])]


def language(out: str) -> str:
    try:
        return str(json.loads(out).get("language"))
    except ValueError:
        return "(no report)"   # reason: not a report, so no language; the check names what came out


def script_suffix(d: Path) -> None:
    """A .sh or .bash suffix says only "a script": a #! line naming Python
    wins, and the Python rules run; one naming a shell, or none, stays shell."""
    print("\nA script suffix, and a #! line:")
    fake = d / "convert.sh"
    fake.write_text(DIRTY, encoding="utf-8")
    rc, out = run(str(fake))
    check("a Python program named .sh is Python, and ruff finds its unused import, exit 1",
          (language(out), rc, any(issues_for(out, "ruff"))) == ("python", 1, True), out[:400])
    for name, body in (("real.sh", "#!/bin/sh\necho hi\n"), ("plain.sh", "echo hi\n"),
                       ("env.bash", "#!/usr/bin/env bash\necho hi\n")):
        (d / name).write_text(body, encoding="utf-8")
        check(f"CONTROL: {name} stays shell", language(run(str(d / name))[1]) == "shell")
    repo = d / "mixed"
    repo.mkdir()
    (repo / "ok.py").write_text(CLEAN, encoding="utf-8")
    (repo / "convert.sh").write_text(DIRTY, encoding="utf-8")
    rc, out = run(str(repo))
    check("directory mode: ruff's finding names the .sh Python program",
          any(f.endswith("convert.sh") for f in issues_for(out, "ruff")), out[:600])


def main() -> int:
    if not (shutil.which("ruff") and shutil.which("mypy")):
        print("SKIP: ruff and mypy are both needed for check.py to fail a file")
        return 0

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        tool = d / "tool"
        tool.write_text(DIRTY, encoding="utf-8")
        tool.chmod(0o755)

        print("One extensionless file:")
        rc, out = run(str(tool))
        check("a python-shebang script is detected and fails on its finding, exit 1",
              rc == 1, out[:400])
        check("ruff reported the unused import in it", any(issues_for(out, "ruff")), out[:400])

        shell = d / "hook"
        shell.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        rc, out = run(str(shell))
        # A sh script is shell, not unknown. Whether it then passes or exits
        # 2 depends on shellcheck being
        # installed; tests/test_check_shell.py covers both.
        check("a '#!/bin/sh' script is detected as shell, not unknown",
              "Cannot detect language" not in out and '"language": "shell"' in out, out[:300])

        plain = d / "Makefile"
        plain.write_text("all:\n\techo hi\n", encoding="utf-8")
        rc, out = run(str(plain))
        check("a suffix-less file with no shebang is unknown, exit 2", rc == 2, out[:300])

        print("\nDirectory mode:")
        repo = d / "repo"
        (repo / "bin").mkdir(parents=True)
        (repo / "ok.py").write_text(CLEAN, encoding="utf-8")
        (repo / "bin" / "tool").write_text(DIRTY, encoding="utf-8")
        (repo / "bin" / "tool").chmod(0o755)
        rc, out = run(str(repo))
        check("a dirty extensionless script in a clean tree fails the run, exit 1",
              rc == 1, out[:400])
        check("ruff's finding names the script, not only the .py files",
              any("bin/tool" in f for f in issues_for(out, "ruff")), out[:600])
        try:
            langs = {s["language"]: s["file_count"] for s in json.loads(out)["languages"]}
        except (ValueError, KeyError):
            langs = {}
        check("the python section counts the script as well as ok.py", langs.get("python") == 2,
              str(langs))

        script_suffix(d)

    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
