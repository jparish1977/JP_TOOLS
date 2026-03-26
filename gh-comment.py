#!/usr/bin/env python3
"""Post a templated comment on a GitHub issue or PR.

Usage:
    python gh-comment.py --repo jparish1977/mandelbrotexplorer --issue 3 --template closing \\
        --var pr_number=33 \\
        --var summary="Hair now supports color cycling via shared processEscapePathShared" \\
        --var file_list="cloudGeneration.js, mandelbrotexplorer.js" \\
        --var verification_steps="Generate Hair, toggle Color Cycle checkbox"

    python gh-comment.py --repo jparish1977/mandelbrotexplorer --issue 4 --template superseded \\
        --var new_issue_number=15 \\
        --var reason="GPU shader approach is orders of magnitude faster than web workers"

    # Preview without posting:
    python gh-comment.py --template spec --var goal="Add Julia mode" --dry-run

    # List available templates:
    python gh-comment.py --list
"""

import argparse
import subprocess
import sys
from pathlib import Path


TEMPLATE_DIR = Path(__file__).parent / "templates" / "comment-templates"


def list_templates():
    """List available comment templates."""
    if not TEMPLATE_DIR.exists():
        print("No templates found", file=sys.stderr)
        return

    for f in sorted(TEMPLATE_DIR.glob("*.md")):
        name = f.stem
        # Read first non-empty line as description
        first_line = ""
        for line in f.read_text(encoding="utf-8").splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                first_line = stripped
                break
        print(f"  {name:20s} {first_line}")


def fill_template(template_name: str, variables: dict) -> str:
    """Fill a template with variables."""
    template_path = TEMPLATE_DIR / f"{template_name}.md"
    if not template_path.exists():
        # Try partial match
        matches = list(TEMPLATE_DIR.glob(f"*{template_name}*.md"))
        if len(matches) == 1:
            template_path = matches[0]
        elif len(matches) > 1:
            print(f"Ambiguous template '{template_name}'. Matches:", file=sys.stderr)
            for m in matches:
                print(f"  {m.stem}", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"Template '{template_name}' not found. Use --list to see available.", file=sys.stderr)
            sys.exit(1)

    content = template_path.read_text(encoding="utf-8")

    for key, value in variables.items():
        content = content.replace(f"{{{key}}}", value)

    # Warn about unfilled placeholders
    import re
    unfilled = re.findall(r'\{(\w+)\}', content)
    if unfilled:
        print(f"Warning: unfilled placeholders: {', '.join(unfilled)}", file=sys.stderr)

    return content


def post_comment(repo: str, issue_number: int, body: str):
    """Post a comment via gh CLI."""
    result = subprocess.run(
        ["gh", "issue", "comment", str(issue_number),
         "--repo", repo,
         "--body", body],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Error posting comment: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(result.stdout.strip())


def close_issue(repo: str, issue_number: int):
    """Close an issue via gh CLI."""
    result = subprocess.run(
        ["gh", "issue", "close", str(issue_number), "--repo", repo],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Error closing issue: {result.stderr}", file=sys.stderr)
    else:
        print(f"Closed #{issue_number}")


def main():
    parser = argparse.ArgumentParser(description="Post templated GitHub comments")
    parser.add_argument("--repo", help="owner/repo")
    parser.add_argument("--issue", type=int, help="Issue or PR number")
    parser.add_argument("--template", "-t", help="Template name")
    parser.add_argument("--var", action="append", default=[], help="key=value pairs")
    parser.add_argument("--dry-run", action="store_true", help="Preview without posting")
    parser.add_argument("--close", action="store_true", help="Also close the issue after commenting")
    parser.add_argument("--list", action="store_true", help="List available templates")
    args = parser.parse_args()

    if args.list:
        list_templates()
        return 0

    if not args.template:
        parser.error("--template is required (or use --list)")

    # Parse variables
    variables = {}
    for var in args.var:
        if "=" not in var:
            parser.error(f"Invalid --var format: {var} (use key=value)")
        key, value = var.split("=", 1)
        variables[key] = value

    body = fill_template(args.template, variables)

    if args.dry_run:
        print("--- PREVIEW ---")
        print(body)
        print("--- END PREVIEW ---")
        return 0

    if not args.repo or not args.issue:
        parser.error("--repo and --issue are required (or use --dry-run)")

    post_comment(args.repo, args.issue, body)

    if args.close:
        close_issue(args.repo, args.issue)

    return 0


if __name__ == "__main__":
    sys.exit(main())
