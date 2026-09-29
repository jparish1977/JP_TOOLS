#!/usr/bin/env python3
"""What would each language's standard complexity and size rules find, per repo?

ANSWERS: before those rules are switched on in configs/ (METHODOLOGY 2.14:
  measure what they would find before selecting them), how many findings each
  rule gives, per repo and in total, over every tracked file of its language,
  at the candidate limits below. Each repo is swept in parallel (2.8) and each
  (repo, language) cell ends in one of three outcomes: clean, findings, or
  could not run, with the reason. The rules run in isolation from any project
  config, so every repo is measured alike.

    python  ruff        C901 (mccabe), PLR0912 (branches), PLR0915 (statements)
    php     phpcs       Generic.Metrics.CyclomaticComplexity, NestingLevel
    js      eslint      complexity, max-depth, max-lines-per-function
    shell, cpp          no standard rule measured: counted as files only

TESTED AGAINST: the 40 git repos under ~/projects on a 32-core box,
  2026-09-29, 2s, every cell measured. python 22 repos, 17 with findings:
  C901>10 289, C901>15 132, PLR0912 179, PLR0915 186. php 8 repos, 4 with
  findings: CyclomaticComplexity 5 errors (>20) and 7 warnings (>10). js 6
  repos, 4 with findings: complexity>10 40, >15 22, max-lines-per-function
  16, max-depth 5. The positive controls are those findings, e.g. phpcs's
  errors in a consuming repo. A first run counted every eslint message with no
  rule id as a parse error (162); they were the repos' own eslint-disable
  comments reported unused, now switched off, and only a fatal message
  counts as unparsed.

NOT TESTED AGAINST: files that are not tracked; repos not cloned on the box it
  runs on (a laptop had 50 that day, this box 40). TypeScript, which eslint
  would need a parser for. CODE OUTSIDE ANY FUNCTION: none of these rules
  measures it, so a script with all its logic at top level reads clean.

usage: tools/lab/complexity-sweep.py [REPOS_DIR] [--jobs N]   (default ~/projects)
  Run from a JP_TOOLS checkout root that has vendor/ and node_modules/.
"""
import json
import os
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

JP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(JP))

from jpcheck.common import tool_roots
from jpcheck.files import _detect_lang


def _tool(rel: str) -> str:
    """REL under this checkout or, from a worktree, under its main checkout, as check.py finds it."""
    for root in tool_roots():
        if (root / rel).exists():
            return str(root / rel)
    return str(JP / rel)

# The candidate limits: (label, limit) pairs, each measured.
LIMITS = {"complexity": (10, 15), "branches": (12,), "statements": (50,),
          "depth": (4,), "function-lines": (100,)}


def _name_process(name: bytes) -> None:
    """Name the process (what top and btop show) instead of python3."""
    try:
        import ctypes
        ctypes.CDLL(None).prctl(15, name, 0, 0, 0)
    except (OSError, AttributeError):
        # reason: no libc or no prctl: the process runs unnamed, which is never fatal
        pass


def tracked(repo: Path) -> dict[str, list[str]]:
    """Tracked files of REPO by language, as check.py detects it."""
    r = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"],
                       capture_output=True, text=True, check=False)
    by: dict[str, list[str]] = {}
    for rel in (n for n in r.stdout.split("\0") if n):
        path = repo / rel
        if path.is_file() and not path.is_symlink():
            by.setdefault(_detect_lang(str(path)), []).append(rel)
    return by


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False,
                          timeout=900)


def python_counts(repo: Path, files: list[str]) -> Counter[str] | str:
    counts: Counter[str] = Counter()
    for limit in LIMITS["complexity"]:
        r = _run(["ruff", "check", "--isolated", "--output-format", "json",
                  "--select", "C901,PLR0912,PLR0915",
                  "--config", f"lint.mccabe.max-complexity={limit}", *files], repo)
        if r.returncode not in (0, 1):
            return f"ruff exit {r.returncode}: {(r.stderr.strip().splitlines() or [''])[-1][:150]}"
        for f in json.loads(r.stdout or "[]"):
            code = f["code"]
            if code == "C901":
                counts[f"C901>{limit}"] += 1
            elif limit == LIMITS["complexity"][0]:
                counts[code] += 1
    return counts


