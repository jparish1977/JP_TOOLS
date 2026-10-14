#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_install_pwsh.py
install-pwsh.sh installs PowerShell 7 and PSScriptAnalyzer at the versions
requirements-system.txt names, without restating them.

WHAT IS CHECKED, against a fake pwsh and a fake curl:
    - pwsh already on PATH: nothing is downloaded, and PSScriptAnalyzer is
      installed at the manifest's pin.
    - no pwsh: exactly the manifest's release is fetched (its URL, linux x64),
      checked against the manifest's sha256, unpacked into ~/.local/pwsh, put
      on GITHUB_PATH, and the module installed with it.
    - a download whose sha256 is not the manifest's: exit 1, nothing unpacked,
      the module not installed.
    - the install never changes the Gallery's trust setting.
    - the pinned module already installed: no install, so no Gallery; only
      another version installed: the pin is installed.
    - a manifest without the lines: the script stops, naming the missing one.

    python3 tests/test_install_pwsh.py
"""

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, run
from fake_tools import LOG_ARGV, calls, fake_tools
from process_check import check_exit

SCRIPT = ROOT / "install-pwsh.sh"
MANIFEST = ("shellcheck 0.9.0 shellcheck koalaman.shellcheck\n"
            "pwsh 7.6.5 powershell Microsoft.PowerShell\n"
            "pwsh:PSScriptAnalyzer 1.25.0 - -\n")
FAKE_PWSH = "print('7.6.5')\n"


def jp_tools(where: Path, manifest: str) -> Path:
    """A copy of the script beside a requirements-system.txt holding MANIFEST."""
    where.mkdir()
    shutil.copy(SCRIPT, where / "install-pwsh.sh")
    (where / "requirements-system.txt").write_text(manifest, encoding="utf-8")
    return where / "install-pwsh.sh"


def tarball_with_pwsh() -> bytes:
    """A release tarball whose pwsh is a fake that logs its arguments."""
    body = f"#!{sys.executable}\n{LOG_ARGV}{FAKE_PWSH}".encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("pwsh")
        info.size = len(body)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def test_pwsh_already_there() -> None:
    with fake_tools(keep_path=True, pwsh=FAKE_PWSH, curl="sys.exit(9)\n") as (work, log):
        script = jp_tools(Path(work) / "jp", MANIFEST)
        r = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=False,
                           env={**os.environ, "HOME": str(work)})
        ran = calls(log)
    check_exit("pwsh present: exit", r, 0)
    check("pwsh present: nothing downloaded", [c for c in ran if c[0] == "curl"], [])
    check_true("pwsh present: the module installed at the manifest's pin",
               any("Install-Module PSScriptAnalyzer -RequiredVersion 1.25.0" in " ".join(c) for c in ran))
    check("pwsh present: the Gallery's trust setting is not touched",
          [c for c in ran if "Set-PSRepository" in " ".join(c)], [])


def fetch_without_pwsh(tmp: str, sha256: str | None = None) -> tuple[subprocess.CompletedProcess[str],
                                                                        list[list[str]], Path, Path]:
    """install-pwsh.sh with no pwsh on PATH and only curl faked, serving a
    tarball whose pwsh is a fake; the manifest's pwsh line carries SHA256,
    the served tarball's own by default. (result, calls, ~/.local/pwsh, GITHUB_PATH)."""
    release = tarball_with_pwsh()
    blob = Path(tmp) / "release.tar.gz"
    blob.write_bytes(release)
    sha256 = sha256 or hashlib.sha256(release).hexdigest()
    manifest = MANIFEST.replace("Microsoft.PowerShell\n", f"Microsoft.PowerShell sha256:{sha256}\n")
    curl = ("out = sys.argv[sys.argv.index('-o') + 1]\n"
            f"open(out, 'wb').write(open({str(blob)!r}, 'rb').read())\n")
    # Only curl is faked: the real tar, sha256sum, mkdir, rm and chmod do the
    # work, from /usr/bin and /bin, and no real pwsh is on that PATH.
    bin_dir = Path(tmp) / "bin"
    bin_dir.mkdir()
    log = Path(tmp) / "log"
    log.touch()
    (bin_dir / "curl").write_text(f"#!{sys.executable}\n{LOG_ARGV}{curl}")
    (bin_dir / "curl").chmod(0o755)
    home = Path(tmp) / "home"
    home.mkdir()
    gh_path = Path(tmp) / "github_path"
    script = jp_tools(Path(tmp) / "jp", manifest)
    path = f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=False,
                       env={"PATH": path, "HOME": str(home), "FAKE_LOG": str(log),
                            "GITHUB_PATH": str(gh_path)})
    return r, calls(log), home / ".local" / "pwsh", gh_path


