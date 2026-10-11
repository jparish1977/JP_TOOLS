#!/usr/bin/env python3
"""Who names one of this repo's lab tools from outside tools/lab?

    python3 tools/lab/lab-use-sweep.py            # every repo under ~/projects

ANSWERS: for each file in tools/lab/ (not its README), every tracked file in
  every git repo under ~/projects that names it by file name (this repo's
  tools/lab/ and tests/ excepted), and every Claude memory file under
  ~/.claude/projects/*/memory. METHODOLOGY 2.9: a lab tool that grows a
  dependency needs promotion. This shows the candidates; a person tells a
  dependency (something runs it and relies on its answer) from a mention (a
  record that it was run) by reading each hit. On 2026-10-10 it found
  i8-proof.sh and run-suites.sh carrying every merge (#218) and no non-lab code
  in JP_TOOLS running any lab tool (#219 is the check that should say so).

TESTED AGAINST: the Air, 2026-10-10, 20 lab tools: every hit read by hand.
  Positive control: i8-proof.sh, known to be named by this seat's checklist
  and memories, was found there.

NOT TESTED AGAINST: names that are a substring of an unrelated word (it
  matches the bare file name, so it over-reports; a false hit costs a look);
  repos not cloned under ~/projects; uses that build the path at run time; files
  over 2 MB, which it skips. Memory directories are symlinks into
  claude-config, so one memory can be listed under more than one path.
"""
import subprocess
import sys
from pathlib import Path

# The repo root, so platformtools imports from here.
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from platformtools.procname import name_process

MAX_BYTES = 2_000_000


def lab_tools() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "tools/lab/*"],
                         capture_output=True, text=True, check=True).stdout.split()
    return sorted(Path(p).name for p in out if not p.endswith("README.md"))


def tracked(repo: Path) -> list[str]:
    r = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, text=True,
                       check=False)
    return [f for f in r.stdout.split("\0") if f] if r.returncode == 0 else []


def text_of(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        # reason: an unreadable file is one this sweep cannot see into; it is skipped, as stated in NOT TESTED
        return ""


def main() -> int:
    name_process("lab-use-sweep")
    names = lab_tools()
    print(f"{len(names)} lab tools:", " ".join(names))
    found: dict[str, list[str]] = {}
    projects = Path.home() / "projects"
    for repo in sorted(projects.iterdir()):
        if not (repo / ".git").exists():
            continue
        for rel in tracked(repo):
            if repo.resolve() == REPO and rel.startswith(("tools/lab/", "tests/")):
                continue
            text = text_of(repo / rel)
            for n in names:
                if n in text:
                    found.setdefault(n, []).append(f"{repo.name}/{rel}")
    for mem in (Path.home() / ".claude" / "projects").glob("*/memory/*.md"):
        text = text_of(mem)
        for n in names:
            if n in text:
                found.setdefault(n, []).append(f"memory:{mem.parent.parent.name}/{mem.name}")
    for n in names:
        where = found.get(n, [])
        print(f"\n{n}: {len(where)} place(s) outside the lab")
        for w in where:
            print("   ", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
