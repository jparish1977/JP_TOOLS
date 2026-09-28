#!/usr/bin/env python
"""
JP_TOOLS/check.py
Run code quality tools against arbitrary files or directories.
Outputs structured JSON suitable for AI agent consumption.

Usage:
    python check.py <path> [--lang python|js|auto] [--tools ruff,mypy] [--pretty]

Exit codes:
    0  - every check ran, and none found an error (warnings OK)
    1  - one or more errors found
    2  - usage or path error, OR a check that could not run: a tool that is
         unavailable, a tool that exited abnormally, or no runner for a
         requested tool. Each one is named on stderr. A check that did not
         run is not a pass.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jpcheck.baseline import compare_baseline, record_baseline
from jpcheck.common import _RAN, _repo_root
from jpcheck.files import (
    _IGNORE_FILE,
    _collect_files,
    _detect_lang,
    _drop_ignored,
    _drop_ignored_issues,
    _IgnoreError,
    ignored_by,
    load_ignores,
)
from jpcheck.lab import _mark_lab_exempt
from jpcheck.registry import (
    _DIR_CAPABLE,
    AUDIT_TOOLS,
    DEFAULT_TOOLS,
    TOOL_RUNNERS,
    _exit_code,
    _run_tools,
    _summarize,
)
from jpcheck.smells import _SMELLS

# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run code quality tools and output structured JSON.",
    )
    parser.add_argument("target", help="File or directory to check")
    parser.add_argument("--lang",  choices=["python", "js", "css", "html", "php", "auto"],
                        default="auto",
                        help="Language override (default: auto-detect)")
    parser.add_argument("--tools", metavar="TOOLS",
                        help="Comma-separated tools to run, e.g. ruff,mypy")
    parser.add_argument("--pretty", action="store_true",
                        help="Pretty-print JSON output")
    parser.add_argument("--audit", action="store_true",
                        help="Also run security audit tools (pip-audit, npm audit, composer audit)")
    parser.add_argument("--skip-unsupported", action="store_true",
                        help="Exit 0 on a file whose language cannot be detected, "
                             "instead of 2. For callers handed an arbitrary file "
                             "list (the pre-commit hook), where a README is not "
                             "a failure.")
    parser.add_argument("--baseline", metavar="FILE",
                        help="Compare against a recorded baseline and fail only "
                             "where a file got WORSE, instead of failing on any "
                             "finding at all. Refuses if the baseline was "
                             "recorded in a different mode.")
    parser.add_argument("--record-baseline", metavar="FILE",
                        help="Measure per-file and write a baseline to FILE. "
                             "Per-file because that is what the gate does; see "
                             "METHODOLOGY, 'Adopting this in a codebase you "
                             "inherited', step 2.")
    parser.add_argument("--force-baseline", action="store_true",
                        help="Allow --record-baseline to write a baseline that "
                             "drops files the existing one recorded. Needed "
                             "only when that loss is intended, such as after "
                             "deleting files.")
    parser.add_argument("--smells-base", metavar="REF", default="HEAD",
                        help="What a smell is NEW against: over its threshold "
                             "now and not at REF fails; already over at REF warns. "
                             "Default HEAD, the last commit, which is what the "
                             "pre-commit hook wants. CI on a pull request passes "
                             "the PR's base.")
    args = parser.parse_args()
    _SMELLS["base"] = args.smells_base

    target = str(Path(args.target).resolve())
    if not Path(target).exists():
        print(json.dumps({"error": f"Path not found: {target}"}))
        sys.exit(2)

    is_dir = Path(target).is_dir()

    # Read before any measuring, so a bad entry refuses every mode alike.
    root = _repo_root(target)
    try:
        ignores = load_ignores(root)
    except _IgnoreError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(2)

    # ── Record a baseline and stop ────────────────────────────────────────
    # Its own path, before the normal flow, because recording MEASURES
    # DIFFERENTLY: it runs every file on its own even when handed a directory,
    # which is exactly what `check.py .` does not do.
    if args.record_baseline:
        sys.exit(record_baseline(target, args.record_baseline, args.audit,
                                 args.force_baseline))

    # ── Single file or explicit --lang / --tools ──────────────────────────
    if not is_dir or args.lang != "auto" or args.tools:
        # A file the repo does not own is skipped, and says which entry did it,
        # so the hook prints the exclusion in every commit it applies to.
        hit = None if is_dir else ignored_by(target, root, ignores)
        if hit:
            print(json.dumps({"target": target,
                              "skipped": f"ignored by {_IGNORE_FILE}: {hit[0]} ({hit[1]})"}))
            sys.exit(0)
        lang = args.lang if args.lang != "auto" else _detect_lang(target)
        if args.tools:
            tool_names = [t.strip() for t in args.tools.split(",")]
        elif lang in DEFAULT_TOOLS:
            tool_names = list(DEFAULT_TOOLS[lang])
        else:
            if args.skip_unsupported:
                print(json.dumps({"target": target, "skipped":
                                  "no language detected"}))
                sys.exit(0)
            print(json.dumps({"error": f"Cannot detect language for: {target}"}))
            sys.exit(2)
        if args.audit:
            tool_names.extend(AUDIT_TOOLS.get(lang, []))

        checks = _run_tools(tool_names, target)
        # A directory named with --tools or --lang is handed to each tool whole,
        # so ignored files are found there too: drop their findings.
        if is_dir:
            for c in checks:
                _drop_ignored_issues(c, root, ignores)
        _mark_lab_exempt(checks)
        # Held in its own name rather than read back out of `output`: the dict
        # is heterogeneous, so indexing it twice asks the type checker to
        # believe a str and a list are also subscriptable by "errors".
        summary = _summarize(checks)
        output = {
            "target":   target,
            "language": lang,
            "checks":   checks,
            "summary":  summary,
        }
        print(json.dumps(output, indent=2 if args.pretty else None))
        # A single file IS the per-file measurement, so a per-file baseline is
        # comparable here and the verdict replaces the compare-against-zero one.
        if args.baseline:
            sys.exit(compare_baseline(checks, target, args.baseline, "per-file"))
        sys.exit(_exit_code(summary, checks))

    # ── Directory: scan, group by language, run appropriate tools ─────────
    groups, skipped = _collect_files(target)
    ignored = _drop_ignored(groups, root, ignores)
    groups = {lang: files for lang, files in groups.items() if files}
    if not groups:
        print(json.dumps({"error": f"No recognized source files in: {target}"}))
        sys.exit(2)

    all_checks = []
    lang_sections = []

    for lang, files in sorted(groups.items()):
        tool_names = list(DEFAULT_TOOLS.get(lang, []))
        if args.audit:
            tool_names.extend(AUDIT_TOOLS.get(lang, []))
        if not tool_names:
            continue

        lang_checks = []
        for name in tool_names:
            runner = TOOL_RUNNERS.get(name)
            if not runner:
                continue
            if name in _DIR_CAPABLE:
                # Run once against the whole dir -- tool handles file discovery.
                result = runner(target)
                _drop_ignored_issues(result, root, ignores)
                # ...by SUFFIX, so a script found by its shebang is not in it.
                # Name each one to the tool and fold it into the same result,
                # a not-run status included, so it cannot drop out.
                for fp in (f for f in files if not Path(f).suffix):
                    extra = runner(fp)
                    result["issues"] = result.get("issues", []) + extra.get("issues", [])
                    if extra["status"] not in _RAN and result["status"] in _RAN:
                        result["status"] = extra["status"]
                        result["note"] = extra.get("note", "")
                    elif extra["status"] == "fail" and result["status"] == "pass":
                        result["status"] = "fail"
                lang_checks.append(result)
            else:
                # Run per-file, merge issues into one result per tool.
                # A runner that could not run on a file did not run for this
                # language. Keeping only "fail" and folding every other status
                # into "pass" would read eslint with no node on PATH as clean.
                merged_issues: list[dict[str, Any]] = []
                statuses: set[str] = set()
                note = ""
                for fp in files:
                    result = runner(fp)
                    merged_issues.extend(result.get("issues", []))
                    statuses.add(result["status"])
                    if result["status"] not in _RAN:
                        note = note or result.get("note", "")
                not_run = sorted(statuses - set(_RAN))
                merged: dict[str, Any] = {
                    "tool":   name,
                    "status": not_run[0] if not_run else
                              ("fail" if "fail" in statuses else "pass"),
                    "issues": merged_issues,
                }
                if note:
                    merged["note"] = note
                lang_checks.append(merged)

        all_checks.extend(lang_checks)
        lang_sections.append({
            "language":   lang,
            "file_count": len(files),
            "tools":      [c["tool"] for c in lang_checks],
        })

    _mark_lab_exempt(all_checks)
    summary = _summarize(all_checks)
    output = {
        "target":     target,
        "mode":       "multi-language",
        "languages":  lang_sections,
        # Reported even when empty, so "nothing was skipped" is a stated result
        # rather than an absence the reader has to infer.
        "skipped":    [{"extension": e, "file_count": n}
                       for e, n in sorted(skipped.items(), key=lambda kv: -kv[1])],
        # Every file .jp-tools-ignore left out, with the entry that did it.
        "ignored":    ignored,
        "checks":     all_checks,
        "summary":    summary,
    }

    print(json.dumps(output, indent=2 if args.pretty else None))
    # A directory run lets the dir-capable tools resolve across files, so this
    # is the WHOLE-REPO measurement. Declaring it as such is what makes
    # compare_baseline refuse rather than report scope as regression.
    if args.baseline:
        sys.exit(compare_baseline(all_checks, target, args.baseline, "whole-repo"))
    sys.exit(_exit_code(summary, all_checks))


if __name__ == "__main__":
    main()