def test_a_download_that_does_not_match_its_hash() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        r, ran, dest, _ = fetch_without_pwsh(tmp, sha256="0" * 64)
        check_exit("hash mismatch: exit 1, says so, nothing unpacked or left, no module install", r, 1,
                   says=["does not match the sha256"])
        check("  nothing unpacked or left, no module install",
              (sorted(p.name for p in dest.iterdir()), [c for c in ran if c[0] == "pwsh"]), ([], []))


def test_no_pwsh_fetches_the_pinned_release() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        r, ran, dest, gh_path = fetch_without_pwsh(tmp)
        urls = [c[c.index("-fsSL") + 1] for c in ran if c[0] == "curl"]
        check("no pwsh: exit", (r.returncode, r.stderr[-300:]), (0, ""))
        check("no pwsh: the manifest's release, linux x64",
              urls, [("https://github.com/PowerShell/PowerShell/releases/download/v7.6.5/"
                      "powershell-7.6.5-linux-x64.tar.gz")])
        check_true("no pwsh: unpacked into ~/.local/pwsh, the tarball removed",
                   (dest / "pwsh").is_file() and not list(dest.glob("*.tar.gz")))
        check("no pwsh: put on GITHUB_PATH for the next step",
              gh_path.read_text(encoding="utf-8").split() if gh_path.exists() else [], [str(dest)])
        check_true("no pwsh: the module installed with the new pwsh",
                   any(c[0] == "pwsh" and "-RequiredVersion 1.25.0" in " ".join(c) for c in ran))


def test_a_failed_module_install_fails_the_script() -> None:
    """pwsh -Command exits 0 after a non-terminating error, so the install asks
    for 'Stop'; a pwsh that then exits 1 stops the script there. Invariant
    globalization is not set: PowerShellGet will not load under it."""
    failing = ("if any('Install-Module' in a for a in sys.argv):\n"
               "    import os\n"
               "    sys.exit(1 if 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT' not in os.environ else 7)\n"
               + FAKE_PWSH)
    with fake_tools(keep_path=True, pwsh=failing) as (work, log):
        script = jp_tools(Path(work) / "jp", MANIFEST)
        env = {k: v for k, v in os.environ.items() if k != "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"}
        r = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=False,
                           env={**env, "HOME": str(work)})
        ran = calls(log)
    installs = [" ".join(c) for c in ran if "Install-Module" in " ".join(c)]
    # Two pwsh calls ran: the installed-modules query, then the failed
    # install. The $PSVersionTable query after it never ran.
    check_exit("install fails: exit 1, the version query never run", r, 1)
    check("  two pwsh calls, the version query not among them", len(ran), 2)
    check_true("install fails: the command stops on any error",
               bool(installs) and "$ErrorActionPreference = 'Stop';" in installs[0])


def module_installed(versions: str) -> list[str]:
    """install-pwsh.sh with pwsh present and VERSIONS of PSScriptAnalyzer
    installed (one per line); the Install-Module commands it ran."""
    pwsh = ("if any('Get-Module' in a for a in sys.argv):\n"
            f"    print({versions!r})\n"
            "    sys.exit(0)\n" + FAKE_PWSH)
    with fake_tools(keep_path=True, pwsh=pwsh, curl="sys.exit(9)\n") as (work, log):
        script = jp_tools(Path(work) / "jp", MANIFEST)
        r = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=False,
                           env={**os.environ, "HOME": str(work)})
        ran = calls(log)
    check_exit(f"module {versions!r} installed: exit", r, 0)
    return [" ".join(c) for c in ran if "Install-Module" in " ".join(c)]


def test_the_pinned_module_already_installed() -> None:
    check("the pinned module already installed: no Install-Module, no Gallery",
          module_installed("1.24.0\n1.25.0"), [])
    check("another version installed: the pin is installed",
          len(module_installed("1.24.0")), 1)


def test_a_manifest_without_the_lines() -> None:
    with fake_tools(keep_path=True, pwsh=FAKE_PWSH) as (work, log):
        script = jp_tools(Path(work) / "jp", "shellcheck 0.9.0 shellcheck -\n")
        r = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=False,
                           env={**os.environ, "HOME": str(work)})
        ran = calls(log)
    check_exit("no pwsh line: exit 1, naming it, and nothing run", r, 1, says=["no 'pwsh' line"])
    check("  nothing run", ran, [])


if __name__ == "__main__":
    sys.exit(run(globals(), "install-pwsh"))
