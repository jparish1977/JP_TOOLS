#!/usr/bin/env python3
"""Install GitHub issue templates into a target repository.

Copies templates/issue-templates/*.yml into <repo>/.github/ISSUE_TEMPLATE/.
Refuses when templates are already there, unless --force.

Exit status: 0 installed, 1 not a git repository or templates already present.
"""

import argparse
import shutil
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "templates" / "issue-templates"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install issue templates into a repo")
    parser.add_argument("target", help="Path to the target repository")
    parser.add_argument("--force", action="store_true", help="Overwrite existing templates")
    args = parser.parse_args(argv)
    target = Path(args.target).resolve()
    if not (target / ".git").exists():
        print(f"Error: {target} is not a git repository")
        return 1
    dest = target / ".github" / "ISSUE_TEMPLATE"
    existing = sorted(dest.glob("*.yml")) if dest.exists() else []
    if existing and not args.force:
        print(f"Templates already exist at {dest}:")
        for f in existing:
            print(f"  {f.name}")
        print("Use --force to overwrite")
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    installed = sorted(t.name for t in SOURCE.glob("*.yml"))
    for name in installed:
        shutil.copy2(SOURCE / name, dest / name)
    print(f"Installed {len(installed)} templates at {dest}:")
    for name in installed:
        print(f"  {name}")
    print("\nCommit and push to activate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
