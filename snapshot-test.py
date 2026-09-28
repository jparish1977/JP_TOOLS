#!/usr/bin/env python3
"""Snapshot testing for JS data structures, to compare before and after a refactor.

A Node script prints JSON; `capture` saves it as a named snapshot, `compare`
reports every path where two snapshots differ, and `diff` does both against
a baseline.

Usage:
    python snapshot-test.py capture --name before-refactor --script test.mjs --output snapshots/
    python snapshot-test.py compare snapshots/before-refactor.json snapshots/after-refactor.json
    python snapshot-test.py diff --name after --script test.mjs --baseline snapshots/before-refactor.json

Numbers compare by value (1 equals 1.0), within 1e-10; everything else by
type and value. A list of a different length is reported as `.length`, and
its common prefix is compared element by element.

Exit status: 0 identical (or captured), 1 different, 2 the script failed or
a snapshot could not be read.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeGuard

SHOWN = 50
TOLERANCE = 1e-10
MISSING = "(missing)"


class SnapshotError(Exception):
    """The script could not produce a snapshot, or a snapshot could not be read."""


@dataclass(frozen=True)
class Difference:
    path: str
    a: object
    b: object


def capture(script_path: str, name: str, output_dir: str) -> Path:
    """Run the Node script, save its JSON output as snapshot NAME. Raises SnapshotError."""
    script = Path(script_path).resolve()
    if not script.exists():
        raise SnapshotError(f"{script_path} not found")
    try:
        result = subprocess.run(["node", str(script)], capture_output=True, text=True,
                                timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SnapshotError(f"could not run node: {exc}") from exc
    if result.returncode != 0:
        raise SnapshotError(f"script failed (exit {result.returncode}):\n{result.stderr}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"script output is not valid JSON: {result.stdout[:500]}") from exc
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.json"
    snapshot = {"name": name, "timestamp": datetime.now().astimezone().isoformat(),
                "script": str(script), "data": data}
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print(f"Snapshot saved: {path}")
    return path


def _is_number(v: object) -> TypeGuard[int | float]:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _diff_dicts(a: dict[str, object], b: dict[str, object], path: str) -> list[Difference]:
    diffs: list[Difference] = []
    for key in sorted(a.keys() | b.keys()):
        child = _join(path, key)
        if key not in a or key not in b:
            diffs.append(Difference(child, a.get(key, MISSING), b.get(key, MISSING)))
        else:
            diffs += deep_diff(a[key], b[key], child)
    return diffs


def _diff_lists(a: list[object], b: list[object], path: str) -> list[Difference]:
    diffs = [Difference(f"{path}.length", len(a), len(b))] if len(a) != len(b) else []
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        diffs += deep_diff(x, y, f"{path}[{i}]")
    return diffs


def deep_diff(a: object, b: object, path: str = "") -> list[Difference]:
    """Every path where two JSON values differ."""
    if _is_number(a) and _is_number(b):
        same = abs(a - b) <= TOLERANCE
    elif isinstance(a, dict) and isinstance(b, dict):
        return _diff_dicts(a, b, path)
    elif isinstance(a, list) and isinstance(b, list):
        return _diff_lists(a, b, path)
    else:
        same = type(a) is type(b) and a == b
    return [] if same else [Difference(path or "(root)", a, b)]


def _numeric_summary(a: object, b: object) -> list[str]:
    """For two objects, each top-level number that changed, with the change."""
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return []
    lines = []
    for key in sorted(a.keys() & b.keys()):
        va, vb = a[key], b[key]
        if _is_number(va) and _is_number(vb) and va != vb:
            delta = vb - va
            pct = f" ({delta / va * 100:+.1f}%)" if va else ""
            lines.append(f"  {key}: {va} -> {vb}  {delta:+}{pct}")
    return ["Summary:", *lines] if lines else []


def _load(path: str) -> dict[str, object]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"cannot read snapshot {path}: {exc}") from exc
    if not isinstance(doc, dict) or "data" not in doc:
        raise SnapshotError(f"{path} is not a snapshot (no data)")
    return doc


def compare(path_a: str, path_b: str) -> bool:
    """Report how two snapshots differ. True when they do not. Raises SnapshotError."""
    a, b = _load(path_a), _load(path_b)
    print(f"Comparing: {a.get('name')} vs {b.get('name')}")
    print(f"  A: {a.get('timestamp')}")
    print(f"  B: {b.get('timestamp')}")
    print()
    diffs = deep_diff(a["data"], b["data"])
    if not diffs:
        print("PASS: Snapshots are identical")
        return True
    print(f"FAIL: {len(diffs)} difference(s) found:")
    print()
    for d in diffs[:SHOWN]:
        print(f"  {d.path}\n    A: {json.dumps(d.a)[:100]}\n    B: {json.dumps(d.b)[:100]}\n")
    if len(diffs) > SHOWN:
        print(f"  ... and {len(diffs) - SHOWN} more")
    for line in _numeric_summary(a["data"], b["data"]):
        print(line)
    return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture", help="Capture a snapshot")
    cap.add_argument("--name", required=True, help="Snapshot name")
    cap.add_argument("--script", required=True, help="Node.js script that outputs JSON")
    cap.add_argument("--output", default="snapshots", help="Output directory")
    cmp = sub.add_parser("compare", help="Compare two snapshots")
    cmp.add_argument("snapshot_a", help="First snapshot JSON")
    cmp.add_argument("snapshot_b", help="Second snapshot JSON")
    diff = sub.add_parser("diff", help="Capture and compare against baseline")
    diff.add_argument("--name", required=True, help="Snapshot name")
    diff.add_argument("--script", required=True, help="Node.js script")
    diff.add_argument("--baseline", required=True, help="Baseline snapshot to compare against")
    diff.add_argument("--output", default="snapshots", help="Output directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "capture":
            capture(args.script, args.name, args.output)
            return 0
        if args.command == "compare":
            return 0 if compare(args.snapshot_a, args.snapshot_b) else 1
        taken = capture(args.script, args.name, args.output)
        return 0 if compare(args.baseline, str(taken)) else 1
    except SnapshotError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
