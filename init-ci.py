#!/usr/bin/env python
"""
JP_TOOLS/init-ci.py
Copy the GitHub Actions CI templates into a project's .github/workflows/ directory:
check.yml (checks and tests on every push and PR) and mutmut-nightly.yml (the
mutation state check.yml's mutation jobs read; it does nothing until the repo
opts in, see check.yml's header).

Usage:
    python init-ci.py [path-to-repo]    # defaults to cwd

A workflow already there is left alone without --force, and the others are
still installed, so re-running it on an adopted repo adds what is new. Exit 1
when any was left alone or the path is not a repository.
"""

import argparse
import shutil
import sys
from pathlib import Path

JP_TOOLS_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = JP_TOOLS_ROOT / "templates"
METHODOLOGY_DOC = JP_TOOLS_ROOT / "METHODOLOGY.md"
# Template name -> the workflow file it becomes.
WORKFLOWS = {"ci-check.yml": "check.yml", "mutmut-nightly.yml": "mutmut-nightly.yml"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Install JP_TOOLS CI workflows into a repo")
    parser.add_argument("path", nargs="?", default=".",
                        help="Path to git repository (default: cwd)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing workflow files")
    args = parser.parse_args()

    repo = Path(args.path).resolve()
    if not (repo / ".git").is_dir():
        print(f"Error: {repo} is not a git repository")
        sys.exit(1)

    missing = [name for name in WORKFLOWS if not (TEMPLATES_DIR / name).exists()]
    if missing:
        print(f"Error: template(s) not found in {TEMPLATES_DIR}: {', '.join(missing)}")
        sys.exit(1)

    dest_dir = repo / ".github" / "workflows"
    dest_dir.mkdir(parents=True, exist_ok=True)
    left = []
    for name, workflow in WORKFLOWS.items():
        dest = dest_dir / workflow
        if dest.exists() and not args.force:
            print(f"Workflow already exists at {dest}: left alone (--force overwrites it)")
            left.append(workflow)
            continue
        shutil.copy2(TEMPLATES_DIR / name, dest)
        print(f"Installed CI workflow at {dest}")
    if len(left) < len(WORKFLOWS):
        print("Commit and push to activate.")
        print()
        print("This project now follows JP_TOOLS methodology.")
        print(f"Read it: {METHODOLOGY_DOC}")
    if left:
        sys.exit(1)


if __name__ == "__main__":
    main()
