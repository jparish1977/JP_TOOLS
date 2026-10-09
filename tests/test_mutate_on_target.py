#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutate_on_target.py
mutate-on-target.py leaves its tree as it found it, and mutate-spec.py runs
specs at once without sharing one.

No pytest, no dependencies, same as the rest of tests/.

WHAT IS CHECKED
    - A run restores TARGET byte for byte, CRLF kept, and leaves no saved
      original behind.
    - A second run on a tree another run holds refuses, exit 2, and touches
      nothing.
    - An original left by a killed run makes the next run refuse, exit 2,
      naming it, and touches neither file.
    - A SIGTERM while a mutant is in place still restores TARGET.
    - A spec that does not anchor refuses, exit 2.
    - Two specs given to mutate-spec.py run at the same time, each in its own
      copy, leaving ROOT untouched and no copy behind.
    - One spec's mutants run at the same time too, its control only once,
      and its output file holds the control and every mutant in spec order.
      "At the same time" is shown by the runs meeting each other, never by
      wall time, which a loaded box stretches.
    - ROOT a git worktree: a mutant's `git add` stays in its own copy; the
      copy is ROOT's commit detached, in ROOT's repository with its refs and
      index; no copy's gitdir is left behind.
    - A SIGTERM to mutate-spec.py mid-run exits 143 and leaves neither a copy
      nor a copy's gitdir.
    - --only NAME (both tools) runs those mutants alone and says how many of
      the spec's ran; a name nothing has is refused, exit 2; --list names
      each mutant and its target and touches nothing.
    - mutate-spec.py: a spec it cannot read fails on its own line while the
      others still run; a name given twice, or two mutants of one name, is
      still one run per mutant.
    Not checked: exit 3, a restore that does not read back; nothing short of
    a second writer racing the restore produces it.

    MUTATE_RUNNER=path runs these against another copy of the runner, with
    mutate-spec.py beside it.

    python3 tests/test_mutate_on_target.py
"""

import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from _harness import ROOT, check, check_true, run

RUNNER = os.environ.get("MUTATE_RUNNER") or str(ROOT / "mutate-on-target.py")
ORIGINAL = b"VALUE = 1\r\nOTHER = 0\r\n"
TEST = """import sys, time
text = open("t.py", "rb").read()
if b"VALUE = 3" in text:
    time.sleep(60)
if b"VALUE = 1" in text:
    print("  ok    value is one")
    sys.exit(0)
