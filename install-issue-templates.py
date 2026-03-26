#!/usr/bin/env python3
"""Install GitHub issue templates into a target repository."""

import argparse
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Install issue templates into a repo")
    parser.add_argument("target", help="Path to the target repository")
    parser.add_argument("--force", action="store_true", help="Overwrite existing templates")
    args = parser.parse_args()

    target = Path(args.target).resolve()
    if not (target / ".git").exists():
        print(f"Error: {target} is not a git repository")
        return 1

    source = Path(__file__).parent / "templates" / "issue-templates"
    dest = target / ".github" / "ISSUE_TEMPLATE"

    if dest.exists() and not args.force:
        existing = list(dest.glob("*.yml"))
        if existing:
            print(f"Templates already exist at {dest}:")
            for f in existing:
                print(f"  {f.name}")
            print("Use --force to overwrite")
            return 1

    dest.mkdir(parents=True, exist_ok=True)

    installed = []
    for template in source.glob("*.yml"):
        shutil.copy2(template, dest / template.name)
        installed.append(template.name)

    print(f"Installed {len(installed)} templates at {dest}:")
    for name in sorted(installed):
        print(f"  {name}")
    print("\nCommit and push to activate.")
    return 0


if __name__ == "__main__":
    exit(main())
