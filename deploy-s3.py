#!/usr/bin/env python
"""
JP_TOOLS/deploy-s3.py
Deploy a local directory to an S3 bucket for static site hosting.

Usage:
    python deploy-s3.py <local-dir> <bucket-name> [--dry-run] [--delete]

Options:
    --dry-run   Show what would be synced without uploading
    --delete    Remove files from bucket that don't exist locally
    --exclude   Glob patterns to exclude (repeatable, e.g. --exclude ".git/*")

Examples:
    python deploy-s3.py ./mandelbrotexplorer iteration8.com --dry-run
    python deploy-s3.py ./mandelbrotexplorer iteration8.com --delete
"""

import os
import sys
import json
import shutil
import subprocess
import argparse
from pathlib import Path
from datetime import datetime


# Default excludes — never deploy these
DEFAULT_EXCLUDES = [
    ".git/*",
    ".github/*",
    "node_modules/*",
    "__pycache__/*",
    "*.code-workspace",
    ".vscode/*",
    ".gitignore",
    ".gitattributes",
    "docs/*",
    "tests/*",
    "screencaps/*",
    "*.md",
    "eslint.config.js",
    ".prettierignore",
    "package.json",
    "package-lock.json",
    "composer.json",
    "composer.lock",
]


def find_aws():
    """Find the AWS CLI binary."""
    for name in ("aws", "aws.exe"):
        path = shutil.which(name)
        if path:
            return path
    # Common Windows install location
    win_path = Path("C:/Program Files/Amazon/AWSCLIV2/aws.exe")
    if win_path.exists():
        return str(win_path)
    return None


def run_aws(args, dry_run=False):
    """Run an AWS CLI command and return the result."""
    aws = find_aws()
    if not aws:
        print("Error: AWS CLI not found. Install with: winget install Amazon.AWSCLI")
        sys.exit(2)

    cmd = [aws] + args
    if dry_run:
        # For s3 sync, --dryrun is the flag
        if "sync" in args:
            cmd.append("--dryrun")

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Deploy a local directory to an S3 bucket.",
    )
    parser.add_argument("source", help="Local directory to deploy")
    parser.add_argument("bucket", help="S3 bucket name (e.g. iteration8.com)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without uploading")
    parser.add_argument("--delete", action="store_true",
                        help="Remove files from bucket that don't exist locally")
    parser.add_argument("--exclude", action="append", default=[],
                        help="Additional glob patterns to exclude (repeatable)")
    parser.add_argument("--no-default-excludes", action="store_true",
                        help="Don't apply default exclude patterns")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.is_dir():
        print(f"Error: {source} is not a directory")
        sys.exit(1)

    bucket = args.bucket
    s3_url = f"s3://{bucket}/"

    # Verify AWS credentials
    result = run_aws(["sts", "get-caller-identity"])
    if result.returncode != 0:
        print("Error: AWS credentials not configured. Run: aws configure")
        print(result.stderr)
        sys.exit(2)

    identity = json.loads(result.stdout)
    print(f"Deploying as: {identity['Arn']}")
    print(f"Source: {source}")
    print(f"Target: {s3_url}")
    print()

    # Build exclude list
    excludes = [] if args.no_default_excludes else list(DEFAULT_EXCLUDES)
    excludes.extend(args.exclude)

    # Build the sync command
    sync_args = ["s3", "sync", str(source), s3_url]
    for pattern in excludes:
        sync_args.extend(["--exclude", pattern])

    if args.delete:
        sync_args.append("--delete")

    # Show what we're doing
    mode = "DRY RUN" if args.dry_run else "DEPLOYING"
    print(f"--- {mode} ---")
    if args.delete:
        print("WARNING: --delete flag set — files not in source will be removed from bucket")
    print()

    result = run_aws(sync_args, dry_run=args.dry_run)
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if result.returncode != 0:
        print(f"\nDeploy failed with exit code {result.returncode}")
        sys.exit(1)

    if not args.dry_run:
        print(f"\nDeploy complete: {s3_url}")
        print(f"Timestamp: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