print("  FAIL  value is one")
sys.exit(1)
"""
TMP = tempfile.mkdtemp(prefix="t-mut-on-target-")


def fixture(name: str, new: str = "VALUE = 2") -> tuple[str, str, dict[str, str]]:
    """A tree with t.py (CRLF) and test.py, a one-mutant spec, and an env
    whose cache is private to this fixture."""
    tree = os.path.join(TMP, name)
    os.makedirs(tree)
    with open(os.path.join(tree, "t.py"), "wb") as fh:
        fh.write(ORIGINAL)
    with open(os.path.join(tree, "test.py"), "w", encoding="utf-8") as fh:
        fh.write(TEST)
    spec = os.path.join(TMP, name + ".json")
    with open(spec, "w", encoding="utf-8") as fh:
        json.dump({"mutants": [{"name": "value", "edits": [["VALUE = 1", new]],
                                "target": "value is one"}]}, fh)
    env = dict(os.environ, XDG_CACHE_HOME=os.path.join(TMP, name + "-cache"))
    return tree, spec, env


def saved_path(tree: str, env: dict[str, str]) -> str:
    key = hashlib.sha256(os.path.realpath(os.path.join(tree, "t.py")).encode()).hexdigest()[:16]
    return os.path.join(env["XDG_CACHE_HOME"], "mutate-on-target", key + ".orig")


def target_bytes(tree: str) -> bytes:
    with open(os.path.join(tree, "t.py"), "rb") as fh:
        return fh.read()


def runner(tree: str, spec: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, RUNNER, tree, "t.py", "test.py", spec],
                          capture_output=True, text=True, env=env, check=False, timeout=120)


def test_a_clean_run() -> None:
    tree, spec, env = fixture("clean")
    r = runner(tree, spec, env)
    check("a clean run: exit 0, the mutant killed on target",
          (r.returncode, "every one killed on target" in r.stdout), (0, True))
    check("a clean run: TARGET restored byte for byte, CRLF kept", target_bytes(tree), ORIGINAL)
    check_true("a clean run: no saved original left behind",
               not os.path.exists(saved_path(tree, env)))


def test_a_held_tree() -> None:
    tree, spec, env = fixture("held")
    fd = os.open(tree, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        r = runner(tree, spec, env)
    finally:
        os.close(fd)
    check("a held tree: refused, exit 2, saying another run holds it",
          (r.returncode, "another mutate-on-target run holds" in r.stderr), (2, True))
    check("a held tree: TARGET untouched", target_bytes(tree), ORIGINAL)


def test_a_killed_run() -> None:
    tree, spec, env = fixture("killed")
    mutated = ORIGINAL.replace(b"VALUE = 1", b"VALUE = 2")
    with open(os.path.join(tree, "t.py"), "wb") as fh:
        fh.write(mutated)
    saved = saved_path(tree, env)
    os.makedirs(os.path.dirname(saved))
    with open(saved, "wb") as fh:
        fh.write(ORIGINAL)
    r = runner(tree, spec, env)
    check("a killed run's original: refused, exit 2, naming it",
          (r.returncode, saved in r.stderr and "killed" in r.stderr), (2, True))
    check("a killed run's original: neither file is touched",
          (target_bytes(tree), os.path.exists(saved)), (mutated, True))


def test_a_sigterm() -> None:
    tree, spec, env = fixture("term", new="VALUE = 3")
    p = subprocess.Popen([sys.executable, RUNNER, tree, "t.py", "test.py", spec],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    deadline = time.time() + 60
    while b"VALUE = 3" not in target_bytes(tree) and time.time() < deadline:
        time.sleep(0.1)
    in_place = b"VALUE = 3" in target_bytes(tree)
    time.sleep(0.5)
    p.send_signal(signal.SIGTERM)
    try:
        p.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
    check_true("a SIGTERM: the mutant was in place when the signal went", in_place)
    check("a SIGTERM: TARGET restored byte for byte", target_bytes(tree), ORIGINAL)
    check_true("a SIGTERM: no saved original left behind",
               not os.path.exists(saved_path(tree, env)))


def test_no_anchor() -> None:
    tree, spec, env = fixture("anchor")
    with open(spec, "w", encoding="utf-8") as fh:
        json.dump({"mutants": [{"name": "gone", "edits": [["NOT THERE", "x"]],
                                "target": "value is one"}]}, fh)
    r = runner(tree, spec, env)
    check("a spec that does not anchor: refused, exit 2, 'occurs 0 times'",
          (r.returncode, "occurs 0 times" in r.stderr), (2, True))


# A test for proving runs overlap without timing them: unmutated, it counts
# itself in $COUNT; mutated, it leaves its value in $MEET and waits up to 30 s
# for $MEET_N values there, so it sees them all only if they ran at once.
MEET_TEST = """import os, sys, time
text = open("t.py", "rb").read()
if b"VALUE = 1" in text:
    with open(os.environ["COUNT"], "a") as fh:
        fh.write("control\\n")
    print("  ok    value is one")
    sys.exit(0)
meet = os.environ["MEET"]
mine = text.split(b"VALUE = ")[1].split(b"\\r")[0].decode()
open(os.path.join(meet, mine), "w").close()
deadline = time.time() + 30
while len([f for f in os.listdir(meet) if "." not in f]) < int(os.environ["MEET_N"]) and time.time() < deadline:
    time.sleep(0.05)
seen = sorted(f for f in os.listdir(meet) if "." not in f)
with open(os.path.join(meet, mine + ".saw"), "w") as fh:
    fh.write(" ".join(seen))
print("  FAIL  value is one")
sys.exit(1)
"""


def meet_run(name: str, specs: list[str], tree: str, env: dict[str, str],
             n: int) -> tuple[subprocess.CompletedProcess[str], str, str]:
    """mutate-spec on SPECS with MEET_TEST in TREE, waiting for N mutants:
    (the run, the meeting dir, the control counter). --jobs N, so the N
    meet whatever the box's CPU count."""
    with open(os.path.join(tree, "test.py"), "w", encoding="utf-8") as fh:
        fh.write(MEET_TEST)
    meet, count, outdir = (os.path.join(TMP, f"{name}-{x}") for x in ("meet", "count", "out"))
    os.makedirs(meet)
    os.makedirs(outdir)
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(RUNNER), "mutate-spec.py"),
                        "--jobs", str(n), tree, *specs], capture_output=True, text=True, check=False,
                       timeout=120, env=dict(env, MUTATE_OUT_DIR=outdir, MEET=meet, MEET_N=str(n), COUNT=count))
    return r, meet, count


