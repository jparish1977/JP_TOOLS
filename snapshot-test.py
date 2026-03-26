#!/usr/bin/env python3
"""Snapshot testing for JS data structures.

Captures output from a JS expression evaluated in Node, saves as JSON snapshots,
and compares before/after refactoring.

Usage:
    # Capture a snapshot
    python snapshot-test.py capture --name before-refactor --script test.mjs --output snapshots/

    # Compare two snapshots
    python snapshot-test.py compare snapshots/before-refactor.json snapshots/after-refactor.json

    # Quick diff: capture and compare in one step
    python snapshot-test.py diff --name refactor-test --script test.mjs --baseline snapshots/before-refactor.json
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime


def capture_snapshot(script_path: str, name: str, output_dir: str) -> dict:
    """Run a Node.js script and capture its JSON output as a snapshot."""
    script = Path(script_path).resolve()
    if not script.exists():
        print(f"Error: {script_path} not found", file=sys.stderr)
        sys.exit(1)

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["node", str(script)],
        capture_output=True, text=True, timeout=60
    )

    if result.returncode != 0:
        print(f"Script failed (exit {result.returncode}):", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("Error: script output is not valid JSON", file=sys.stderr)
        print(f"Output: {result.stdout[:500]}", file=sys.stderr)
        sys.exit(1)

    snapshot = {
        "name": name,
        "timestamp": datetime.now().isoformat(),
        "script": str(script),
        "data": data,
    }

    snapshot_path = output / f"{name}.json"
    snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print(f"Snapshot saved: {snapshot_path}")
    return snapshot


def compare_snapshots(path_a: str, path_b: str, verbose: bool = False) -> bool:
    """Compare two snapshots and report differences."""
    a = json.loads(Path(path_a).read_text(encoding="utf-8"))
    b = json.loads(Path(path_b).read_text(encoding="utf-8"))

    data_a = a["data"]
    data_b = b["data"]

    print(f"Comparing: {a['name']} vs {b['name']}")
    print(f"  A: {a['timestamp']}")
    print(f"  B: {b['timestamp']}")
    print()

    diffs = deep_diff(data_a, data_b, "")

    if not diffs:
        print("PASS: Snapshots are identical")
        return True

    print(f"FAIL: {len(diffs)} difference(s) found:")
    print()
    for diff in diffs[:50]:  # Cap output
        print(f"  {diff['path']}")
        print(f"    A: {json.dumps(diff['a'])[:100]}")
        print(f"    B: {json.dumps(diff['b'])[:100]}")
        print()

    if len(diffs) > 50:
        print(f"  ... and {len(diffs) - 50} more")

    # Summary stats
    if isinstance(data_a, dict) and isinstance(data_b, dict):
        print("Summary:")
        for key in sorted(set(list(data_a.keys()) + list(data_b.keys()))):
            val_a = data_a.get(key)
            val_b = data_b.get(key)
            if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
                delta = val_b - val_a
                pct = (delta / val_a * 100) if val_a != 0 else float("inf")
                status = "=" if delta == 0 else f"{'+' if delta > 0 else ''}{delta} ({pct:+.1f}%)"
                print(f"  {key}: {val_a} → {val_b}  {status}")

    return False


def deep_diff(a, b, path: str) -> list[dict]:
    """Recursively diff two JSON structures."""
    diffs = []

    if type(a) != type(b):
        diffs.append({"path": path or "(root)", "a": a, "b": b})
        return diffs

    if isinstance(a, dict):
        all_keys = set(list(a.keys()) + list(b.keys()))
        for key in sorted(all_keys):
            child_path = f"{path}.{key}" if path else key
            if key not in a:
                diffs.append({"path": child_path, "a": "(missing)", "b": b[key]})
            elif key not in b:
                diffs.append({"path": child_path, "a": a[key], "b": "(missing)"})
            else:
                diffs.extend(deep_diff(a[key], b[key], child_path))

    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append({"path": f"{path}.length", "a": len(a), "b": len(b)})
        for i in range(min(len(a), len(b))):
            diffs.extend(deep_diff(a[i], b[i], f"{path}[{i}]"))

    elif a != b:
        # For floats, allow small tolerance
        if isinstance(a, float) and isinstance(b, float):
            if abs(a - b) > 1e-10:
                diffs.append({"path": path, "a": a, "b": b})
        else:
            diffs.append({"path": path, "a": a, "b": b})

    return diffs


def main():
    parser = argparse.ArgumentParser(description="Snapshot testing for JS refactoring")
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="Capture a snapshot")
    cap.add_argument("--name", required=True, help="Snapshot name")
    cap.add_argument("--script", required=True, help="Node.js script that outputs JSON")
    cap.add_argument("--output", default="snapshots", help="Output directory")

    cmp = sub.add_parser("compare", help="Compare two snapshots")
    cmp.add_argument("snapshot_a", help="First snapshot JSON")
    cmp.add_argument("snapshot_b", help="Second snapshot JSON")
    cmp.add_argument("--verbose", "-v", action="store_true")

    diff = sub.add_parser("diff", help="Capture and compare against baseline")
    diff.add_argument("--name", required=True, help="Snapshot name")
    diff.add_argument("--script", required=True, help="Node.js script")
    diff.add_argument("--baseline", required=True, help="Baseline snapshot to compare against")
    diff.add_argument("--output", default="snapshots", help="Output directory")

    args = parser.parse_args()

    if args.command == "capture":
        capture_snapshot(args.script, args.name, args.output)
        return 0

    elif args.command == "compare":
        ok = compare_snapshots(args.snapshot_a, args.snapshot_b, getattr(args, "verbose", False))
        return 0 if ok else 1

    elif args.command == "diff":
        capture_snapshot(args.script, args.name, args.output)
        snapshot_path = Path(args.output) / f"{args.name}.json"
        ok = compare_snapshots(args.baseline, str(snapshot_path))
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
