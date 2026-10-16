"""Recording a per-file baseline and comparing a run against it."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jpexit
from jpcheck.common import (
    _VERDICTS,
    _node_bin,
    _php_bin,
    _php_cmd,
    _rel,
    _repo_root,
    node_module_roots,
)
from jpcheck.files import _collect_files, _detect_lang, _drop_ignored, ignored_by, load_ignores
from jpcheck.native_tools import _pwsh
from jpcheck.php_tools import _composer_cmd
from jpcheck.registry import AUDIT_TOOLS, DEFAULT_TOOLS, _run_tools, _summarize, serves

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
#
# AUDITS ARE NOT BASELINED. pip-audit, npm audit and composer audit count
# advisories against the dependencies, and their count moves when a new
# advisory is published, with no change to any file. So an audit is never
# recorded and never compared: its findings are reported on every run, and a
# baseline verdict does not include them.
#
# Version 2 records fixable and fixable_unsafe per file, so a write-back moves
# them with the rest and "totals" stays what a fresh record would hold.
# Version 1 did not, and is refused with the command that re-records it.

BASELINE_VERSION = 2

# The counts each file's entry holds, per tool.
_COUNTS = ("errors", "warnings", "fixable", "fixable_unsafe")

_AUDITS = frozenset(t for tools in AUDIT_TOOLS.values() for t in tools)

# Codec names fold case, so "utf-8" and "UTF-8" name the same codec.
_UTF8 = "utf-8"

# A version is read from the copy of the tool its runner runs, found the way
# the runner finds it: PATH's eslint read while the gate loads node_modules'
# would match or differ by accident. Every tool the registry can run is in
# _VERSION_CMD, _NODE_PACKAGES or _INTERNAL_CHECKS, and
# tests/test_baseline_versions.py (test_every_tool_has_a_version_source)
# holds that for the next tool added.


def _on_path(*names: str) -> list[str] | None:
    exe = next((p for p in map(shutil.which, names) if p), None)
    return [exe, "--version"] if exe else None


def _through_php(name: str) -> list[str] | None:
    """A Composer tool, run as `php BIN` the way its runner runs it."""
    php, bin_ = _php_cmd(), _php_bin(name)
    return [php, bin_, "--version"] if php and bin_ else None


def _composer() -> list[str] | None:
    """composer, invoked the way run_composer_audit invokes it."""
    cmd = _composer_cmd(_php_cmd(), shutil.which("composer") or shutil.which("composer.bat"))
    return [*cmd[:-2], "--version"] if cmd else None


def _prettier() -> list[str] | None:
    exe = _node_bin("prettier")
    return [exe, "--version"] if exe else None


# The highest installed version, which is the one Import-Module loads.
_PSSA_VERSION = ("(Get-Module -ListAvailable PSScriptAnalyzer | Sort-Object Version "
                 "-Descending | Select-Object -First 1).Version.ToString()")


def _pssa() -> list[str] | None:
    pwsh = _pwsh()
    return [pwsh, "-NoProfile", "-NonInteractive", "-Command", _PSSA_VERSION] if pwsh else None


_VERSION_CMD: dict[str, Callable[[], list[str] | None]] = {
    "ruff":             lambda: _on_path("ruff"),
    "mypy":             lambda: _on_path("mypy"),
    "cppcheck":         lambda: _on_path("cppcheck"),
    "shellcheck":       lambda: _on_path("shellcheck"),
    "pip-audit":        lambda: _on_path("pip-audit", "pip-audit.exe"),
    "npm-audit":        lambda: _on_path("npm", "npm.cmd"),
    "prettier":         _prettier,
    "phpstan":          lambda: _through_php("phpstan"),
    "phpcs":            lambda: _through_php("phpcs"),
    "rector":           lambda: _through_php("rector"),
    "composer-audit":   _composer,
    "psscriptanalyzer": _pssa,
}

# eslint and stylelint run inside JP_TOOLS' node runners, which import the
# package from the first tool root's node_modules that has it. That copy's
# package.json says its version; there is no binary to ask.
_NODE_PACKAGES = ("eslint", "stylelint")

# JP_TOOLS' own checks, not separate tools: their version is this checkout's.
# "" is recorded for them, so their counts are always compared.
_INTERNAL_CHECKS = frozenset({"directive-reason", "no-cover", "noqa", "smells",
                              "suppress-reason"})

_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")


def _node_package_version(name: str) -> str:
    for modules in node_module_roots():
        manifest = modules / name / "package.json"
        if manifest.is_file():
            try:
                version = json.loads(manifest.read_text(encoding=_UTF8)).get("version")
            except (OSError, ValueError, AttributeError):
                # reason: "" is "cannot tell", which never refuses a comparison
                return ""
            return version if isinstance(version, str) else ""
    return ""


def _tool_version(name: str) -> str:
    """Best-effort version string, or "" meaning "cannot tell"."""
    if name in _NODE_PACKAGES:
        return _node_package_version(name)
    resolve = _VERSION_CMD.get(name)
    cmd = resolve() if resolve else None
    if not cmd:
        return ""
    # A cache of its own, as run_psscriptanalyzer has: a pwsh killed mid-write
    # corrupts a shared cache, and every pwsh after it crashes at startup.
    with tempfile.TemporaryDirectory(prefix="jp-version-cache-") as cache:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30,
                               env={**os.environ, "XDG_CACHE_HOME": cache})
        except (OSError, subprocess.SubprocessError):
            # reason: no version to print; the tool's row shows without one, and
            # the tool's own run reports what is wrong with it
            return ""
    m = _VERSION_RE.search(f"{r.stdout} {r.stderr}")
    return m.group(0) if m else ""


def _counts_by_file(checks: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    """{path: {tool: {count: n for each of _COUNTS}}} for one run."""
    out: dict[str, Any] = {}
    for c in checks:
        tool = c.get("tool", "?")
        for i in c.get("issues", []):
            slot = out.setdefault(_rel(i.get("file", ""), root), {})
            counts = slot.setdefault(tool, dict.fromkeys(_COUNTS, 0))
            counts["errors" if i.get("severity") == "error" else "warnings"] += 1
            for flag in ("fixable", "fixable_unsafe"):
                if i.get(flag):
                    counts[flag] += 1
    return out


def _not_audits(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in checks if c.get("tool") not in _AUDITS]


def _statuses(checks: list[dict[str, Any]]) -> dict[str, str]:
    return {c.get("tool", "?"): c.get("status", "unknown") for c in checks}


def per_file_checks(target: str, lang: str = "auto",
                    tools: list[str] | None = None) -> list[dict[str, Any]]:
    """Run the measurement the GATE performs: every file on its own.

    Deliberately not `check.py .`. The entire point of the mode field is that
    the two produce different numbers, so a recorder taking the cheaper
    whole-repo route would record exactly the quantity the gate cannot
    reproduce. A directory compared against a baseline is measured this way
    too, for the same reason.

    LANG other than "auto" keeps only that language's files; TOOLS, when
    given, runs those of them that serve each file's language in place of
    its default ones.
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
        file_lang = _detect_lang(f)
        if lang not in ("auto", file_lang):
            continue
        names = [t for t in tools if serves(t, file_lang)] if tools else list(DEFAULT_TOOLS.get(file_lang, []))
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
        prior = json.loads(Path(dest).read_text(encoding=_UTF8))
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
                     status: dict[str, str], existed: bool, existing: int | None,
                     dropped: list[str]) -> None:
    print(f"recorded {len(files)} file(s) from {len(tools)} tool(s), "
          f"mode=per-file, to {dest}")
    # Say what was displaced, not only what was written. Keyed on the file
    # having EXISTED, not on how much it recorded: a baseline of zero files is
    # a real baseline (every file was clean), and it is the one most easily
    # mistaken for no baseline at all. EXISTING is None when the file could
    # not be read, which is not a baseline of zero files.
    if existed and existing is None:
        print("  replaced a file that could not be read as a baseline, on --force-baseline")
    elif existed:
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