def saw(meet: str) -> dict[str, str]:
    """What each mutant saw in the meeting dir: {value: the values there}."""
    out = {}
    for f in os.listdir(meet):
        if f.endswith(".saw"):
            with open(os.path.join(meet, f), encoding="utf-8") as fh:
                out[f.removesuffix(".saw")] = fh.read()
    return out


def test_specs_at_once() -> None:
    tree, _, env = fixture("spec-tree")
    specs = []
    for name, new in (("one", "VALUE = 2"), ("two", "VALUE = 3")):
        sp = os.path.join(TMP, f"spec-{name}.json")
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump({"about": "Run: mutate-on-target.py TREE t.py test.py SPEC.",
                       "mutants": [{"name": name, "edits": [["VALUE = 1", new]],
                                    "target": "value is one"}]}, fh)
        specs.append(sp)
    r, meet, _ = meet_run("spec", specs, tree, env, 2)
    check("mutate-spec: both specs killed on target, exit 0",
          (r.returncode, r.stdout.count("every one killed on target")), (0, 2))
    check("mutate-spec: the two specs' mutants ran at once, each saw the other",
          saw(meet), {"2": "2 3", "3": "2 3"})
    check("mutate-spec: specs in one directory keep their file names as labels",
          sorted(line.split(":")[0] for line in r.stdout.splitlines()), ["spec-one", "spec-two"])
    check("mutate-spec: ROOT itself untouched, byte for byte", target_bytes(tree), ORIGINAL)
    check("mutate-spec: no private copy left beside ROOT",
          [d for d in os.listdir(TMP) if d.startswith(".mutate-")], [])


def test_one_spec_mutants_at_once() -> None:
    """One spec of three mutants: its control runs ONCE, and its mutants run
    at the same time, each in its own copy."""
    tree, spec, env = fixture("per-mutant")
    with open(spec, "w", encoding="utf-8") as fh:
        json.dump({"about": "Run: mutate-on-target.py TREE t.py test.py SPEC.",
                   "mutants": [{"name": f"to-{v}", "edits": [["VALUE = 1", f"VALUE = {v}"]],
                                "target": "value is one"} for v in (2, 3, 4)]}, fh)
    r, meet, count = meet_run("per-mutant", [spec], tree, env, 3)
    check("one spec: every mutant killed on target, exit 0, one control said",
          (r.returncode, "3 mutant(s), one control, every one killed on target" in r.stdout), (0, True))
    with open(count, encoding="utf-8") as fh:
        check("one spec: the control ran once, not once per mutant", fh.read(), "control\n")
    check("one spec: its three mutants ran at once, each saw all three",
          saw(meet), dict.fromkeys(("2", "3", "4"), "2 3 4"))
    check("one spec: ROOT itself untouched, byte for byte", target_bytes(tree), ORIGINAL)
    out = os.path.join(TMP, "per-mutant-out", "per-mutant.mutate.out")
    text = ""
    if os.path.exists(out):
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
    check("one spec: the output file holds the control and every mutant, in spec order",
          [ln.split()[0] for ln in text.splitlines() if ln.startswith("  ")],
          ["control", "to-2", "to-3", "to-4"])


GIT_TEST = """import os, subprocess, sys
text = open("t.py", "rb").read()
def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()
if (git("rev-parse", "HEAD") != os.environ["EXPECT_HEAD"]
        or git("symbolic-ref", "-q", "HEAD") != ""
        or git("rev-parse", "--verify", "-q", "refs/heads/other") == ""
        or os.path.realpath(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
        != os.path.realpath(os.environ["EXPECT_COMMON"])
        or "t.py" not in git("ls-files").split()):
    print("  FAIL  the copy's git: ROOT's commit detached, its repository, refs and index")
    sys.exit(1)
if b"VALUE = 1" in text:
    print("  ok    value is one")
    sys.exit(0)
mine = "new-" + text.split(b"VALUE = ")[1].split(b"\\r")[0].decode()
open(mine, "w").close()
git("add", "-N", mine)
print("  FAIL  value is one")
sys.exit(1)
"""


