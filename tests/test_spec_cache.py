#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_spec_cache.py
mutate-spec.py keeps a spec's all-killed verdict while nothing its runs read
has changed (jpcheck/spec_cache.py), and runs it again when anything has.

    - Parsing strace's own lines: a path per call, pids in either form, an
      escaped byte; a call with no path is none.
    - Sorting paths: the run's copy and the checkout are the checkout's,
      relative; a tool's cache in it, a skipped prefix and /proc are no input;
      anything else is an outside input, absolute.
    - Fingerprints: a file by content, a directory by listing, missing as
      missing; outside, by size and mtime.
    - Kept and reused: end to end through mutate-spec.py with a stand-in
      strace, which writes the trace in strace's format. A second run is
      kept; an edited test, a file appearing where one was missing, an
      outside file's change, a changed spec all run again; an --only run, a
      spec not all killed and a run without strace keep nothing.

    python3 tests/test_spec_cache.py
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from _harness import ROOT, check, check_true, run
from jpcheck import spec_cache

STRACE_LINES = """\
12 openat(AT_FDCWD, "/repo/a.py", O_RDONLY|O_CLOEXEC) = 3
[pid    13] newfstatat(AT_FDCWD, "rel/b.txt", 0x7ffc, 0) = -1 ENOENT (No such file or directory)
13 execve("/usr/bin/ruff", ["ruff", "check"], 0x5 /* 9 vars */) = 0
12 openat(AT_FDCWD, "/repo/caf\\xc3\\xa9.py", O_RDONLY) = 3
12 getdents64(3, 0x55 /* 4 entries */, 32768) = 112
12 <... openat resumed>) = 4
"""


def test_paths_in_strace_lines() -> None:
    check("each call's path, in either pid form, escapes undone; a call with no path, none",
          sorted(spec_cache.paths_in(STRACE_LINES)),
          sorted(["/repo/a.py", "rel/b.txt", "/usr/bin/ruff", "/repo/café.py"]))


def test_sort_paths() -> None:
    paths = {"/w/copy/a.py", "/root/b.py", "/w/copy/.mypy_cache/x.db", "/w/copy/pkg/__pycache__/m.pyc",
             "/w/other/x", "/proc/self/maps", "/usr/lib/python3/os.py", "rel.py", "/w/copy"}
    repo, ext = spec_cache.sort_paths(paths, "/w/copy", "/root", ("/w",))
    check("the copy's and the checkout's paths are the checkout's, relative; a relative one from the copy",
          sorted(repo), [".", "a.py", "b.py", "rel.py"])
    check("  a tool's cache, a skipped prefix and /proc are no input; the rest are outside inputs",
          sorted(ext), ["/usr/lib/python3/os.py"])


def test_fingerprints() -> None:
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "d").mkdir()
        (Path(td) / "f").write_text("one", encoding="utf-8")
        f1, d1 = spec_cache.repo_print(td, "f"), spec_cache.repo_print(td, "d")
        check("a missing path is absent, in and outside the checkout",
              (spec_cache.repo_print(td, "none"), spec_cache.ext_print(str(Path(td) / "none"))),
              ("absent", "absent"))
        (Path(td) / "f").write_text("two", encoding="utf-8")
        (Path(td) / "d" / "new").write_text("", encoding="utf-8")
        check("a file by its content, a directory by its listing",
              (spec_cache.repo_print(td, "f") != f1, spec_cache.repo_print(td, "d") != d1), (True, True))
        e1 = spec_cache.ext_print(str(Path(td) / "f"))
        os.utime(Path(td) / "f", ns=(1, 1))
        check("outside, a file by size and mtime", spec_cache.ext_print(str(Path(td) / "f")) != e1, True)
    with mock.patch("shutil.which", return_value=None):
        check("no strace: no prefix, so nothing is traced", spec_cache.strace_prefix("t"), None)


