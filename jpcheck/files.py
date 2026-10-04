"""Which files are checked: language detection, collection, and .jp-tools-ignore."""

import fnmatch
import os
from pathlib import Path
from typing import Any

from jpcheck.common import _VERDICTS, _rel, _verdict

_EXT_TO_LANG = {
    ".py":   "python",
    ".js":   "js",  ".ts":  "js",  ".jsx": "js",  ".tsx": "js",
    ".mjs":  "js",  ".cjs": "js",
    ".css":  "css", ".scss": "css", ".less": "css",
    ".html": "html", ".htm": "html",
    ".php":  "php",
    # C/C++ and Arduino. .ino is a C++ sketch; only the extension differs.
    ".c":    "cpp", ".h":   "cpp", ".cpp": "cpp",
    ".hpp":  "cpp", ".cc":  "cpp", ".ino": "cpp",
    # Shell, backed by shellcheck.
    ".sh":   "shell", ".bash": "shell",
}

# Interpreters shellcheck understands, as a #! line names them.
_SHELL_INTERPRETERS = {b"sh", b"bash", b"dash", b"ksh"}

# Directories that are not this project's source, skipped by every walk: the
# gate's file collection and the coverage-exemption scan alike. Virtualenvs
# under other names are caught by their pyvenv.cfg (is_project_file).
_SKIP_DIRS = {"node_modules", "vendor", "__pycache__", ".git", ".venv", "venv",
              "dist", "build", ".mypy_cache", ".ruff_cache", ".pytest_cache",
              ".tox", "site-packages"}


def source_paths(target: str, suffixes: tuple[str, ...]) -> list[str]:
    """TARGET as the paths to hand a tool that walks whatever it is given.

    A file is itself. A directory is the fewest paths that cover its files
    without entering a _SKIP_DIRS directory: whole where nothing beneath it is
    skipped, else split into its subdirectories and its own files ending in
    one of SUFFIXES. Handed the directory itself, phpstan, phpcs and rector
    analysed vendor/ as if it were the project's own code. A directory that
    cannot be listed raises OSError.
    """
    p = Path(target)
    return _cover(p, suffixes)[0] if p.is_dir() else [target]


def _cover(d: Path, suffixes: tuple[str, ...]) -> tuple[list[str], bool]:
    """(the paths covering D, whether D holds no skipped directory at any depth)."""
    parts: list[str] = []
    clean = True
    for e in sorted(d.iterdir()):
        if e.is_symlink():
            # Not followed, and not handed over: a link to a directory could
            # lead anywhere, vendor/ included, so its parent is split rather
            # than walked whole. A link to a file is measured at its target,
            # as in _collect_files.
            clean = clean and not e.is_dir()
            continue
        if e.is_dir():
            if e.name in _SKIP_DIRS:
                clean = False
                continue
            sub, sub_clean = _cover(e, suffixes)
            parts.extend(sub)
            clean = clean and sub_clean
        elif e.suffix.lower() in suffixes:
            parts.append(str(e))
    return ([str(d)] if clean else parts), clean


def _shebang_lang(p: Path) -> str | None:
    """The language a script's #! line names, for a file with NO suffix.

    Suffix-only detection would leave every extensionless script unchecked:
    the hook passes --skip-unsupported, so `bin/tool` would go through as
    "cannot detect language", while ruff and mypy both check such a file when
    it is named. Reads one line. Python and shell: `#!/bin/sh`, `#!/bin/bash`
    and `#!/usr/bin/env bash` alike, since `env` names the interpreter next.
    """
    if p.suffix:
        return None
    try:
        with p.open("rb") as fh:
            first = fh.readline(256)
    except OSError:
        # reason: a file that cannot be read has no shebang to read; the
        # language then comes from the suffix, and a linter reports the read
        return None
    return _interpreter_lang(first) if first.startswith(b"#!") else None


def _interpreter_lang(shebang: bytes) -> str | None:
    """"python" or "shell" for the interpreter a #! line names, else None."""
    if b"python" in shebang:
        return "python"
    words = shebang[2:].split()
    if words and words[0].endswith(b"/env"):
        words = [w for w in words[1:] if not w.startswith(b"-")]
    if words and words[0].rsplit(b"/", 1)[-1] in _SHELL_INTERPRETERS:
        return "shell"
    return None


def _detect_lang(target: str) -> str:
    p = Path(target)
    if p.is_file():
        return _EXT_TO_LANG.get(p.suffix.lower()) or _shebang_lang(p) or "unknown"
    return "unknown"


# Extensions that are not source and are uninteresting to report as skipped.
_UNREMARKABLE = {
    ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".lock",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf", ".zip", ".gz",
    ".csv", ".tsv", ".log", ".xml", ".sql", ".neon", ".db", "",
}