def test_a_worktree_copy_has_its_own_index() -> None:
    """ROOT a git worktree on a branch, whose .git is a file: a mutant's `git
    add` lands in its own copy's index, not ROOT's; the copy is ROOT's commit,
    detached (a commit there must not move ROOT's branch), in ROOT's
    repository with its refs and index; and no copy's gitdir is left."""
    repo = os.path.join(TMP, "git-repo")
    tree, spec, env = fixture("git-wt-src")
    shutil.move(tree, repo)
    with open(os.path.join(repo, "test.py"), "w", encoding="utf-8") as fh:
        fh.write(GIT_TEST)
    genv = dict(env, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

    def git(*a: str) -> str:
        return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True,
                              env=genv, check=True).stdout.strip()

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "fixture")
    git("branch", "other")
    tree = os.path.join(TMP, "git-wt")
    git("worktree", "add", "-q", "-b", "side", tree)
    with open(spec, "w", encoding="utf-8") as fh:
        json.dump({"about": "Run: mutate-on-target.py TREE t.py test.py SPEC.",
                   "mutants": [{"name": f"to-{v}", "edits": [["VALUE = 1", f"VALUE = {v}"]],
                                "target": "value is one"} for v in (2, 3)]}, fh)
    outdir = os.path.join(TMP, "git-wt-out")
    os.makedirs(outdir)
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(RUNNER), "mutate-spec.py"),
                        tree, spec], capture_output=True, text=True, check=False, timeout=120,
                       env=dict(genv, MUTATE_OUT_DIR=outdir, EXPECT_HEAD=git("rev-parse", "HEAD"),
                                EXPECT_COMMON=os.path.join(repo, ".git")))
    check("a worktree ROOT: every mutant killed on target, each copy's git as ROOT's, detached",
          (r.returncode, "every one killed on target" in r.stdout), (0, True))
    check("a worktree ROOT: no mutant's git add reached ROOT's index",
          subprocess.run(["git", "-C", tree, "ls-files"], capture_output=True, text=True,
                         env=genv, check=True).stdout.split(), ["t.py", "test.py"])
    check("a worktree ROOT: no copy's gitdir left in the repository",
          sorted(os.listdir(os.path.join(repo, ".git", "worktrees"))), ["git-wt"])


def test_spec_a_sigterm_leaves_nothing() -> None:
    """ROOT a git worktree, a mutant whose test hangs: a SIGTERM to mutate-spec
    while it runs ends the run with 143 and leaves no copy beside ROOT and no
    copy's gitdir in the repository."""
    repo = os.path.join(TMP, "term-repo")
    tree, spec, env = fixture("term-wt-src", new="VALUE = 3")
    shutil.move(tree, repo)
    genv = dict(env, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    for a in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "fixture"]):
        subprocess.run(["git", "-C", repo, *a], capture_output=True, env=genv, check=True)
    tree = os.path.join(TMP, "term-wt")
    subprocess.run(["git", "-C", repo, "worktree", "add", "-q", "-b", "side", tree],
                   capture_output=True, env=genv, check=True)
    with open(spec, "w", encoding="utf-8") as fh:
        json.dump({"about": "Run: mutate-on-target.py TREE t.py test.py SPEC.",
                   "mutants": [{"name": "hang", "edits": [["VALUE = 1", "VALUE = 3"]],
                                "target": "value is one"}]}, fh)
    outdir = os.path.join(TMP, "term-wt-out")
    os.makedirs(outdir)

    def copies() -> list[str]:
        return [d for d in os.listdir(TMP) if d.startswith(".mutate-term-wt-src-")]

    def mutant_in_place() -> bool:
        for d in copies():
            t = os.path.join(TMP, d, "term-wt")
            if os.path.exists(os.path.join(t, "t.py")) and b"VALUE = 3" in target_bytes(t):
                return True
        return False

    p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(RUNNER), "mutate-spec.py"),
                          tree, spec], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         env=dict(genv, MUTATE_OUT_DIR=outdir))
    deadline = time.time() + 60
    while not mutant_in_place() and time.time() < deadline:
        time.sleep(0.1)
    in_place = mutant_in_place()
    time.sleep(0.5)
    p.send_signal(signal.SIGTERM)
    try:
        p.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
    check_true("a SIGTERM to mutate-spec: the mutant was running in its copy when it went", in_place)
    check("a SIGTERM to mutate-spec: exit 143, no copy beside ROOT, no copy's gitdir left",
          (p.returncode, copies(), sorted(os.listdir(os.path.join(repo, ".git", "worktrees")))),
          (143, [], ["term-wt"]))


