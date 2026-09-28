"""Helpers every runner shares: environment, status, tool lookup, config discovery."""

import configparser
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

# The JP_TOOLS checkout. configs/, vendor/ and node_modules/ sit beside
# check.py, one level above this package.
TOOLS_ROOT = Path(__file__).parent.parent

# Inject known tool locations that may not be on PATH yet (e.g. before reboot)
_EXTRA_PATHS = [
    Path(os.environ.get("APPDATA", "")) / "npm",           # Node global bins
    Path("C:/Program Files/nodejs"),
    Path("C:/Users") / os.environ.get("USERNAME", "") / "AppData/Local/Programs/PHP/8.3.30/nts/x64",
]
os.environ["PATH"] = os.pathsep.join(
    [str(p) for p in _EXTRA_PATHS if p.exists()] + [os.environ.get("PATH", "")]
)

# Force UTF-8 for subprocess output on Windows (avoids cp1252 decode errors)
os.environ["PYTHONUTF8"] = "1"


# Tools honour FORCE_COLOR/CLICOLOR_FORCE even when their output is captured,
# and ANSI escapes then break every line-oriented parser here: with
# FORCE_COLOR set, mypy emits "\x1b[1m\x1b[31merror:", the parser matches
# nothing, and a failing file reads as a pass.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\([A-Z]")


