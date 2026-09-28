#!/usr/bin/env python3
"""Post a templated comment on a GitHub issue or PR, or open an issue from a template.

Usage:
    python gh-comment.py --repo owner/repo --issue 3 --template closing \\
        --var pr=33 \\
        --var summary="Colour cycling now shared between both renderers" \\
        --var files="render.js, colours.js" \\
        --var verify="Render, then toggle Colour Cycle"

    python gh-comment.py --repo owner/repo --issue 4 --template superseded \\
        --var issue=15 --var reason="The GPU approach replaces this"

    python gh-comment.py --repo owner/repo --create "Julia mode" --template spec --var goal="..."

    # Preview without posting:
    python gh-comment.py --template spec --var goal="Add Julia mode" --dry-run

    # List available templates:
    python gh-comment.py --list

Templates live in templates/comment-templates/; a name may be a unique part of
a file name. `{name}` placeholders are filled from --var in one pass, so a
value containing braces is inserted as written. A template with a
placeholder left unfilled is shown by --dry-run with a warning, and refused
for posting.

Exit status: 0 done, 1 a template problem or a failed gh call.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "comment-templates"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


class CommentError(Exception):
    """A template could not be used, or gh failed."""


def list_templates() -> list[str]:
    """One line per template: its name and its first non-empty line."""
    lines = []
    for f in sorted(TEMPLATE_DIR.glob("*.md")):
        text = (line.strip().lstrip("#").strip() for line in f.read_text(encoding="utf-8").splitlines())
        lines.append(f"  {f.stem:20s} {next((t for t in text if t), '')}")
    return lines


def find_template(name: str) -> Path:
    """The template called NAME, or the one file whose name contains it."""
    exact = TEMPLATE_DIR / f"{name}.md"
    if exact.exists():
        return exact
    matches = sorted(TEMPLATE_DIR.glob(f"*{name}*.md"))
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise CommentError(f"Ambiguous template '{name}'. Matches: "
                           + ", ".join(m.stem for m in matches))
    raise CommentError(f"Template '{name}' not found. Use --list to see available.")


def fill(content: str, variables: dict[str, str]) -> tuple[str, list[str]]:
    """(CONTENT with each known {name} replaced, the names left unfilled)."""
    unfilled: list[str] = []

    def one(m: re.Match[str]) -> str:
        if m.group(1) in variables:
            return variables[m.group(1)]
        unfilled.append(m.group(1))
        return m.group(0)

    return PLACEHOLDER.sub(one, content), unfilled


def gh(*args: str) -> str:
    """Run gh and return its stdout. Raises CommentError with gh's own message."""
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise CommentError(f"gh {args[0]} {args[1]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", help="owner/repo")
    parser.add_argument("--issue", type=int, help="Issue or PR number (for commenting)")
    parser.add_argument("--create", metavar="TITLE", help="Create a new issue with this title")
    parser.add_argument("--label", action="append", default=[], help="Labels for --create")
    parser.add_argument("--template", "-t", help="Template name")
    parser.add_argument("--var", action="append", default=[], help="key=value pairs")
    parser.add_argument("--dry-run", action="store_true", help="Preview without posting")
    parser.add_argument("--close", action="store_true", help="Also close the issue after commenting")
    parser.add_argument("--list", action="store_true", help="List available templates")
    return parser


def _variables(parser: argparse.ArgumentParser, pairs: list[str]) -> dict[str, str]:
    bad = [p for p in pairs if "=" not in p]
    if bad:
        parser.error(f"Invalid --var format: {bad[0]} (use key=value)")
    return dict(p.split("=", 1) for p in pairs)


def _post(args: argparse.Namespace, body: str) -> None:
    if args.create:
        labels = [x for label in args.label for x in ("--label", label)]
        print(gh("issue", "create", "--repo", args.repo, "--title", args.create, "--body", body, *labels))
        return
    print(gh("issue", "comment", str(args.issue), "--repo", args.repo, "--body", body))
    if args.close:
        gh("issue", "close", str(args.issue), "--repo", args.repo)
        print(f"Closed #{args.issue}")


def _require(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not args.template:
        parser.error("--template is required (or use --list)")
    if not args.dry_run and (not args.repo or not (args.create or args.issue)):
        parser.error("--repo and one of --issue or --create are required (or use --dry-run)")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(list_templates()) or "No templates found")
        return 0
    _require(parser, args)
    try:
        body, unfilled = fill(find_template(args.template).read_text(encoding="utf-8"),
                              _variables(parser, args.var))
        if unfilled:
            print(f"Warning: unfilled placeholders: {', '.join(unfilled)}", file=sys.stderr)
        if args.dry_run:
            print(f"--- PREVIEW ---\n{body}\n--- END PREVIEW ---")
            return 0
        if unfilled:
            raise CommentError("not posting a comment with unfilled placeholders")
        _post(args, body)
    except CommentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
