#!/usr/bin/env python3
"""Deploy a project to S3 and GitHub Pages in one command.

Usage:
    python deploy-all.py <project-dir> <s3-bucket> [--dry-run] [--skip-s3] [--skip-pages]

Steps:
    1. Refuse unless the project is on master or main, with a clean tree
    2. Merge that branch into gh-pages (when a gh-pages branch exists) and push it
    3. Deploy the project to the S3 bucket with deploy-s3.py
    4. Always end back on the branch it started from, whatever failed

Exit status: 0 deployed, 1 a refusal or any failed step.
"""

import argparse
import subprocess
import sys
from pathlib import Path


class StepFailedError(Exception):
    """A git command or the S3 deploy failed; the message says which and why."""


def git(project: Path, *args: str) -> str:
    """Run git in PROJECT and return its stdout. Raises StepFailedError."""
    r = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise StepFailedError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def _refusal(project: Path) -> str | None:
    """Why this project cannot be deployed as it stands, or None."""
    if not (project / ".git").exists():
        return f"{project} is not a git repository"
    branch = git(project, "branch", "--show-current")
    if branch not in ("master", "main"):
        return f"on branch '{branch}', expected master or main"
    status = git(project, "status", "--porcelain")
    return f"working directory not clean:\n{status}" if status else None


def _has_branch(project: Path, name: str) -> bool:
    return subprocess.run(["git", "-C", str(project), "show-ref", "--verify", "--quiet",
                           f"refs/heads/{name}"], check=False).returncode == 0


def sync_pages(project: Path, main_branch: str) -> str:
    """Merge MAIN_BRANCH into gh-pages and push it. Returns what happened.

    The checkout of MAIN_BRANCH afterwards runs whether the merge or push
    succeeded or not, so a failure never leaves the project on gh-pages.
    """
    if not _has_branch(project, "gh-pages"):
        return "No gh-pages branch, skipping"
    git(project, "checkout", "gh-pages")
    try:
        git(project, "merge", main_branch, "--no-edit")
        git(project, "push")
    finally:
        git(project, "checkout", main_branch)
    return "gh-pages synced"


def deploy_s3(project: Path, bucket: str) -> None:
    script = Path(__file__).resolve().parent / "deploy-s3.py"
    r = subprocess.run([sys.executable, str(script), str(project), bucket], cwd=project, check=False)
    if r.returncode != 0:
        raise StepFailedError(f"deploy-s3.py exited {r.returncode}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", help="Path to the project directory")
    parser.add_argument("bucket", help="S3 bucket name")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    parser.add_argument("--skip-s3", action="store_true", help="Skip S3 deploy")
    parser.add_argument("--skip-pages", action="store_true", help="Skip GitHub Pages sync")
    return parser


def _deploy(args: argparse.Namespace, project: Path, main_branch: str) -> None:
    print("Syncing gh-pages..." if not args.skip_pages else "  Skipping gh-pages (--skip-pages)")
    if not args.skip_pages:
        print(f"  {sync_pages(project, main_branch)}")
    if args.skip_s3:
        print("  Skipping S3 (--skip-s3)")
    else:
        print(f"Deploying to s3://{args.bucket}/...")
        deploy_s3(project, args.bucket)
        print("  S3 deployed")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project = Path(args.project).resolve()
    try:
        refusal = _refusal(project)
        if refusal:
            print(f"Error: {refusal}", file=sys.stderr)
            return 1
        main_branch = git(project, "branch", "--show-current")
        print(f"Deploying {project.name} from {main_branch}")
        if args.dry_run:
            print(f"  [dry-run] Would sync gh-pages to {main_branch}")
            print(f"  [dry-run] Would deploy to s3://{args.bucket}/")
            return 0
        _deploy(args, project, main_branch)
    except StepFailedError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"\nDone. {project.name} deployed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