def test_exact_forms() -> None:
    """The strace command, fingerprints and sources, each exactly."""
    with mock.patch("shutil.which", side_effect=lambda name: "/x/strace" if name == "strace" else None):
        check("strace -f, quiet, the traced calls, to the file named",
              spec_cache.strace_prefix("T"),
              ["/x/strace", "-f", "-qq", "-e", f"trace={spec_cache.CALLS}", "-o", "T"])
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "d").mkdir()
        for name in ("b", "a"):
            (Path(td) / "d" / name).write_text("", encoding="utf-8")
        (Path(td) / "f").write_bytes(b"x")
        listing = "dir:" + hashlib.sha256(b"a\0b").hexdigest()
        check("a directory: its names, sorted, NUL between, hashed",
              (spec_cache.repo_print(td, "d"), spec_cache.ext_print(str(Path(td) / "d"))), (listing, listing))
        check("a checkout file: its content hashed",
              spec_cache.repo_print(td, "f"), "file:" + hashlib.sha256(b"x").hexdigest())
        st = (Path(td) / "f").stat()
        check("an outside file: size and mtime", spec_cache.ext_print(str(Path(td) / "f")),
              f"stat:1:{st.st_mtime_ns}")
        check("sources: each file's content hashed, by path",
              spec_cache.key(str(Path(td) / "f")), {str(Path(td) / "f"): hashlib.sha256(b"x").hexdigest()})


def test_matches() -> None:
    """An entry matches only with the sources asked for, fingerprints held as
    mappings, and every one of them still as recorded."""
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "f").write_bytes(b"x")
        out = str(Path(td) / "f")
        doc: dict[str, object] = {"sources": {"s": "1"}, "verdict": "V",
                                  "repo": {"f": spec_cache.repo_print(td, "f")},
                                  "ext": {out: spec_cache.ext_print(out)}}
        check("everything as recorded: its verdict", spec_cache._matches(doc, td, {"s": "1"}), "V")
        check("other sources: none", spec_cache._matches(doc, td, {"s": "2"}), None)
        check("other sources, even with nothing recorded to compare: none",
              spec_cache._matches({**doc, "repo": {}, "ext": {}}, td, {"s": "2"}), None)
        check("checkout prints not a mapping: none", spec_cache._matches({**doc, "repo": []}, td, {"s": "1"}), None)
        check("outside prints not a mapping: none", spec_cache._matches({**doc, "ext": []}, td, {"s": "1"}), None)
        check("an outside print that moved: none",
              spec_cache._matches({**doc, "ext": {out: "stat:0:0"}}, td, {"s": "1"}), None)


def test_kept_refuses_what_it_cannot_read() -> None:
    with tempfile.TemporaryDirectory() as td:
        check("no entry: run it", spec_cache.kept(td, "s", td, {}), None)
        (Path(td) / "s").mkdir()
        (Path(td) / "s" / "a.json").write_text("{ not json", encoding="utf-8")
        check("an entry that is not JSON: run it", spec_cache.kept(td, "s", td, {}), None)
        (Path(td) / "s" / "a.json").write_text("{}", encoding="utf-8")
        check("an entry with no sources: run it", spec_cache.kept(td, "s", td, {}), None)
        (Path(td) / "s" / "a.json").write_text(json.dumps({"sources": {}, "repo": [], "ext": {}, "verdict": "v"}),
                                               encoding="utf-8")
        check("an entry whose fingerprints are not a mapping: run it", spec_cache.kept(td, "s", td, {}), None)
        (Path(td) / "s" / "b.json").write_text(json.dumps({"sources": {}, "repo": {}, "ext": {}, "verdict": "V"}),
                                               encoding="utf-8")
        check("an entry it cannot read does not stop it finding one that matches",
              spec_cache.kept(td, "s", td, {}), "V")