def record_baseline(target: str, dest: str, force: bool = False, tools: list[str] | None = None) -> int:
    """Record TARGET's per-file counts to DEST: every file's default tools,
    or the TOOLS named (where each applies), as a run with them measures."""
    root   = _repo_root(target)
    checks = _not_audits(per_file_checks(target, tools=tools))
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
        return jpexit.NOT_RUN
    Path(dest).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                          encoding=_UTF8)
    _report_recorded(dest, files, tools, status, existed, None if unreadable else existing, dropped)
    return jpexit.OK


def _read_baseline(source: str, mode: str) -> dict[str, Any] | None:
    """The baseline document, or None after saying why it cannot be compared."""
    try:
        doc: dict[str, Any] = json.loads(Path(source).read_text(encoding=_UTF8))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"baseline: cannot read {source}: {exc}", file=sys.stderr)
        return None
    version = doc.get("baseline_version")
    if version != BASELINE_VERSION:
        print(f"baseline: REFUSING to compare. {source} is baseline version "
              f"{version}, and this check.py reads version {BASELINE_VERSION}, "
              f"which records fixable counts per file. Re-record it from the "
              f"repository root: check.py . --record-baseline {source}",
              file=sys.stderr)
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
        # A tool the baseline has no version for is "cannot tell", as "" is.
        was_v, now_v = old_versions.get(tool), _tool_version(tool)
        if was_v and now_v and was_v != now_v:
            skipped.append((tool, f"version {was_v} -> {now_v}"))
    return skipped


