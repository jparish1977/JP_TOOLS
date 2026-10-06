#!/usr/bin/env python3
"""Check that every tool JP_TOOLS' gate declares is pinned, and installed at its pin.

METHODOLOGY 7.9: declare the gate's tools in a manifest, pinned, then check
that every declared pin is actually installed at that version. One manifest
per arm, each read against where its own installer puts things:

    python  requirements-dev.txt, name==version, against the interpreter
            running this (CI's `python`; a box's tools venv:
            ~/.local/share/jp-tools-venv/bin/python check-pins.py python)
    php     composer.json require-dev, against vendor/composer/installed.json
    js      package.json dependencies, against node_modules/NAME/package.json
    powershell  requirements-system.txt's pwsh and pwsh:MODULE lines, against
            pwsh itself: pwsh at least its tested version, each module at
            exactly its pin

    python3 check-pins.py [python] [php] [js] [powershell]      (default: every arm)

vendor/ and node_modules/ are looked for here, then in the main checkout when
this is a worktree, as check.py looks for the tools themselves.

A finding is a pin that is not one exact version, a declared package that is
not installed, or one installed at another version. A composer `dev-`
constraint names a branch, not a version, so it cannot be pinned without a
lock: it is printed as not checked, and never counted as a pass.

Exit codes (jpexit): 0 OK, every pin checked and matched; 1 FINDINGS; 2
NOT_RUN, a manifest could not be read or an arm's install is absent
altogether (no vendor/, no node_modules/), so that arm was not looked at.
"""

import argparse
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jpexit
from jpcheck.common import tool_roots
from platformtools.procname import name_process

ROOT = Path(__file__).resolve().parent

# One exact version: digits and dots, an optional pre-release or build tail,
# and for composer an optional leading v. No operator, range or wildcard.
EXACT = re.compile(r"v?\d+(\.\d+)*([-+][0-9A-Za-z.-]+)?")
REQUIREMENT = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\S.*)?")
BRANCH_PREFIX = "dev-"


class NotRunError(Exception):
    """An arm could not be looked at; the message says why."""


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise NotRunError(f"cannot read {path}: {e}") from e


def _same(pin: str, installed: str) -> bool:
    """Composer writes versions with or without a leading v; the pin may too."""
    return pin.removeprefix("v") == installed.removeprefix("v")


def python_pins(root: Path) -> list[tuple[str, str]]:
    """(name, specifier) for each requirement in requirements-dev.txt."""
    path = root / "requirements-dev.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise NotRunError(f"cannot read {path}: {e}") from e
    pins = []
    for line in lines:
        text = line.split("#", 1)[0].strip()
        m = REQUIREMENT.fullmatch(text)
        if m:
            pins.append((m.group(1), (m.group(2) or "").replace(" ", "")))
        elif text:
            # An option line (-r, -e, --index-url) pins nothing this can check.
            pins.append((text, ""))
    return pins


def python_installed(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:  # reason: None is the answer "not installed", reported as a finding
        return None


def check_python(root: Path, installed: Callable[[str], str | None] = python_installed) -> list[str]:
    print(f"python: requirements-dev.txt against {sys.executable}")
    findings = []
    for name, spec in python_pins(root):
        version = spec.removeprefix("==")
        if not spec.startswith("==") or not EXACT.fullmatch(version):
            findings.append(f"python: {name} {spec or '(no version)'} is not pinned to one version (name==X.Y.Z)")
            continue
        have = installed(name)
        if have is None:
            findings.append(f"python: {name}=={version} is not installed")
        elif have != version:
            findings.append(f"python: {name} is pinned {version}, installed {have}")
    return findings


def _install_dir(name: str, roots: tuple[Path, ...]) -> Path:
    found = next((r / name for r in roots if (r / name).is_dir()), None)
    if found is None:
        raise NotRunError(f"no {name}/ in {', '.join(str(r) for r in roots)}: install it first")
    return found


def check_php(root: Path, roots: tuple[Path, ...]) -> list[str]:
    declared = _json(root / "composer.json").get("require-dev", {})
    vendor = _install_dir("vendor", roots)
    data = _json(vendor / "composer" / "installed.json")
    packages = data.get("packages", data) if isinstance(data, dict) else data
    have = {p["name"]: p["version"] for p in packages}
    print(f"php: composer.json require-dev against {vendor}")
    findings = []
    for name, pin in declared.items():
        if pin.startswith(BRANCH_PREFIX):
            print(f"php: NOT CHECKED {name} {pin}: a branch, not a version")
        elif not EXACT.fullmatch(pin):
            findings.append(f"php: {name} {pin} is not pinned to one version")
        elif name not in have:
            findings.append(f"php: {name} {pin} is not installed")
        elif not _same(pin, have[name]):
            findings.append(f"php: {name} is pinned {pin}, installed {have[name]}")
    return findings


def check_js(root: Path, roots: tuple[Path, ...]) -> list[str]:
    declared = _json(root / "package.json").get("dependencies", {})
    modules = _install_dir("node_modules", roots)
    print(f"js: package.json dependencies against {modules}")
    findings = []
    for name, pin in declared.items():
        if not EXACT.fullmatch(pin) or pin.startswith("v"):
            findings.append(f"js: {name} {pin} is not pinned to one version")
            continue
        manifest = modules / name / "package.json"
        if not manifest.is_file():
            findings.append(f"js: {name} {pin} is not installed")
            continue
        have = _json(manifest).get("version")
        if have != pin:
            findings.append(f"js: {name} is pinned {pin}, installed {have}")
    return findings


def system_pins(root: Path, prefix: str) -> list[tuple[str, str]]:
    """(TOOL, VERSION) for each requirements-system.txt line whose TOOL is PREFIX
    or starts with PREFIX:, in file order."""
    path = root / "requirements-system.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise NotRunError(f"cannot read {path}: {e}") from e
    out = []
    for line in lines:
        words = line.split("#", 1)[0].split()
        if len(words) >= 2 and (words[0] == prefix or words[0].startswith(prefix + ":")):
            out.append((words[0], words[1]))
    return out


# PowerShell's own version, then "NAME VERSION" for the highest installed
# version of each module named in JP_PINS_MODULES: the one Import-Module loads.
_PS_QUERY = (
    "$PSVersionTable.PSVersion.ToString(); "
    "foreach ($m in $env:JP_PINS_MODULES -split ',') { if ($m) { "
    "Get-Module -ListAvailable $m | Sort-Object Version -Descending | Select-Object -First 1 | "
    "ForEach-Object { $_.Name + ' ' + $_.Version.ToString() } } }"
)


def powershell_installed(modules: list[str]) -> tuple[str | None, dict[str, str]]:
    """(pwsh's version, {module: highest installed version}); (None, {}) without pwsh.
    A pwsh that is there and does not run (a snap one with no capabilities,
    say) raises NotRunError with what it said: it is not "not installed"."""
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        return None, {}
    r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", _PS_QUERY],
                       capture_output=True, text=True, check=False,
                       env={**os.environ, "JP_PINS_MODULES": ",".join(modules)})
    lines = r.stdout.splitlines()
    if r.returncode != 0 or not lines:
        said = (r.stderr.strip().splitlines() or ["no output"])[0]
        raise NotRunError(f"{pwsh} did not run (exit {r.returncode}): {said[:200]}")
    have = dict(ln.split(" ", 1) for ln in lines[1:] if " " in ln)
    return lines[0].strip(), have