def test_entries_per_state_newest_kept() -> None:
    """Two states of the inputs (a branch and the default branch) each keep
    their entry; past KEEP entries the oldest go."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "root"
        root.mkdir()
        (root / "f").write_text("a", encoding="utf-8")
        spec_cache.keep(td, "s", str(root), {}, {"f"}, set(), "A")
        (root / "f").write_text("b", encoding="utf-8")
        spec_cache.keep(td, "s", str(root), {}, {"f"}, set(), "B")
        check("the state now: its own entry", spec_cache.kept(td, "s", str(root), {}), "B")
        (root / "f").write_text("a", encoding="utf-8")
        check("  and back at the other state: that one's, still there", spec_cache.kept(td, "s", str(root), {}), "A")
        for n in range(spec_cache.KEEP + 3):
            (root / "f").write_text(str(n), encoding="utf-8")
            spec_cache.keep(td, "s", str(root), {}, {"f"}, set(), str(n))
        check("past KEEP entries, the oldest go", len(list((Path(td) / "s").glob("*.json"))), spec_cache.KEEP)


def test_keep_names_by_content_and_keeps_the_newest() -> None:
    """An entry is written where no cache was yet, named by its sorted JSON;
    pruning goes by modification time, newest kept, not by name."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "root"
        root.mkdir()
        (root / "f").write_bytes(b"x")
        outside = Path(td) / "out.txt"
        outside.write_bytes(b"y")
        cache = Path(td) / "a" / "b"
        spec_cache.keep(str(cache), "s", str(root), {"p": "1"}, {"f"}, {str(outside)}, "V")
        doc = {"sources": {"p": "1"}, "verdict": "V", "repo": {"f": spec_cache.repo_print(str(root), "f")},
               "ext": {str(outside): spec_cache.ext_print(str(outside))}}
        text = json.dumps(doc, sort_keys=True).encode()
        named = cache / "s" / f"{hashlib.sha256(text).hexdigest()[:16]}.json"
        check("written where no cache was, named by its sorted JSON, holding it",
              named.read_bytes() if named.is_file() else None, text)
        spec_cache.keep(str(cache), "s", str(root), {"p": "1"}, {"f"}, {str(outside)}, "V")
        check("  the same state again: the same one entry", len(list((cache / "s").glob("*.json"))), 1)
        entries = cache / "s"
        for p in entries.glob("*.json"):
            p.unlink()
        names = [f"{c}.json" for c in "abcdefghijk"]
        for age, name in enumerate(names):
            (entries / name).write_text("{}", encoding="utf-8")
            os.utime(entries / name, ns=(10**9 * (100 - age), 10**9 * (100 - age)))
        (root / "f").write_bytes(b"new")
        spec_cache.keep(str(cache), "s", str(root), {}, {"f"}, set(), "N")
        left = sorted(p.name for p in entries.glob("*.json"))
        check("pruned by age, newest kept: the new entry and the 7 youngest, whatever their names",
              len(left) == spec_cache.KEEP and "a.json" in left and "k.json" not in left
              and not any(n in left for n in ("i.json", "j.json", "k.json", "h.json")), True)


FAKE_STRACE = '''#!/usr/bin/env python3
"""A stand-in for strace: runs the command after -o FILE and writes, in
strace's format, the reads the fixture's runs make."""
import os, subprocess, sys
a = sys.argv[1:]
trace, cmd = a[a.index("-o") + 1], a[a.index("-o") + 2:]
r = subprocess.run(cmd, check=False)
tree = cmd[2]
with open(trace, "w", encoding="utf-8") as fh:
    for p in ("t.py", "test.py"):
        fh.write(f'1 openat(AT_FDCWD, "{tree}/{p}", O_RDONLY) = 3\\n')
    fh.write(f'[pid 2] newfstatat(AT_FDCWD, "{tree}/absent.txt", 0x0, 0) = -1 ENOENT\\n')
    fh.write(f'2 openat(AT_FDCWD, "{os.environ["FAKE_OUTSIDE"]}", O_RDONLY) = 3\\n')
sys.exit(r.returncode)
'''

TEST = "import sys\nimport t\nif t.VALUE != 1:\n    print('FAIL value is one')\n    sys.exit(1)\nprint('ok')\n"