def _compared_paths(target: str, root: Path, now: dict[str, Any],
                    was: dict[str, Any], lang: str = "auto") -> tuple[set[str], int]:
    """(the paths this run can compare, how many baselined paths it did not measure).

    A run measures only what it was pointed at. A baselined file outside that
    was not looked at, so it is counted on one line rather than compared: read
    as zero it would print as a gain that never happened, once per file, and
    the hook checks one staged file per run. A measured file that went clean
    has no findings in `now`, so the target itself is added.

    LANG other than "auto" measured only that language's files, so a
    baselined file of another language is outside the run in the same way.
    """
    rel_target = _rel(target, root)
    if Path(target).is_dir():
        # _rel names the root itself ".", never "": it is a Path's as_posix().
        prefix = "" if rel_target == "." else rel_target.rstrip("/") + "/"
        in_scope = {p for p in was if p.startswith(prefix)}
    else:
        in_scope = {rel_target}
    if lang != "auto":
        in_scope = {p for p in in_scope if _detect_lang(str(root / p)) == lang}
    compared = set(now) | in_scope
    unmeasured = len(set(was) - compared)
    return compared, unmeasured


# The counts a file can get worse in: its errors, and its warnings, each on its
# own, so a warning fixed does not pay for an error added. fixable and
# fixable_unsafe say how a finding could be fixed, not how many there are.
_COMPARED = ("errors", "warnings")


def _count(counts: dict[str, Any], path: str, tool: str, key: str) -> int:
    """COUNTS' KEY for PATH's TOOL, zero when there is none. Every entry of a
    baseline this reads (version 2) holds every count, so zero means the file
    or tool was not recorded, which is counting from zero."""
    return int(counts.get(path, {}).get(tool, {}).get(key, 0))


def _deltas(compared: set[str], now: dict[str, Any], was: dict[str, Any],
            skip: set[str]) -> tuple[list[tuple[str, str, str, int, int]], list[str]]:
    """(each (path, tool, count, before, after) that got worse, a line per changed count)."""
    worse, deltas = [], []
    for path in sorted(compared):
        tools = set(now.get(path, {})) | set(was.get(path, {}))
        for tool in sorted(tools - skip):
            for key in _COMPARED:
                before, after = _count(was, path, tool, key), _count(now, path, tool, key)
                if after != before:
                    deltas.append(f"  {path}  {tool}  {key}  {before} -> {after} "
                                  f"({after - before:+d})")
                if after > before:
                    worse.append((path, tool, key, before, after))
    return worse, deltas