def _at_least(have: str, least: str) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(p) for p in re.findall(r"\d+", v)[:3])
    return parts(have) >= parts(least)


def check_powershell(root: Path, query: Callable[[list[str]], tuple[str | None, dict[str, str]]]
                     = powershell_installed) -> list[str]:
    """pwsh at least requirements-system.txt's tested version (apt and snap
    ship one release, so it is a minimum), and each pwsh:MODULE at exactly its
    pin, the Gallery serving every release. The highest installed version is
    the one compared: it is the one Import-Module loads."""
    pins = system_pins(root, "pwsh")
    modules = {tool.split(":", 1)[1]: pin for tool, pin in pins if ":" in tool}
    least = next((pin for tool, pin in pins if tool == "pwsh"), None)
    version, have = query(list(modules))
    if version is None:
        raise NotRunError("pwsh is not installed (requirements-system.txt): install PowerShell 7")
    print(f"powershell: requirements-system.txt against pwsh {version}")
    findings = []
    if least and not _at_least(version, least):
        findings.append(f"powershell: pwsh {version} is older than the tested {least}")
    for name, pin in modules.items():
        if not EXACT.fullmatch(pin):
            findings.append(f"powershell: {name} {pin} is not pinned to one version")
        elif name not in have:
            findings.append(f"powershell: {name} {pin} is not installed")
        elif have[name] != pin:
            findings.append(f"powershell: {name} is pinned {pin}, installed {have[name]}")
    return findings


ARMS = ("python", "php", "js", "powershell")


class _Parser(argparse.ArgumentParser):
    """argparse, but a usage error is jpexit.NOT_RUN rather than argparse's own 2."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"check-pins: {message}", file=sys.stderr)
        raise SystemExit(jpexit.NOT_RUN)


def main(argv: list[str] | None = None, root: Path = ROOT, roots: tuple[Path, ...] | None = None,
         installed: Callable[[str], str | None] = python_installed,
         powershell: Callable[[list[str]], tuple[str | None, dict[str, str]]] = powershell_installed) -> int:
    name_process("check-pins")
    parser = _Parser(description="Check that the gate's tools are pinned, and installed at their pins.")
    parser.add_argument("arms", nargs="*", choices=ARMS, metavar="ARM",
                        help=f"which manifests to check: {', '.join(ARMS)} (default: all)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    install_roots = roots if roots is not None else tool_roots()
    checks: dict[str, Callable[[], list[str]]] = {
        "python": lambda: check_python(root, installed),
        "php": lambda: check_php(root, install_roots),
        "js": lambda: check_js(root, install_roots),
        "powershell": lambda: check_powershell(root, powershell),
    }
    findings, not_run = [], []
    for arm in args.arms or ARMS:
        try:
            findings += checks[arm]()
        except NotRunError as e:
            not_run.append(f"{arm}: NOT RUN: {e}")
    for line in findings + not_run:
        print(f"check-pins: {line}")
    if not_run:
        return jpexit.NOT_RUN
    if findings:
        return jpexit.FINDINGS
    print("check-pins: every pin checked is installed at its version")
    return jpexit.OK


if __name__ == "__main__":
    sys.exit(main())