def _fixture(td: Path, new: str = "VALUE = 2") -> tuple[Path, Path, dict[str, str]]:
    tree, bin_dir = td / "tree", td / "bin"
    tree.mkdir()
    bin_dir.mkdir()
    (tree / "t.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tree / "test.py").write_text(TEST, encoding="utf-8")
    spec = td / "spec.json"
    spec.write_text(json.dumps({"about": "Run: mutate-on-target.py TREE t.py test.py SPEC",
                                "mutants": [{"name": "value", "edits": [["VALUE = 1", new]],
                                             "target": "value is one"}]}), encoding="utf-8")
    (bin_dir / "strace").write_text(FAKE_STRACE, encoding="utf-8")
    (bin_dir / "strace").chmod(0o755)
    outside = td / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    for d in ("out", "copies", "tmp"):
        (td / d).mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("MUTATE_")}
    # The runs' temporary directory is td/tmp, so td/outside.txt is an outside
    # input; under the system's /tmp it would be a temporary file, no input.
    env.update(PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}", FAKE_OUTSIDE=str(outside),
               MUTATE_CACHE_DIR=str(td / "cache"), MUTATE_OUT_DIR=str(td / "out"),
               MUTATE_COPY_DIR=str(td / "copies"), XDG_CACHE_HOME=str(td / "xdg"), TMPDIR=str(td / "tmp"))
    return tree, spec, env


def _spec(tree: Path, spec: Path, env: dict[str, str], *extra: str) -> tuple[int, str, str]:
    p = subprocess.run([sys.executable, str(ROOT / "mutate-spec.py"), "--jobs", "2", *extra, str(tree), str(spec)],
                       capture_output=True, text=True, env=env, check=False, timeout=120)
    return p.returncode, p.stdout.strip(), p.stderr


def test_a_spec_is_kept_while_nothing_it_read_changed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        tree, spec, env = _fixture(td)
        entries = td / "cache" / "spec"
        rc, out, _ = _spec(tree, spec, env)
        found = sorted(entries.glob("*.json")) if entries.is_dir() else []
        check_true("first run: every one killed, run, and kept",
                   rc == 0 and out.endswith("every one killed on target") and len(found) == 1)
        entry = found[0]
        doc = json.loads(entry.read_text(encoding="utf-8"))
        check("  with what its runs read: the checkout's paths and the outside one",
              (sorted(doc["repo"]), sorted(doc["ext"])), (["absent.txt", "t.py", "test.py"], [env["FAKE_OUTSIDE"]]))
        rc, out, _ = _spec(tree, spec, env)
        check_true("second run, nothing changed: kept, still a pass to every reader",
                   rc == 0 and "every one killed on target, kept:" in out)
        (tree / "test.py").write_text(TEST + "# edited\n", encoding="utf-8")
        check_true("the test edited: run again", "kept" not in _spec(tree, spec, env)[1])
        check_true("  and kept again", "kept" in _spec(tree, spec, env)[1])
        (tree / "absent.txt").write_text("", encoding="utf-8")
        check_true("a file appearing where the run found none: run again", "kept" not in _spec(tree, spec, env)[1])
        os.utime(env["FAKE_OUTSIDE"], (time.time() + 60, time.time() + 60))
        check_true("an outside file changed: run again", "kept" not in _spec(tree, spec, env)[1])
        doc = json.loads(spec.read_text(encoding="utf-8"))
        doc["about"] += " "
        spec.write_text(json.dumps(doc), encoding="utf-8")
        check_true("the spec changed: run again", "kept" not in _spec(tree, spec, env)[1])
        before = sorted(p.name for p in entries.glob("*.json"))
        check_true("an --only run is never kept or reused",
                   "kept" not in _spec(tree, spec, env, "--only", "value")[1]
                   and sorted(p.name for p in entries.glob("*.json")) == before)


def test_nothing_is_kept_for_a_spec_not_all_killed_or_without_strace() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        tree, spec, env = _fixture(td, new="VALUE = 1  # same")
        rc, _, _ = _spec(tree, spec, env)
        check_true("a mutant that survives: the spec fails, and nothing is kept",
                   rc != 0 and not (td / "cache").exists())
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        tree, spec, env = _fixture(td)
        (td / "bin" / "strace").unlink()
        env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep)
                                      if not (Path(p) / "strace").exists())
        rc, _, err = _spec(tree, spec, env)
        check_true("no strace: the spec runs and passes, nothing kept, and it says so",
                   rc == 0 and not (td / "cache").exists() and "strace is not installed" in err)


if __name__ == "__main__":
    sys.exit(run(globals(), "spec cache"))