def _symlink_kind(link: Path, top: Path) -> str | None:
    """Which kind of symlink LINK is, as a skipped label; None for a plain file.

    A SYMLINKED FILE IS NOT COLLECTED. Findings are keyed by the resolved
    path, so a file reached through N links was measured N+1 times under one
    key, and its baseline came out N+1 times too loose. A link inside the tree
    loses nothing, since its target is collected directly. One pointing out of
    the tree is not this repo's file. Each kind is counted with the skipped
    extensions, so that it is reported.
    """
    if not link.is_symlink():
        return None
    try:
        target = link.resolve(strict=True)
    except (OSError, RuntimeError):
        return "(broken symlink)"     # reason: the string IS the verdict the report shows
    if target.is_relative_to(top):
        return "(symlink inside the tree, measured at its target)"
    return "(symlink out of the tree, not this repo's file)"


def _collect_files(directory: str) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Scan a directory, grouping files by language.

    Also returns a count of source-looking extensions that no tool covers.
    Silently omitting them is how this toolbox came to look inapplicable to an
    embedded project: pointed at a repo of one .py, one .ino and one .h, it
    reported on the .py and said nothing whatsoever about the other two, which
    reads as "the tool does nothing" rather than "the tool does not cover this".
    """
    groups: dict[str, list[str]] = {}
    skipped: dict[str, int] = {}
    top = Path(directory).resolve()
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            kind = _symlink_kind(Path(root) / f, top)
            if kind:
                skipped[kind] = skipped.get(kind, 0) + 1
                continue
            ext = Path(f).suffix.lower()
            lang = _EXT_TO_LANG.get(ext) or (
                _shebang_lang(Path(root) / f) if not ext else None)
            if lang:
                groups.setdefault(lang, []).append(str(Path(root) / f))
            elif ext not in _UNREMARKABLE:
                skipped[ext] = skipped.get(ext, 0) + 1
    return groups, skipped


# ── content this repo does not own ────────────────────────────────────────────
# Vendor code is not the repo's to fix. A repo lists such paths in a committed
# .jp-tools-ignore at its root, one entry per line:
#
#     oob/*.html   # byte-exact vendor pages kept as provenance
#
# The glob is matched with fnmatch against the path relative to the repo root,
# so `*` crosses directories. The reason after `#` is REQUIRED, the way a
# no-cover needs one, so a vendor copy and a file nobody wanted to fix cannot
# look alike. A skipped file is always reported, with the entry that skipped it.
# A line splits at its FIRST `#`, so a path containing `#` cannot be written as
# a glob: everything after that `#` is read as the reason.
_IGNORE_FILE = ".jp-tools-ignore"
Ignore = tuple[str, str]                  # glob, reason


class _IgnoreError(Exception):
    """A .jp-tools-ignore line check.py cannot use."""


def load_ignores(root: Path) -> list[Ignore]:
    """(glob, reason) for each entry in ROOT/.jp-tools-ignore; [] when there is none."""
    path = root / _IGNORE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # reason: no .jp-tools-ignore is no entries, as the docstring says
        return []
    out: list[Ignore] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        glob, _, reason = line.partition("#")
        if not glob.strip() or not reason.strip():
            raise _IgnoreError(f"{path}:{n}: '{line}' needs a glob and a reason after '#', "
                               "so a vendor copy and a file nobody wanted to fix cannot look alike")
        out.append((glob.strip(), reason.strip()))
    return out


def ignored_by(path: str, root: Path, ignores: list[Ignore]) -> Ignore | None:
    """The entry that ignores PATH, or None."""
    rel = _rel(path, root)
    return next(((g, r) for g, r in ignores if fnmatch.fnmatchcase(rel, g)), None)


def _drop_ignored(groups: dict[str, list[str]], root: Path,
                  ignores: list[Ignore]) -> list[dict[str, str]]:
    """Remove ignored files from GROUPS in place; return each one with its entry."""
    gone: list[dict[str, str]] = []
    for lang, files in groups.items():
        keep = []
        for f in files:
            hit = ignored_by(f, root, ignores)
            if hit:
                gone.append({"file": _rel(f, root), "pattern": hit[0], "reason": hit[1]})
            else:
                keep.append(f)
        groups[lang] = keep
    return gone


def _drop_ignored_issues(result: dict[str, Any], root: Path, ignores: list[Ignore]) -> None:
    """A dir-capable tool finds its own files, ignored ones included: drop their findings."""
    if not ignores:
        return
    issues = result.get("issues", [])
    kept = [i for i in issues if not ignored_by(i.get("file", ""), root, ignores)]
    if len(kept) != len(issues):
        result["issues"] = kept
        if result.get("status") in _VERDICTS:
            result["status"] = _verdict(kept)
