#!/usr/bin/env python3
"""Deploy a local directory to an S3 bucket for static site hosting.

Usage:
    python deploy-s3.py <local-dir> <bucket-name> [--dry-run] [--delete]

Options:
    --dry-run               Show what would be synced without uploading
    --delete                Remove files from the bucket that do not exist locally
    --exclude PATTERN       Another glob to leave out (repeatable, e.g. --exclude ".git/*")
    --no-default-excludes   Do not leave out the defaults below

By default the repository's own furniture is never deployed: .git, .github,
node_modules, docs, tests, markdown, editor and package-manager files. An
excluded path is also exempt from --delete, so a file already in the bucket
under an excluded name stays there.

Examples:
    python deploy-s3.py ./site example.com --dry-run
    python deploy-s3.py ./site example.com --delete

Exit status: 0 deployed, 1 the source is not a directory or the sync
failed, 2 the AWS CLI is missing or has no credentials.
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_EXCLUDES = [
    ".git/*", ".github/*", "node_modules/*", "__pycache__/*", "*.code-workspace",
    ".vscode/*", ".gitignore", ".gitattributes", "docs/*", "tests/*", "screencaps/*",
    "*.md", "eslint.config.js", ".prettierignore", "package.json", "package-lock.json",
    "composer.json", "composer.lock",
]

# Where the Windows installer puts the AWS CLI when it is not on PATH.
_WINDOWS_AWS = Path("C:/Program Files/Amazon/AWSCLIV2/aws.exe")


class DeployError(Exception):
    """Could not deploy. `code` is the exit status."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def find_aws() -> str | None:
    """The AWS CLI binary, or None."""
    for name in ("aws", "aws.exe"):
        path = shutil.which(name)
        if path:
            return path
    return str(_WINDOWS_AWS) if _WINDOWS_AWS.exists() else None


def run_aws(args: list[str]) -> subprocess.CompletedProcess[str]:
    aws = find_aws()
    if not aws:
        raise DeployError("AWS CLI not found. Install with: winget install Amazon.AWSCLI "
                          "(Windows) or your package manager", 2)
    return subprocess.run([aws, *args], capture_output=True, text=True, check=False)


def identity() -> str:
    """The ARN the AWS CLI deploys as. Raises DeployError without credentials."""
    result = run_aws(["sts", "get-caller-identity"])
    if result.returncode != 0:
        raise DeployError(f"AWS credentials not configured. Run: aws configure\n{result.stderr}", 2)
    try:
        return str(json.loads(result.stdout)["Arn"])
    except (ValueError, KeyError) as exc:
        raise DeployError(f"unexpected sts output: {result.stdout[:200]}", 2) from exc


def sync_args(source: Path, bucket: str, excludes: list[str], delete: bool, dry_run: bool) -> list[str]:
    """The `aws s3 sync` arguments."""
    args = ["s3", "sync", str(source), f"s3://{bucket}/"]
    for pattern in excludes:
        args += ["--exclude", pattern]
    if delete:
        args.append("--delete")
    if dry_run:
        args.append("--dryrun")
    return args


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="Local directory to deploy")
    parser.add_argument("bucket", help="S3 bucket name (e.g. example.com)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without uploading")
    parser.add_argument("--delete", action="store_true",
                        help="Remove files from bucket that don't exist locally")
    parser.add_argument("--exclude", action="append", default=[],
                        help="Additional glob patterns to exclude (repeatable)")
    parser.add_argument("--no-default-excludes", action="store_true",
                        help="Don't apply default exclude patterns")
    return parser


def deploy(args: argparse.Namespace) -> None:
    """Sync the source to the bucket. Raises DeployError."""
    source = Path(args.source).resolve()
    if not source.is_dir():
        raise DeployError(f"{source} is not a directory", 1)
    print(f"Deploying as: {identity()}")
    print(f"Source: {source}")
    print(f"Target: s3://{args.bucket}/")
    print()
    excludes = ([] if args.no_default_excludes else list(DEFAULT_EXCLUDES)) + args.exclude
    print(f"--- {'DRY RUN' if args.dry_run else 'DEPLOYING'} ---")
    if args.delete:
        print("WARNING: --delete is set: files not in the source will be removed from the bucket")
    print()
    result = run_aws(sync_args(source, args.bucket, excludes, args.delete, args.dry_run))
    print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        raise DeployError(f"Deploy failed with exit code {result.returncode}", 1)
    if not args.dry_run:
        print(f"\nDeploy complete: s3://{args.bucket}/")
        print(f"Timestamp: {datetime.now().astimezone().isoformat()}")


def main(argv: list[str] | None = None) -> int:
    try:
        deploy(_parser().parse_args(argv))
    except DeployError as exc:
        print(f"Error: {exc}")
        return exc.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
