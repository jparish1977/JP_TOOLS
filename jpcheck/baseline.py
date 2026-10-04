"""Recording a per-file baseline and comparing a run against it."""

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from jpcheck.common import _VERDICTS, _rel, _repo_root
from jpcheck.files import _collect_files, _detect_lang, _drop_ignored, ignored_by, load_ignores
from jpcheck.registry import AUDIT_TOOLS, DEFAULT_TOOLS, _run_tools, _summarize

# ── baseline ──────────────────────────────────────────────────────────────────
#
# A baseline is a stored per-file, per-tool count the gate compares against
# instead of comparing against zero. Three things about it are load-bearing:
#
# MODE. A baseline must be measured the way the gate measures. A whole-repo run
# and the hook's per-file runs disagree on the tools that resolve ACROSS files
# (mypy, phpstan) while ruff, phpcs and rector agree exactly, and the
# difference looks like drift in files nobody touched. So the mode is recorded
# here and a mismatch is REFUSED rather than compared (METHODOLOGY step 2).
#
# TOOLS THAT DID NOT RUN. An unavailable tool reports zero issues. That reads as
# an improvement, lets a real regression through, and then bakes a false zero
# into the next re-record, after which the true count reads as a regression. A
# tool that is unavailable now, or whose version differs from the recording, is
# reported as "cannot compare" and excluded from the verdict. Never counted as
# zero, never silently passed. This is METHODOLOGY step 3 applied to the
# baseline itself: a check that did not run looks exactly like one that passed.
#
# VERSIONS DEGRADE, THEY DO NOT REFUSE. Compare only when BOTH sides are known.
# An unknown version is not evidence of a match, but refusing on one would make
# the feature unusable wherever a tool has no version we can parse, and a
# guard that fires on "cannot tell" gets switched off.
#
# The verdict is on ERRORS, because that is what the gate already exits on.
# Warnings are recorded per file so a later change can use them, and are printed
# in the report, but they do not fail a commit that errors would not fail.

BASELINE_VERSION = 1

_VERSION_CMD = {
    "ruff":      ["ruff", "--version"],
    "mypy":      ["mypy", "--version"],
    "eslint":    ["eslint", "--version"],
    "stylelint": ["stylelint", "--version"],
    "prettier":  ["prettier", "--version"],
    "cppcheck":  ["cppcheck", "--version"],
}
_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")


def _tool_version(name: str) -> str:
    """Best-effort version string, or "" meaning "cannot tell"."""
    cmd = _VERSION_CMD.get(name)
    if not cmd or not shutil.which(cmd[0]):
        return ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        # reason: no version to print; the tool's row shows without one, and
        # the tool's own run reports what is wrong with it
        return ""
    m = _VERSION_RE.search(f"{r.stdout} {r.stderr}")
    return m.group(0) if m else ""


