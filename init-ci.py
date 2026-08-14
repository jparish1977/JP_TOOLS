#!/usr/bin/env python
"""
JP_TOOLS/init-ci.py
Copy the GitHub Actions CI template into a project's .github/workflows/ directory.

Usage:
    python init-ci.py [path-to-repo]    # defaults to cwd
"""

import argparse
import shutil
import sys
from pathlib import Path

JP_TOOLS_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = JP_TOOLS_ROOT / "templates"
METHODOLOGY_DOC = JP_TOOLS_ROOT / "METHODOLOGY.md"


def main() -> None:
    parser = argparse.ArgumentParser(description="Install JP_TOOLS CI workflow into a repo")
    parser.add_argument("path", nargs="?", default=".",
                        help="Path to git repository (default: cwd)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing workflow file")
    args = parser.parse_args()

    repo = Path(args.path).resolve()
    if not (repo / ".git").is_dir():
        print(f"Error: {repo} is not a git repository")
        sys.exit(1)

    src = TEMPLATES_DIR / "ci-check.yml"
    if not src.exists():
        print(f"Error: template not found at {src}")
        sys.exit(1)

    dest_dir = repo / ".github" / "workflows"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "check.yml"

    if dest.exists() and not args.force:
        print(f"Workflow already exists at {dest}")
        print("Use --force to overwrite.")
        sys.exit(1)

    shutil.copy2(src, dest)
    print(f"Installed CI workflow at {dest}")
    print("Commit and push to activate.")
    print()
    print("This project now follows JP_TOOLS methodology.")
    print(f"Read it: {METHODOLOGY_DOC}")


if __name__ == "__main__":
    main()