def _plain_env() -> dict[str, str]:
    """Environment with colour forcing removed."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("FORCE_COLOR", "CLICOLOR_FORCE", "MYPY_FORCE_COLOR")}
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


def strip_ansi(text: str) -> str:
    """Remove ANSI escapes. Belt and braces alongside _plain_env()."""
    return _ANSI.sub("", text)


def _php_bin(name: str) -> str | None:
    """Resolve a Composer bin, falling back to global PATH."""
    for ext in ("", ".bat", ".cmd"):
        local = TOOLS_ROOT / "vendor" / "bin" / f"{name}{ext}"
        if local.exists():
            return str(local)
    return shutil.which(name) or shutil.which(f"{name}.bat")


def _node_bin(name: str) -> str | None:
    """Resolve an npm bin from JP_TOOLS' own node_modules, falling back to PATH.

    package.json declares prettier, and `npm install` puts it in
    node_modules/.bin, which is not on PATH. Resolving through PATH alone would
    leave the manifest satisfied while the gate called the tool unavailable
    (METHODOLOGY, "Declare the instrument's own dependencies"). fix.py uses the
    same lookup, in the same order.
    """
    local_bin = TOOLS_ROOT / "node_modules" / ".bin"
    for ext in (".cmd", ""):
        local = local_bin / f"{name}{ext}"
        if local.exists():
            return str(local)
    return shutil.which(name) or shutil.which(f"{name}.cmd")


def _node_runner_failed(tool: str,
                        result: subprocess.CompletedProcess[str]) -> dict[str, Any] | None:
    """The result to report when a JP_TOOLS node runner did not lint, else None.

    jp_eslint.mjs and jp_stylelint.mjs exit 0 with a JSON array whenever they
    linted, lint findings included. Any other exit means they did not: node
    could not load eslint or stylelint (ERR_MODULE_NOT_FOUND, exit 1), or the
    runner refused (exit 2). stdout is then empty, and `result.stdout or "[]"`
    would read that as a clean pass.
    """
    if result.returncode == 0:
        return None
    lines = [ln.strip() for ln in strip_ansi(result.stderr).splitlines() if ln.strip()]
    detail = next((ln for ln in lines if "Error" in ln),
                  lines[0] if lines else f"exit {result.returncode}")
    return {"tool": tool, "status": "error", "issues": [],
            "note": f"runner exited {result.returncode}: {detail[:300]}"}


def _php_cmd() -> str | None:
    return shutil.which("php") or shutil.which("php.exe")


def _declares_section(path: Path, section: str) -> bool:
    """Whether a config file shared by several tools carries SECTION.

    SECTION is a dotted TOML table for a .toml file (`tool.ruff`) and an INI
    section name otherwise (`mypy`). A file that cannot be read or parsed
    counts as declaring it: the tool is then left to read the same file and
    report why it cannot, instead of the gate quietly running the defaults.
    """
    try:
        if path.suffix == ".toml":
            with path.open("rb") as fh:
                table: Any = tomllib.load(fh)
            for key in section.split("."):
                table = table.get(key) if isinstance(table, dict) else None
            return table is not None
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        return parser.has_section(section)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, configparser.Error):
        # reason: unreadable counts as the project's, so the tool reports the broken file
        return True


def _find_project_config(target: str, filenames: list[str],
                         sections: dict[str, str] | None = None,
                         stop_at_repo_root: bool = False) -> Path | None:
    """Walk up from target looking for a project-local config file.

    A file named in SECTIONS counts only if it carries that section, which is
    how the tools themselves read a shared file: ruff skips a pyproject.toml
    with no [tool.ruff] and mypy a setup.cfg with no [mypy]. Counting such a
    file would hand the tool no config at all and switch off every rule the
    JP_TOOLS defaults add.

    STOP_AT_REPO_ROOT ends the walk at the first directory holding .git or .hg,
    which is where mypy stops looking; ruff does not stop there. Walking past
    it for mypy would count a config above the repository that mypy never
    reads, and the gate would pass mypy no config at all.
    """
    sections = sections or {}
    p = Path(target).resolve()
    start = p if p.is_dir() else p.parent
    for directory in (start, *start.parents):
        hit = next((directory / name for name in filenames
                    if _is_config(directory / name, sections.get(name))), None)
        if hit:
            return hit
        if stop_at_repo_root and _is_repo_root(directory):
            break
    return None


def _is_config(candidate: Path, section: str | None) -> bool:
    """The file exists and, if it is shared by several tools, carries SECTION."""
    return candidate.exists() and (section is None or _declares_section(candidate, section))


def _is_repo_root(directory: Path) -> bool:
    """Where mypy stops walking up: a directory holding .git or .hg."""
    return (directory / ".git").exists() or (directory / ".hg").exists()


# ── helpers ───────────────────────────────────────────────────────────────────

def _status(issues: list[dict[str, Any]], returncode: int | None = None) -> str:
    if returncode is not None and returncode not in (0, 1):
        return "error"
    return "fail" if issues else "pass"


def _tool_missing(name: str) -> dict[str, Any]:
    return {
        "tool":   name,
        "status": "unavailable",
        "issues": [],
        "note":   f"'{name}' not found on PATH: install it to enable this check",
    }


# Statuses that mean a check ran and reached a verdict. "skip" is a runner
# deciding it does not apply (npm-audit with no package.json), not a failure.
_RAN = ("pass", "fail", "skip")


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # A hook exports GIT_DIR and GIT_INDEX_FILE relative to the committing
    # repo, which points at the wrong place for a `git -C` elsewhere.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                              text=True, check=False, env=env)
    except FileNotFoundError as e:
        raise _NoGitError("git") from e


class _NoGitError(Exception):
    """git itself is not on PATH, so no history can be read."""


def _repo_root(target: str) -> Path:
    """What baseline paths are relative to.

    Git toplevel where there is one, so a file is the same key no matter which
    directory the gate ran from. Keyed on absolute paths a baseline is useless
    on any other machine; keyed on cwd-relative ones it silently changes meaning
    when the hook runs from a subdirectory.
    """
    p = Path(target).resolve()
    start = p if p.is_dir() else p.parent
    # Through _git, which drops the hook's GIT_* variables. In a linked
    # worktree a hook runs with GIT_DIR set and no GIT_WORK_TREE, and git then
    # answers the current directory as the top, so a file in a subdirectory
    # would be keyed by its bare name and miss its baseline entry.
    try:
        r = _git(["rev-parse", "--show-toplevel"], start)
    except _NoGitError:
        return start      # reason: with no git there is no repo; the start directory is the root
    if r.returncode == 0 and r.stdout.strip():
        return Path(r.stdout.strip())
    return start


def _rel(path_str: str, root: Path) -> str:
    try:
        return Path(path_str).resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return Path(path_str).as_posix()