def _counts_by_file(checks: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    """{path: {tool: {"errors": n, "warnings": n}}} for one run."""
    out: dict[str, Any] = {}
    for c in checks:
        tool = c.get("tool", "?")
        for i in c.get("issues", []):
            slot = out.setdefault(_rel(i.get("file", ""), root), {})
            counts = slot.setdefault(tool, {"errors": 0, "warnings": 0})
            counts["errors" if i.get("severity") == "error" else "warnings"] += 1
    return out


def _statuses(checks: list[dict[str, Any]]) -> dict[str, str]:
    return {c.get("tool", "?"): c.get("status", "unknown") for c in checks}


def _per_file_checks(target: str, audit: bool = False) -> list[dict[str, Any]]:
    """Run the measurement the GATE performs: every file on its own.

    Deliberately not `check.py .`. The entire point of the mode field is that
    the two produce different numbers, so a recorder taking the cheaper
    whole-repo route would record exactly the quantity the gate cannot
    reproduce.
    """
    p = Path(target)
    root = _repo_root(target)
    ignores = load_ignores(root)
    if p.is_file():
        files = [] if ignored_by(str(p), root, ignores) else [str(p)]
    else:
        groups, _ = _collect_files(str(p))
        _drop_ignored(groups, root, ignores)
        files = sorted(f for fs in groups.values() for f in fs)
    checks: list[dict[str, Any]] = []
    for f in files:
        lang = _detect_lang(f)
        names = list(DEFAULT_TOOLS.get(lang, []))
        if audit:
            names.extend(AUDIT_TOOLS.get(lang, []))
        if names:
            checks.extend(_run_tools(names, f))
    return checks


def _dropped_entries(dest: str, files: dict[str, Any]) -> tuple[list[str], int, bool]:
    """Files the existing baseline records that this write would not.

    Returns (dropped, existing_count, unreadable). A destination that does not
    exist drops nothing: there is no prior measurement to lose.

    A destination that exists and CANNOT BE PARSED is a third state and is
    reported as such, not folded into the first. A file that fails to parse
    did hold a measurement (a truncated write, conflict markers, a half-synced
    copy over a real baseline), and its contents are precisely what nobody can
    recover, so treating it as absent destroys the prior numbers in the one
    case where losing them costs most.

    Recording is whole-file: the doc is rebuilt and rewritten, so pointing two
    per-file invocations at one path would keep only the second, and the loss
    would surface one step later as the first file failing for being
    un-baselined: a zero that means "never measured" wearing the face of a
    zero that means "measured, and clean".
    """
    if not Path(dest).exists():
        return [], 0, False
    try:
        prior = json.loads(Path(dest).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # reason: the third value says the baseline could not be read, and the caller reports that
        return [], 0, True
    had = prior.get("files")
    if not isinstance(had, dict):
        # Parsed, but not a baseline this tool wrote: something is there, and
        # this run cannot say what it was worth.
        return [], 0, True
    return sorted(set(had) - set(files)), len(had), False


def _refuse_write(dest: str, files: dict[str, Any], dropped: list[str], existing: int,
                  unreadable: bool) -> bool:
    """Print why DEST must not be overwritten, and return True; False when it may be."""
    if unreadable:
        print(f"baseline: REFUSING to write {dest} -- it exists but cannot be "
              f"read as a baseline, so this run cannot say what would be lost.",
              file=sys.stderr)
        print("  That is a reason to stop, not to proceed: a truncated write, "
              "conflict markers or a half-synced file all look like this, and "
              "the numbers in it are the ones nobody can reconstruct.",
              file=sys.stderr)
        print("  Inspect it, or overwrite deliberately with --force-baseline.",
              file=sys.stderr)
        return True
    if dropped:
        shown = ", ".join(dropped[:3]) + (f", +{len(dropped) - 3} more"
                                          if len(dropped) > 3 else "")
        print(f"baseline: REFUSING to write {dest} -- it records {existing} "
              f"file(s) and this run measured {len(files)}, so "
              f"{len(dropped)} would be discarded: {shown}",
              file=sys.stderr)
        print("  A discarded file is not baselined, so it fails the next gate "
              "for having no recorded count -- which looks like a different "
              "bug one step later.", file=sys.stderr)
        print(f"  To baseline a whole tree in one go:  "
              f"check.py <dir> --record-baseline {dest}", file=sys.stderr)
        print("  To replace the baseline deliberately, including on purpose "
              "after deleting files:  --force-baseline", file=sys.stderr)
        return True
    return False


def _report_recorded(dest: str, files: dict[str, Any], tools: list[str],
                     status: dict[str, str], existed: bool, existing: int,
                     dropped: list[str]) -> None:
    print(f"recorded {len(files)} file(s) from {len(tools)} tool(s), "
          f"mode=per-file, to {dest}")
    # Say what was displaced, not only what was written. Keyed on the file
    # having EXISTED, not on how much it recorded: a baseline of zero files is
    # a real baseline (every file was clean), and it is the one most easily
    # mistaken for no baseline at all.
    if existed:
        note = f"  replaced a baseline of {existing} file(s)"
        if dropped:
            note += f", discarding {len(dropped)} on --force-baseline"
        print(note)
    missing = [t for t, s in sorted(status.items()) if s not in _VERDICTS]
    if missing:
        print("  WARNING: recorded while these tools were not running: "
              + ", ".join(f"{t} ({status[t]})" for t in missing))
        print("  Their zeros are not measurements. Re-record where they run, "
              "or a later real count will read as a regression.")


def record_baseline(target: str, dest: str, audit: bool = False,
                    force: bool = False) -> int:
    root   = _repo_root(target)
    checks = _per_file_checks(target, audit)
    files  = _counts_by_file(checks, root)
    tools  = sorted({c.get("tool", "?") for c in checks})
    status = _statuses(checks)
    doc = {
        "baseline_version": BASELINE_VERSION,
        "mode":     "per-file",
        "recorded": time.strftime("%Y-%m-%d"),
        "root":     root.name,
        # Recorded so a later run can refuse to compare numbers that two
        # different tools produced. "" means the version could not be
        # established, which downstream treats as "cannot tell", not "same".
        "tools":    {t: _tool_version(t) for t in tools},
        # A tool that was unavailable WHEN RECORDED contributed zero, and that
        # zero is not a measurement. Keeping the status makes it visible rather
        # than letting it read as a clean file.
        "status":   status,
        "totals":   _summarize(checks),
        "files":    files,
    }
    existed = Path(dest).exists()
    dropped, existing, unreadable = _dropped_entries(dest, files)
    if not force and _refuse_write(dest, files, dropped, existing, unreadable):
        return 2
    Path(dest).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")
    _report_recorded(dest, files, tools, status, existed, existing, dropped)
    return 0


def _read_baseline(source: str, mode: str) -> dict[str, Any] | None:
    """The baseline document, or None after saying why it cannot be compared."""
    try:
        doc: dict[str, Any] = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"baseline: cannot read {source}: {exc}", file=sys.stderr)
        return None
    recorded_mode = doc.get("mode", "unknown")
    if recorded_mode != mode:
        print(f"baseline: REFUSING to compare. It was recorded as "
              f"mode={recorded_mode!r} and this run is {mode!r}. Those are "
              f"different quantities in the same units -- for any tool that "
              f"resolves across files they disagree by hundreds of findings on "
              f"unchanged code. Re-record with --record-baseline.",
              file=sys.stderr)
        return None
    return doc


def _uncomparable(status: dict[str, str], old_versions: dict[str, str]) -> list[tuple[str, str]]:
    """(tool, why) for each tool that did not run, or whose version changed."""
    skipped: list[tuple[str, str]] = []
    for tool, st in sorted(status.items()):
        if st not in _VERDICTS:
            skipped.append((tool, f"did not run ({st})"))
            continue
        was_v, now_v = old_versions.get(tool, ""), _tool_version(tool)
        if was_v and now_v and was_v != now_v:
            skipped.append((tool, f"version {was_v} -> {now_v}"))
    return skipped


def _compared_paths(target: str, root: Path, now: dict[str, Any],
                    was: dict[str, Any]) -> tuple[set[str], int]:
    """(the paths this run can compare, how many baselined paths it did not measure).

    A run measures only what it was pointed at. A baselined file outside that
    was not looked at, so it is counted on one line rather than compared: read
    as zero it would print as a gain that never happened, once per file, and
    the hook checks one staged file per run. A measured file that went clean
    has no findings in `now`, so the target itself is added.
    """
    rel_target = _rel(target, root)
    if Path(target).is_dir():
        prefix = "" if rel_target in ("", ".") else rel_target.rstrip("/") + "/"
        in_scope = {p for p in was if p.startswith(prefix)}
    else:
        in_scope = {rel_target}
    compared = set(now) | in_scope
    unmeasured = len(set(was) - compared)
    return compared, unmeasured


def _deltas(compared: set[str], now: dict[str, Any], was: dict[str, Any],
            skip: set[str]) -> tuple[list[tuple[str, str, int, int]], list[str]]:
    """(each (path, tool, before, after) that got worse, a line per changed count)."""
    worse, deltas = [], []
    for path in sorted(compared):
        tools = set(now.get(path, {})) | set(was.get(path, {}))
        for tool in sorted(tools - skip):
            before = int(was.get(path, {}).get(tool, {}).get("errors", 0))
            after  = int(now.get(path, {}).get(tool, {}).get("errors", 0))
            if after != before:
                deltas.append(f"  {path}  {tool}  {before} -> {after} "
                              f"({after - before:+d})")
            if after > before:
                worse.append((path, tool, before, after))
    return worse, deltas


def _report_comparison(source: str, skipped: list[tuple[str, str]], deltas: list[str],
                       unmeasured: int, worse: list[tuple[str, str, int, int]]) -> None:
    for tool, why in skipped:
        print(f"baseline: cannot compare {tool} -- {why}. Excluded from the "
              f"verdict rather than counted as zero.", file=sys.stderr)
    for line in deltas:
        print(line)
    if not deltas:
        # Said, not implied: a ratchet that only speaks when it fails is
        # indistinguishable from one that never ran.
        print(f"baseline: no change against {Path(source).name}")
    if unmeasured:
        print(f"baseline: {unmeasured} other baselined file(s) not measured in "
              f"this run, so not compared")
    if worse:
        print(file=sys.stderr)
        print("baseline: WORSE than recorded --", file=sys.stderr)
        for path, tool, before, after in worse:
            print(f"  {path}  {tool}  {before} -> {after}", file=sys.stderr)


def compare_baseline(checks: list[dict[str, Any]], target: str,
                     source: str, mode: str) -> int:
    """0 = no worse, 1 = worse, 2 = cannot compare."""
    doc = _read_baseline(source, mode)
    if doc is None:
        return 2
    root = _repo_root(target)
    now = _counts_by_file(checks, root)
    was = doc.get("files", {})
    skipped = _uncomparable(_statuses(checks), doc.get("tools", {}))
    compared, unmeasured = _compared_paths(target, root, now, was)
    worse, deltas = _deltas(compared, now, was, {t for t, _ in skipped})
    _report_comparison(source, skipped, deltas, unmeasured, worse)
    return 1 if worse else 0