def test_specs_with_one_file_name() -> None:
    """Two specs named code.json in different directories (a repo that mirrors
    its tests' layout in its specs has them) each get their own output file and label, carrying
    their own last line: one spec's mutant is killed, the other's survives."""
    tree, _, env = fixture("same-name-tree")
    specs = []
    for sub, edit in (("books", ["VALUE = 1", "VALUE = 2"]), ("figures", ["OTHER = 0", "OTHER = 9"])):
        sp = os.path.join(TMP, "same-name", sub, "code.json")
        os.makedirs(os.path.dirname(sp))
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump({"about": "Run: mutate-on-target.py TREE t.py test.py SPEC.",
                       "mutants": [{"name": sub, "edits": [edit], "target": "value is one"}]}, fh)
        specs.append(sp)
    outdir = os.path.join(TMP, "same-name-out")
    os.makedirs(outdir)
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(RUNNER), "mutate-spec.py"),
                        tree, *specs], capture_output=True, text=True,
                       env=dict(env, MUTATE_OUT_DIR=outdir), check=False, timeout=120)
    by_label = {line.split(":")[0]: line for line in r.stdout.splitlines()}
    check("same file name: two output files", sorted(os.listdir(outdir)),
          ["books__code.mutate.out", "figures__code.mutate.out"])
    check("same file name: two labels", sorted(by_label), ["books__code", "figures__code"])
    check_true("same file name: each label carries its own spec's line",
               "every one killed" in by_label.get("books__code", "")
               and "1 wrong" in by_label.get("figures__code", ""))
    check("same file name: the surviving mutant still fails the run", r.returncode, 1)


def two_mutant_spec(name: str) -> tuple[str, str, dict[str, str]]:
    """The usual tree, and a spec of two mutants, 'two' and 'nine', both killed on target."""
    tree, spec, env = fixture(name)
    with open(spec, "w", encoding="utf-8") as fh:
        json.dump({"about": "Run: mutate-on-target.py TREE t.py test.py SPEC.",
                   "mutants": [{"name": n, "edits": [["VALUE = 1", v]], "target": "value is one"}
                               for n, v in (("two", "VALUE = 2"), ("nine", "VALUE = 9"))]}, fh)
    return tree, spec, env


def runner_with(tree: str, spec: str, env: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, RUNNER, tree, "t.py", "test.py", spec, *extra],
                          capture_output=True, text=True, env=env, check=False, timeout=120)


def test_only_and_list() -> None:
    """--only NAME runs the control and that mutant alone, and says how many of
    the spec's ran; a name no mutant has is refused, never run as nothing;
    --list names each mutant and its target and touches nothing."""
    tree, spec, env = two_mutant_spec("only")
    r = runner_with(tree, spec, env, "--only", "nine")
    check("--only nine: exit, one of two run, and it says so",
          (r.returncode, "1 of 2 mutant(s) (--only), every one killed" in r.stdout), (0, True))
    check("--only nine: the other mutant did not run", "  two " in r.stdout, False)
    r = runner_with(tree, spec, env, "--only", "two", "--only", "nine")
    check("--only twice: both run", (r.returncode, "2 of 2 mutant(s) (--only)" in r.stdout), (0, True))
    r = runner_with(tree, spec, env, "--only", "seven")
    check("--only a name no mutant has: refused, exit 2, naming the spec's names",
          (r.returncode, "no mutant has that name" in r.stderr and "'two'" in r.stderr), (2, True))
    check("--only a name no mutant has: TARGET untouched", target_bytes(tree), ORIGINAL)
    r = runner_with(tree, spec, env, "--list")
    check("--list: exit, each name and target",
          (r.returncode, r.stdout.splitlines()), (0, ["two\t-> value is one", "nine\t-> value is one"]))
    check("--list: TARGET untouched", target_bytes(tree), ORIGINAL)