def _report_comparison(source: str, skipped: list[tuple[str, str]], deltas: list[str],
                       unmeasured: int, worse: list[tuple[str, str, str, int, int]]) -> None:
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
        for path, tool, key, before, after in worse:
            print(f"  {path}  {tool}  {key}  {before} -> {after}", file=sys.stderr)


# TOUCHED. A file a commit changes must leave with fewer findings than it
# came with, until it has none, and a count that drops is written back, so
# the gain cannot be spent later. Both halves count only the tools that
# judge a file on its own. mypy and phpstan resolve across files, so this
# file's count from them moves when OTHER files change: requiring it to drop
# would fail a commit over code it did not touch, and locking in a drop that
# another file caused would fail a later one when that file changes back.
_CROSS_FILE = frozenset({"mypy", "phpstan"})


def _own_tools(path: str, now: dict[str, Any], was: dict[str, Any],
               skip: set[str]) -> list[str]:
    tools = set(now.get(path, {})) | set(was.get(path, {}))
    return sorted(tools - skip - _CROSS_FILE)


def _not_reduced(path: str, now: dict[str, Any], was: dict[str, Any],
                 skip: set[str]) -> tuple[int, int] | None:
    """(before, after) when a touched file had findings and kept as many; else None.

    Findings are its errors and its warnings, as _deltas and _lock_in count
    them: a file holding only warnings must lower them too. It is asked only
    once no count rose, so an unchanged sum means no count fell."""
    tools = _own_tools(path, now, was, skip)
    before = sum(_count(was, path, t, key) for t in tools for key in _COMPARED)
    after = sum(_count(now, path, t, key) for t in tools for key in _COMPARED)
    return (before, after) if before > 0 and after >= before else None


def _move_totals(totals: dict[str, Any], old: dict[str, Any], new: dict[str, Any]) -> None:
    """TOTALS less what entry OLD held over entry NEW, in each of _COUNTS,
    and in total, which is errors plus warnings."""
    for key in _COUNTS:
        fell = int(old.get(key, 0)) - int(new.get(key, 0))
        totals[key] = int(totals.get(key, 0)) - fell
        if key in ("errors", "warnings"):
            totals["total"] = int(totals.get("total", 0)) - fell


def _lock_in(source: str, doc: dict[str, Any], path: str, now: dict[str, Any],
             skip: set[str]) -> list[str]:
    """Write each lowered count for PATH back to SOURCE; a line per count lowered.

    A tool's entry is replaced when its errors or its warnings fell. Called
    only once no count rose, so nothing compared is written higher. "totals"
    moves with it: each of its counts falls by what each replaced entry held
    more than its new one, so they match a fresh record of the same tree.
    """
    was = doc.setdefault("files", {})
    totals = doc.get("totals")
    lowered = []
    for tool in _own_tools(path, now, was, skip):
        fell = []
        for key in _COMPARED:
            before, after = _count(was, path, tool, key), _count(now, path, tool, key)
            if after < before:
                fell.append(f"  {path}  {tool}  {key}  {before} -> {after}")
        if fell:
            old = was.get(path, {}).get(tool, {})
            new = now.get(path, {}).get(tool, dict.fromkeys(_COUNTS, 0))
            was.setdefault(path, {})[tool] = new
            if isinstance(totals, dict):
                _move_totals(totals, old, new)
            lowered.extend(fell)
    if lowered:
        Path(source).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                                encoding=_UTF8)
    return lowered


def _touched_verdict(source: str, doc: dict[str, Any], path: str, now: dict[str, Any],
                     skip: set[str]) -> bool:
    """True when a touched PATH may pass: it lowered its own count, or had none.
    A pass writes the lowered counts back, and says so."""
    stuck = _not_reduced(path, now, doc.get("files", {}), skip)
    if stuck:
        before, after = stuck
        print(file=sys.stderr)
        print(f"baseline: {path} was changed and still has {after} of its {before} "
              f"recorded finding(s). A commit that changes a file with findings "
              f"fixes at least one (mypy and phpstan are not counted: other "
              f"files move them). If it cannot this time, say why in the "
              f"commit message: a line 'LINT-DEBT-OK: <reason>'.", file=sys.stderr)
        return False
    lowered = _lock_in(source, doc, path, now, skip)
    if lowered:
        print(f"baseline: lowered in {Path(source).name}, so the gain is kept:")
        for line in lowered:
            print(line)
    return True


