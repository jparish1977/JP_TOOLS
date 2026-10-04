"""Running the tools over one target: a single file, or a directory grouped by language."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from jpcheck.common import _RAN, _VERDICTS, _worse
from jpcheck.files import Ignore, _collect_files, _drop_ignored, _drop_ignored_issues
from jpcheck.lab import _mark_lab_exempt
from jpcheck.registry import (
    _DIR_CAPABLE,
    AUDIT_TOOLS,
    DEFAULT_TOOLS,
    TOOL_RUNNERS,
    _run_tools,
    _summarize,
)

Runner = Callable[[str], dict[str, Any]]


class NoToolsError(Exception):
    """No tool applies: the language could not be detected and none was named."""


class NoSourceError(Exception):
    """A directory holds no file any tool recognises."""


def tool_names(lang: str, tools_arg: str | None, audit: bool) -> list[str]:
    """The tools to run on one target: those named, else the language's defaults.

    Each once, in first-named order: --audit adds the language's audit tool to
    a --tools list that may already name it, and a check run twice reports
    every finding twice."""
    if tools_arg:
        names = [t.strip() for t in tools_arg.split(",")]
    elif lang in DEFAULT_TOOLS:
        names = list(DEFAULT_TOOLS[lang])
    else:
        raise NoToolsError(lang)
    if audit:
        names.extend(AUDIT_TOOLS.get(lang, []))
    return list(dict.fromkeys(names))


def _served(name: str, lang: str, groups: dict[str, list[str]]) -> list[str]:
    """The files in GROUPS the tool NAME runs on: LANG's, or with no language
    named (--tools alone), those of every language the tool serves."""
    langs = [lang] if lang != "unknown" else [
        g for g in groups if name in DEFAULT_TOOLS.get(g, []) + AUDIT_TOOLS.get(g, [])]
    return [f for g in langs for f in groups.get(g, [])]


def check_target(target: str, lang: str, names: list[str], root: Path,
                 ignores: list[Ignore]) -> dict[str, Any]:
    """Run NAMES on one target: a file, or a directory named with --lang or --tools.

    A directory goes whole only to the tools that discover files themselves.
    Every other tool reads one file, so it runs once per file it serves, as in
    a plain directory run; handed the directory, it failed to read it.
    """
    if Path(target).is_dir():
        groups, _ = _collect_files(target)
        _drop_ignored(groups, root, ignores)
        checks = [c for name in names for c in _language_checks(
            target, _served(name, lang, groups), [name], root, ignores)]
    else:
        checks = _run_tools(names, target)
    _mark_lab_exempt(checks)
    return {"target": target, "language": lang, "checks": checks,
            "summary": _summarize(checks)}


def _fold(result: dict[str, Any], extra: dict[str, Any]) -> None:
    """Fold one extra run of the same tool into RESULT, a not-run status included."""
    result["issues"] = result.get("issues", []) + extra.get("issues", [])
    if extra["status"] not in _RAN and result["status"] in _RAN:
        result["status"] = extra["status"]
        result["note"] = extra.get("note", "")
    elif extra["status"] in _VERDICTS and result["status"] in _VERDICTS:
        result["status"] = _worse(result["status"], extra["status"])


def _dir_capable_result(runner: Runner, target: str, files: list[str], root: Path,
                        ignores: list[Ignore]) -> dict[str, Any]:
    """One run over the whole directory, where the tool does its own discovery.

    It discovers by SUFFIX, so a script found by its shebang is not in it:
    each one is named to the tool and folded into the same result, a not-run
    status included, so it cannot drop out.
    """
    result = runner(target)
    _drop_ignored_issues(result, root, ignores)
    for fp in (f for f in files if not Path(f).suffix):
        _fold(result, runner(fp))
    return result


def _per_file_result(name: str, runner: Runner, files: list[str]) -> dict[str, Any]:
    """One run per file, merged into one result for the tool.

    A runner that could not run on a file did not run for this language, so a
    not-run status wins over pass and fail alike: folding it into "pass" would
    read eslint with no node on PATH as clean.
    """
    issues: list[dict[str, Any]] = []
    statuses: set[str] = set()
    note = ""
    for fp in files:
        result = runner(fp)
        issues.extend(result.get("issues", []))
        statuses.add(result["status"])
        if result["status"] not in _RAN:
            note = note or result.get("note", "")
    not_run = sorted(statuses - set(_RAN))
    merged: dict[str, Any] = {
        "tool":   name,
        "status": not_run[0] if not_run else max(statuses & set(_VERDICTS), default="pass",
                                                 key=_VERDICTS.index),
        "issues": issues,
    }
    if note:
        merged["note"] = note
    return merged


def _language_checks(target: str, files: list[str], names: list[str], root: Path,
                     ignores: list[Ignore]) -> list[dict[str, Any]]:
    checks = []
    for name in names:
        runner = TOOL_RUNNERS.get(name)
        if not runner:
            checks.extend(_run_tools([name], target))
        elif name in _DIR_CAPABLE:
            checks.append(_dir_capable_result(runner, target, files, root, ignores))
        else:
            checks.append(_per_file_result(name, runner, files))
    return checks


def check_directory(target: str, audit: bool, root: Path,
                    ignores: list[Ignore]) -> dict[str, Any]:
    """Scan a directory, group its files by language, run each language's tools."""
    groups, skipped = _collect_files(target)
    ignored = _drop_ignored(groups, root, ignores)
    groups = {lang: files for lang, files in groups.items() if files}
    if not groups:
        raise NoSourceError(target)
    all_checks: list[dict[str, Any]] = []
    sections = []
    for lang, files in sorted(groups.items()):
        names = list(DEFAULT_TOOLS.get(lang, [])) + (AUDIT_TOOLS.get(lang, []) if audit else [])
        if not names:
            continue
        checks = _language_checks(target, files, names, root, ignores)
        all_checks.extend(checks)
        sections.append({"language": lang, "file_count": len(files),
                         "tools": [c["tool"] for c in checks]})
    _mark_lab_exempt(all_checks)
    return {
        "target":     target,
        "mode":       "multi-language",
        "languages":  sections,
        # Reported even when empty, so "nothing was skipped" is a stated result
        # rather than an absence the reader has to infer.
        "skipped":    [{"extension": e, "file_count": n}
                       for e, n in sorted(skipped.items(), key=lambda kv: -kv[1])],
        # Every file .jp-tools-ignore left out, with the entry that did it.
        "ignored":    ignored,
        "checks":     all_checks,
        "summary":    _summarize(all_checks),
    }
