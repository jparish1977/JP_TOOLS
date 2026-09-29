#!/usr/bin/env python3
"""How many linter suppression directives across a set of repos give no reason?

ANSWERS: before the directive-reason rule reaches a repo, which of its tracked
  shell, C/C++, PHP, JS, CSS and HTML files hold a suppression directive with
  no reason on its line (what the rule will fail), per repo, with the file and
  line of each. Uses the rule's own parser, jpcheck/directives.py, so the
  count is what the gate will say, not an approximation of it.

TESTED AGAINST: the 50 git repos under ~/projects on a laptop, 2026-09-29: 24
  directives with no reason in 9 repos (8 shellcheck disable, 9 phpcs, 7
  eslint-disable; none of the others), 0 files unread. Five read by eye, in
  five repos and three kinds of directive, were each a real directive with no
  reason on the line.
  Positive control: a planted `# shellcheck disable=SC2086` with no reason is
  what tests/test_check_directive_reason.py fails the gate on.

NOT TESTED AGAINST: files that are not tracked (the gate checks what is
  committed), and repos not cloned on this box. Language is by suffix, plus
  the #! line of an extensionless file, as check.py detects it.

usage: tools/lab/directive-sweep.py [REPOS_DIR]   (default ~/projects)
  Run from a JP_TOOLS checkout root.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jpcheck.directives import directives
from jpcheck.files import _detect_lang


def _name_process(name: bytes) -> None:
    """Name the process (what top and btop show) instead of python3."""
    try:
        import ctypes
        ctypes.CDLL(None).prctl(15, name, 0, 0, 0)
    except (OSError, AttributeError):
        # reason: no libc or no prctl: the process runs unnamed, which is never fatal
        pass


def tracked(repo: Path) -> list[str]:
    """Tracked files, NUL-separated so a name with a space stays whole."""
    r = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"],
                       capture_output=True, text=True, check=False)
    return [n for n in r.stdout.split("\0") if n]


def unreasoned_in(repo: Path, unread: list[str]) -> list[str]:
    """'file:line name' for each directive with no reason in REPO's tracked files."""
    out = []
    for rel in tracked(repo):
        path = repo / rel
        lang = _detect_lang(str(path))
        if lang in ("unknown", "python"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            unread.append(f"{repo.name}/{rel} ({exc.__class__.__name__})")
            continue
        out += [f"{rel}:{d['line']} {d['name']}" for d in directives(text, lang)
                if not d["reasoned"]]
    return out


def main(argv: list[str]) -> int:
    base = Path(argv[0]).expanduser() if argv else Path.home() / "projects"
    repos = [p.parent for p in sorted(base.glob("*/.git"))]
    unread: list[str] = []
    total = 0
    for repo in repos:
        found = unreasoned_in(repo, unread)
        if found:
            total += len(found)
            print(f"{repo.name}: {len(found)}")
            print("\n".join(f"    {f}" for f in found))
    for u in unread:
        print(f"UNREAD {u}")
    print(f"{len(repos)} repo(s), {total} directive(s) with no reason, "
          f"{len(unread)} tracked file(s) unread")
    return 1 if unread else 0


if __name__ == "__main__":
    _name_process(b"directive-sweep")
    sys.exit(main(sys.argv[1:]))