def test_spec_only_and_list() -> None:
    """mutate-spec.py --only runs just the specs that have the name, each with
    its own named mutants; a name no spec has is refused; --list runs nothing."""
    tree, spec, env = two_mutant_spec("spec-only")
    other = os.path.join(TMP, "spec-only-other.json")
    with open(other, "w", encoding="utf-8") as fh:
        json.dump({"about": "Run: mutate-on-target.py TREE t.py test.py SPEC.",
                   "mutants": [{"name": "three", "edits": [["VALUE = 1", "VALUE = 3"]],
                                "target": "value is one"}]}, fh)
    outdir = os.path.join(TMP, "spec-only-out")
    os.makedirs(outdir)
    spec_py = os.path.join(os.path.dirname(RUNNER), "mutate-spec.py")

    def spec_run(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, spec_py, tree, spec, other, *extra], capture_output=True,
                              text=True, env=dict(env, MUTATE_OUT_DIR=outdir), check=False, timeout=120)

    r = spec_run("--only", "nine")
    check("mutate-spec --only nine: exit, and only the spec that has it ran",
          (r.returncode, [ln.split(":")[0] for ln in r.stdout.splitlines()]), (0, ["spec-only"]))
    check_true("mutate-spec --only nine: one of its two mutants", "1 of 2 mutant(s) (--only)" in r.stdout)
    r = spec_run("--only", "seven")
    check("mutate-spec --only a name no spec has: refused, exit 2",
          (r.returncode, "no mutant in the given specs" in r.stderr, r.stdout), (2, True, ""))
    r = spec_run("--list")
    check("mutate-spec --list: exit, every spec's names",
          (r.returncode, sorted(r.stdout.splitlines())),
          (0, ["spec-only-other: three\t-> value is one", "spec-only: nine\t-> value is one",
               "spec-only: two\t-> value is one"]))
    check("mutate-spec --list and the refusal: TARGET untouched", target_bytes(tree), ORIGINAL)


def test_spec_a_bad_spec_beside_a_good_one() -> None:
    """A spec with no mutants key fails on its own line; the good spec beside
    it still runs and reports, and the run exits 1."""
    tree, spec, env = two_mutant_spec("bad-spec")
    bad = os.path.join(TMP, "bad-spec-bad.json")
    with open(bad, "w", encoding="utf-8") as fh:
        json.dump({"about": "Run: mutate-on-target.py TREE t.py test.py SPEC."}, fh)
    outdir = os.path.join(TMP, "bad-spec-out")
    os.makedirs(outdir)
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(RUNNER), "mutate-spec.py"),
                        tree, spec, bad], capture_output=True, text=True,
                       env=dict(env, MUTATE_OUT_DIR=outdir), check=False, timeout=120)
    by_label = {line.split(":")[0]: line for line in r.stdout.splitlines()}
    check("a bad spec: exit 1, a line for each spec", (r.returncode, sorted(by_label)),
          (1, ["bad-spec", "bad-spec-bad"]))
    check_true("a bad spec: its line names the error", "rc=1 KeyError: 'mutants'" in by_label.get("bad-spec-bad", ""))
    check_true("a bad spec: the good spec's mutants still killed",
               "every one killed on target" in by_label.get("bad-spec", ""))
    check("a bad spec: no traceback", "Traceback" in r.stderr, False)


def test_spec_a_name_twice_is_one_run() -> None:
    """--only X given twice runs X once; two mutants named X run in one copy,
    each once, not once per name."""
    tree, spec, env = two_mutant_spec("dup")
    outdir = os.path.join(TMP, "dup-out")
    os.makedirs(outdir)
    spec_py = os.path.join(os.path.dirname(RUNNER), "mutate-spec.py")

    def mutant_lines(*extra: str) -> tuple[int, list[str]]:
        r = subprocess.run([sys.executable, spec_py, tree, spec, *extra], capture_output=True,
                           text=True, env=dict(env, MUTATE_OUT_DIR=outdir), check=False, timeout=120)
        with open(os.path.join(outdir, "dup.mutate.out"), encoding="utf-8") as fh:
            names = [ln.split()[0] for ln in fh.read().splitlines() if ln.startswith("  ")]
        return r.returncode, names

    check("--only two --only two: the control and two, once",
          mutant_lines("--only", "two", "--only", "two"), (0, ["control", "two"]))
    with open(spec, "w", encoding="utf-8") as fh:
        json.dump({"about": "Run: mutate-on-target.py TREE t.py test.py SPEC.",
                   "mutants": [{"name": "twin", "edits": [["VALUE = 1", v]], "target": "value is one"}
                               for v in ("VALUE = 2", "VALUE = 9")]}, fh)
    check("two mutants named twin: each runs once",
          mutant_lines(), (0, ["control", "twin", "twin"]))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        rc = run(globals(), "mutate-on-target")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(rc)
