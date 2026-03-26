#!/usr/bin/env python3
"""Deploy a project to S3 + GitHub Pages in one command.

Usage:
    python deploy-all.py <project-dir> <s3-bucket> [--dry-run]

Steps:
    1. Verify on master/main branch and clean
    2. Sync gh-pages branch to master
    3. Deploy to S3 bucket
    4. Return to master
"""

import argparse
import subprocess
import sys
import os
from pathlib import Path


def run(cmd, cwd=None, check=True):
    """Run a command and return output."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, cwd=cwd
    )
    if check and result.returncode != 0:
        print(f"Error running: {cmd}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result


def get_branch(cwd):
    return run("git branch --show-current", cwd=cwd).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description="Deploy to S3 + GitHub Pages")
    parser.add_argument("project", help="Path to the project directory")
    parser.add_argument("bucket", help="S3 bucket name")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    parser.add_argument("--skip-s3", action="store_true", help="Skip S3 deploy")
    parser.add_argument("--skip-pages", action="store_true", help="Skip GitHub Pages sync")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    if not (project / ".git").exists():
        print(f"Error: {project} is not a git repository", file=sys.stderr)
        return 1

    # Step 0: Verify clean state on master/main
    branch = get_branch(project)
    if branch not in ("master", "main"):
        print(f"Error: on branch '{branch}', expected master or main", file=sys.stderr)
        return 1

    status = run("git status --porcelain", cwd=project).stdout.strip()
    if status:
        print(f"Error: working directory not clean:\n{status}", file=sys.stderr)
        return 1

    main_branch = branch
    print(f"Deploying {project.name} from {main_branch}")

    if args.dry_run:
        print("  [dry-run] Would sync gh-pages to", main_branch)
        print(f"  [dry-run] Would deploy to s3://{args.bucket}/")
        return 0

    # Step 1: Sync gh-pages
    if not args.skip_pages:
        print("Syncing gh-pages...")
        # Check if gh-pages exists
        branches = run("git branch", cwd=project).stdout
        if "gh-pages" in branches:
            run("git checkout gh-pages", cwd=project)
            result = run(f"git merge {main_branch} --no-edit", cwd=project, check=False)
            if result.returncode != 0:
                print("Error merging to gh-pages. Returning to", main_branch, file=sys.stderr)
                run(f"git checkout {main_branch}", cwd=project)
                return 1
            run("git push", cwd=project)
            run(f"git checkout {main_branch}", cwd=project)
            print("  gh-pages synced")
        else:
            print("  No gh-pages branch, skipping")
    else:
        print("  Skipping gh-pages (--skip-pages)")

    # Step 2: Deploy to S3
    if not args.skip_s3:
        print(f"Deploying to s3://{args.bucket}/...")
        deploy_script = Path(__file__).parent / "deploy-s3.py"
        result = run(
            f'python "{deploy_script}" "{project}" {args.bucket}',
            cwd=project
        )
        print("  S3 deployed")
    else:
        print("  Skipping S3 (--skip-s3)")

    print(f"\nDone. {project.name} deployed to:")
    if not args.skip_pages:
        print(f"  GitHub Pages: gh-pages branch pushed")
    if not args.skip_s3:
        print(f"  S3: s3://{args.bucket}/")

    return 0


if __name__ == "__main__":
    sys.exit(main())