def _report_audits(checks: list[dict[str, Any]]) -> None:
    """A line per audit tool that ran: its findings are in the report above,
    and the verdict leaves them out."""
    for c in checks:
        if c.get("tool") in _AUDITS:
            print(f"baseline: {c['tool']} is an audit: {len(c.get('issues', []))} "
                  f"finding(s), reported above and never compared to the baseline, "
                  f"since a new advisory is not this file getting worse.", file=sys.stderr)


def raised_counts(old: dict[str, Any], new: dict[str, Any]) -> list[tuple[str, str, str, int, int]]:
    """(path, tool, count, before, after) for each count baseline NEW holds
    above OLD. A file or tool OLD has no entry for counts from zero, so a new
    file recorded with findings is raised. A count OLD's entry does not hold
    at all (an older version's entry) is not compared."""
    was, now = old.get("files", {}), new.get("files", {})
    out = []
    for path in sorted(now):
        for tool in sorted(now[path]):
            entry = was.get(path, {}).get(tool)
            for key in _COUNTS:
                if entry is not None and key not in entry:
                    continue
                before = int((entry or {}).get(key, 0))
                after = int(now[path][tool].get(key, 0))
                if after > before:
                    out.append((path, tool, key, before, after))
    return out


def check_raised(old_source: str, new_source: str) -> int:
    """jpexit.OK when baseline NEW_SOURCE raises no count over OLD_SOURCE,
    FINDINGS when it raises one, NOT_RUN when either cannot be read."""
    docs = []
    for source in (old_source, new_source):
        try:
            docs.append(json.loads(Path(source).read_text(encoding=_UTF8)))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"baseline: cannot read {source}: {exc}", file=sys.stderr)
            return jpexit.NOT_RUN
    raised = raised_counts(docs[0], docs[1])
    if not raised:
        print(f"baseline: {new_source} raises no count over {old_source}")
        return jpexit.OK
    print(f"baseline: {new_source} RAISES {len(raised)} count(s) over {old_source}:",
          file=sys.stderr)
    for path, tool, key, before, after in raised:
        print(f"  {path}  {tool}  {key}  {before} -> {after}", file=sys.stderr)
    return jpexit.FINDINGS


def compare_baseline(checks: list[dict[str, Any]], target: str,
                     source: str, mode: str, touched: bool = False,
                     lang: str = "auto") -> int:
    """jpexit.OK no worse, FINDINGS worse, NOT_RUN cannot compare.

    With TOUCHED, TARGET is one file a commit changes: see _CROSS_FILE.
    LANG is the run's --lang: only that language's files were measured.
    NOT_LOWERED when it is no worse but fixed none of its recorded findings.
    """
    doc = _read_baseline(source, mode)
    if doc is None:
        return jpexit.NOT_RUN
    root = _repo_root(target)
    if touched and Path(target).is_dir():
        print("baseline: --touched takes one file, the one being changed", file=sys.stderr)
        return jpexit.NOT_RUN
    _report_audits(checks)
    checks = _not_audits(checks)
    now = _counts_by_file(checks, root)
    was = doc.get("files", {})
    skipped = _uncomparable(_statuses(checks), doc.get("tools", {}))
    skip = {t for t, _ in skipped}
    compared, unmeasured = _compared_paths(target, root, now, was, lang)
    worse, deltas = _deltas(compared, now, was, skip)
    _report_comparison(source, skipped, deltas, unmeasured, worse)
    if worse:
        return jpexit.FINDINGS
    if touched and not _touched_verdict(source, doc, _rel(target, root), now, skip):
        return jpexit.NOT_LOWERED
    return jpexit.OK