def php_counts(repo: Path, files: list[str]) -> Counter[str] | str:
    r = _run([_tool("vendor/bin/phpcs"), "--standard=Generic", "--report=json",
              "--sniffs=Generic.Metrics.CyclomaticComplexity,Generic.Metrics.NestingLevel",
              "-q", *files], repo)
    if r.returncode not in (0, 1, 2):
        return f"phpcs exit {r.returncode}: {(r.stderr.strip().splitlines() or [''])[-1][:150]}"
    counts: Counter[str] = Counter()
    for data in json.loads(r.stdout or "{}").get("files", {}).values():
        for m in data.get("messages", []):
            counts[f"{m['source'].split('.')[2]}:{m['type'].lower()}"] += 1
    return counts


def js_counts(repo: Path, files: list[str]) -> Counter[str] | str:
    counts: Counter[str] = Counter()
    for limit in LIMITS["complexity"]:
        rules = [f"complexity: [1, {limit}]"]
        if limit == LIMITS["complexity"][0]:
            rules += [f"max-depth: [1, {LIMITS['depth'][0]}]",
                      f"max-lines-per-function: [1, {LIMITS['function-lines'][0]}]"]
        # With every other rule off, a repo's own eslint-disable comments would
        # be reported as unused, with no rule id: not what is measured here.
        cmd = [_tool("node_modules/.bin/eslint"), "--no-config-lookup", "-f", "json",
               "--no-warn-ignored", "--report-unused-disable-directives-severity", "off"]
        for rule in rules:
            cmd += ["--rule", rule]
        r = _run(cmd + files, repo)
        if r.returncode not in (0, 1):
            return f"eslint exit {r.returncode}: {(r.stderr.strip().splitlines() or [''])[-1][:150]}"
        for res in json.loads(r.stdout or "[]"):
            for m in res.get("messages", []):
                if m.get("fatal"):
                    # eslint could not parse the file: unmeasured, not a finding.
                    if limit == LIMITS["complexity"][0]:
                        counts["UNPARSED-FILES"] += 1
                    continue
                rule = m.get("ruleId") or "no-rule-id"
                counts[f"{rule}>{limit}" if rule == "complexity" else rule] += 1
    return counts


MEASURE = {"python": python_counts, "php": php_counts, "js": js_counts}


Row = tuple[str, str, int, Counter[str] | str | None]


def sweep(repo: Path) -> list[Row]:
    """(repo, language, files, counts or the reason it could not run, or None)."""
    rows: list[Row] = []
    for lang, files in sorted(tracked(repo).items()):
        if lang in MEASURE:
            try:
                rows.append((repo.name, lang, len(files), MEASURE[lang](repo, files)))
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                rows.append((repo.name, lang, len(files), f"{exc.__class__.__name__}: {exc}"[:150]))
        elif lang in ("shell", "cpp"):
            rows.append((repo.name, lang, len(files), None))
    return rows


def main(argv: list[str]) -> int:
    jobs = os.cpu_count() or 4
    if "--jobs" in argv:
        i = argv.index("--jobs")
        jobs = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    base = Path(argv[0]).expanduser() if argv else Path.home() / "projects"
    repos = [p.parent for p in sorted(base.glob("*/.git"))]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        rows = [row for result in pool.map(sweep, repos) for row in result]
    totals: dict[str, Counter[str]] = {}
    failed = 0
    for repo, lang, n, counts in rows:
        if counts is None:
            print(f"{repo:28} {lang:7} {n:5} files  NOT MEASURED (no standard rule)")
        elif isinstance(counts, str):
            failed += 1
            print(f"{repo:28} {lang:7} {n:5} files  COULD NOT RUN: {counts}")
        else:
            totals.setdefault(lang, Counter()).update(counts)
            shown = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "clean"
            print(f"{repo:28} {lang:7} {n:5} files  {shown}")
    print()
    for lang, counts in sorted(totals.items()):
        cells = [r for r in rows if r[1] == lang and not isinstance(r[3], str) and r[3] is not None]
        dirty = sum(1 for r in cells if r[3])
        print(f"TOTAL {lang:7} {len(cells)} repo(s), {dirty} with findings: "
              + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "clean"))
    print(f"{len(repos)} repo(s), {failed} cell(s) could not run")
    return 1 if failed else 0


if __name__ == "__main__":
    _name_process(b"cx-sweep")
    sys.exit(main(sys.argv[1:]))
