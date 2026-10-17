#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_spec_cache.py
mutate-spec.py keeps a spec's all-killed verdict while nothing its runs read
has changed (jpcheck/spec_cache.py), and runs it again when anything has.

    - Parsing strace's own lines: a path per call, pids in either form, an
      escaped byte, whether any call opened it; a call with no path is none.
    - Sorting paths: the run's copy and the checkout are the checkout's,
      relative; a tool's cache in it, a skipped prefix and /proc are no input;
      anything else is an outside input, absolute; opened if either was.
    - Fingerprints: a file by content, missing as missing; outside, by size
      and mtime; a directory opened by its listing, one only resolved through
      as a directory whatever it holds.
    - Kept and reused: end to end through mutate-spec.py with a stand-in
      strace, which writes the trace in strace's format. A second run is
      kept, and still kept after a file is written beside the checkout; an
      edited test, a file appearing where one was missing, a file added to a
      directory a run listed, an outside file's change, a changed spec all run
      again; an --only run, a spec not all killed and a run without strace
      keep nothing.

    python3 tests/test_spec_cache.py
"""

import hashlib
import io
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

# strace -f -y's own lines: AT_FDCWD and a directory's number carry its path.
STRACE_LINES = """\
12 openat(AT_FDCWD</w>, "/repo/a.py", O_RDONLY|O_CLOEXEC) = 3</repo/a.py>
[pid    13] newfstatat(AT_FDCWD</repo>, "rel/b.txt", 0x7ffc, 0) = -1 ENOENT (No such file or directory)
13 execve("/usr/bin/ruff", ["ruff", "check"], 0x5 /* 9 vars */) = 0
12 openat(AT_FDCWD</w>, "/repo/caf\\xc3\\xa9.py", O_RDONLY) = 3</repo/caf\\xc3\\xa9.py>
12 getdents64(3</repo>, 0x55 /* 4 entries */, 32768) = 112
12 newfstatat(AT_FDCWD</w>, "/repo/a.py", {st_mode=S_IFREG|0644, st_size=1, ...}, 0) = 0
12 newfstatat(AT_FDCWD</w>, "/repo", {st_mode=S_IFDIR|0755, ...}, 0) = 0
12 openat(AT_FDCWD</w>, "/repo", O_RDONLY|O_NONBLOCK|O_CLOEXEC|O_DIRECTORY) = 3</repo>
12 readlink("/home", 0x7ffc, 1023) = -1 EINVAL (Invalid argument)
12 access("/etc/x", R_OK)    = 0
12 creat("/repo/new", 0644) = 3</repo/new>
12 open("/etc/y", O_RDONLY) = 3</etc/y>
12 openat2(AT_FDCWD</w>, "/etc/z", {flags=O_RDONLY}, 24) = 3</etc/z>
14 openat(5</a\\76b>, "c", O_RDONLY) = 6</a\\76b/c>
14 newfstatat(3</repo/a.py>, "", {st_mode=S_IFREG|0644, ...}, AT_EMPTY_PATH) = 0
"""

# Two calls made relative to a directory's descriptor, as strace 6.8 wrote
# them: find walking a subdirectory through its parent's descriptor, and a
# file opened with Python's dir_fd.
FIND = """\
71 openat(AT_FDCWD</r>, ".", O_RDONLY|O_NOCTTY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC|O_DIRECTORY) = 4</r>
71 getdents64(4</r>, 0x5adac725c100 /* 3 entries */, 32768) = 72
71 newfstatat(5</r>, "sub", {st_mode=S_IFDIR|0775, st_size=4096, ...}, AT_SYMLINK_NOFOLLOW) = 0
71 openat(5</r>, "sub", O_RDONLY|O_NOCTTY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC|O_DIRECTORY) = 6</r/sub>
71 getdents64(6</r/sub>, 0x5adac725c120 /* 3 entries */, 32768) = 80
72 fchdir(3</r>)                    = 0
72 execve("/usr/bin/cat", ["cat", "./sub/f.txt"], 0x7ffe902d1670 /* 70 vars */) = 0
72 openat(AT_FDCWD</r>, "./sub/f.txt", O_RDONLY) = 3</r/sub/f.txt>
"""
DIR_FD = """\
80 openat(AT_FDCWD</r>, "sub", O_RDONLY|O_CLOEXEC|O_DIRECTORY) = 3</r/sub>
80 openat(3</r/sub>, "f.txt", O_RDONLY|O_CLOEXEC) = 4</r/sub/f.txt>
80 chdir("sub")                    = 0
80 newfstatat(AT_FDCWD</r/sub>, "nope/x", 0x7ffca8643ea0, 0) = -1 ENOENT (No such file or directory)
"""


def test_paths_in_strace_lines() -> None:
    t = spec_cache.paths_in(STRACE_LINES)
    check("each call's path, in either pid form, escapes undone (\\xNN and octal), a relative one in the "
          "directory its call prints, an empty one the descriptor's own; opened if any call opened it; "
          "a call with no path, none",
          t.paths,
          {"/repo/a.py": True, "/repo/rel/b.txt": False, "/usr/bin/ruff": False, "/repo/café.py": True,
           "/repo": True, "/home": False, "/etc/x": False, "/repo/new": True, "/etc/y": True, "/etc/z": True,
           "/a>b/c": True})
    check("  found: every path a call returned no error for, padded or not",
          t.found, set(t.paths) - {"/repo/rel/b.txt", "/home"})
    check("  every line placed", t.unplaced, [])


def test_calls_relative_to_a_descriptor() -> None:
    check("find: the subdirectory it walks by its parent's descriptor is opened, so listed; the file "
          "cat reads, from the directory find's fchdir left",
          spec_cache.paths_in(FIND).paths, {"/r": True, "/r/sub": True, "/usr/bin/cat": False, "/r/sub/f.txt": True})
    check("dir_fd: the file opened through a directory's descriptor, under that directory; after a chdir, "
          "a relative name in the new directory",
          spec_cache.paths_in(DIR_FD).paths, {"/r/sub": True, "/r/sub/f.txt": True, "/r/sub/nope/x": False})


def test_names_without_a_directory() -> None:
    """open, stat, execve and the like print no directory: a relative name
    is placed in its process's working directory as its AT_FDCWD calls
    print it, before or after, and not across a chdir."""
    t = spec_cache.paths_in('9 openat(AT_FDCWD</r>, "a", O_RDONLY) = 3</r/a>\n'
                            '9 stat("b", {st_mode=S_IFREG}) = 0\n')
    check("after an AT_FDCWD: there", (t.paths, t.unplaced), ({"/r/a": True, "/r/b": False}, []))
    t = spec_cache.paths_in('9 execve(".git/hooks/pre-commit", [".git/hooks/pre-commit"], 0x6 /* 83 vars */) = 0\n'
                            '8 openat(AT_FDCWD</elsewhere>, "x", O_RDONLY) = 3</elsewhere/x>\n'
                            '9 openat(AT_FDCWD</r>, "/etc/ld.so.cache", O_RDONLY) = 3</etc/ld.so.cache>\n')
    check("before its process's first AT_FDCWD (git running a hook): held, then placed where that one says, "
          "not where another process is",
          (t.paths, t.found >= {"/r/.git/hooks/pre-commit"}, t.unplaced),
          ({"/r/.git/hooks/pre-commit": False, "/elsewhere/x": True, "/etc/ld.so.cache": True}, True, []))
    for between in ('7 chdir("/x")                    = 0\n', '7 fchdir(4</x>)                    = 0\n'):
        what = between.split("(")[0][2:]
        t = spec_cache.paths_in('9 stat("b", 0x1) = 0\n' + between + '9 openat(AT_FDCWD</r>, "/e", O_RDONLY) = 3\n')
        check(f"  a {what} by another process in between: still placed", (t.paths.get("/r/b"), t.unplaced),
              (False, []))
        t = spec_cache.paths_in(THREAD + '9 stat("b", 0x1) = 0\n' + between
                                + '9 openat(AT_FDCWD</r>, "/e", O_RDONLY) = 3\n')
        check(f"  a {what} by a thread of its own in between: it cannot be placed",
              ("/r/b" in t.paths, [line.split("(")[0] for line in t.unplaced]), (False, ["9 stat"]))
    t = spec_cache.paths_in(THREAD + '9 openat(AT_FDCWD</r>, "a", O_RDONLY) = 3\n7 chdir("/x") = 0\n'
                            '9 stat("b", 0x1) = 0\n7 stat("c", 0x1) = 0\n')
    check("  a thread's chdir to a known place moves the process with it: both now there",
          (t.paths.get("/x/b"), t.paths.get("/x/c"), t.unplaced), (False, False, []))
    t = spec_cache.paths_in('9 stat("b", 0x1) = 0\n')
    check("  never told where: cannot be placed", (t.paths, len(t.unplaced)), ({}, 1))
    t = spec_cache.paths_in('9 chdir("sub") = 0\n')
    check("  a relative chdir from nowhere known: cannot be placed", (t.paths, len(t.unplaced)), ({}, 1))
    t = spec_cache.paths_in('9 chdir("sub") = 0\n9 stat("b", 0x1) = 0\n9 openat(AT_FDCWD</r/sub>, "x", O_RDONLY) = 3\n')
    check("  after it, a held name is placed by the next AT_FDCWD, which says where the chdir went",
          (t.paths, len(t.unplaced)), ({"/r/sub/b": False, "/r/sub/x": True}, 1))
    t = spec_cache.paths_in('9 openat(AT_FDCWD</r>, "a", O_RDONLY) = 3\n9 chdir("sub") = 0\n9 stat("b", 0x1) = 0\n'
                            '9 chdir("/nowhere") = -1 ENOENT (No such file or directory)\n9 stat("c", 0x1) = 0\n'
                            '9 chdir("/u" <unfinished ...>\n9 <... chdir resumed>) = 0\n9 stat("d", 0x1) = 0\n')
    check("  a chdir that worked: names after it in the new directory; one that failed: where they were; "
          "one whose result came later: not known",
          (sorted(t.paths), [line.split("(")[0] for line in t.unplaced]),
          (["/nowhere", "/r/a", "/r/sub", "/r/sub/b", "/r/sub/c", "/u"], ["9 stat"]))
    t = spec_cache.paths_in('9 fchdir(4</r/d>) = 0\n9 stat("b", 0x1) = 0\n9 fchdir(6</q>) = -1 EBADF (Bad file)\n'
                            '9 stat("c", 0x1) = 0\n9 fchdir(5) = 0\n9 stat("e", 0x1) = 0\n')
    check("  an fchdir: names after it in its directory; one that failed: where they were; one strace could "
          "not name: none known",
          (t.paths, [line.split("(")[0] for line in t.unplaced]), ({"/r/d/b": False, "/r/d/c": False}, ["9 stat"]))


# Process 9 starts thread 7: CLONE_FS, so the two share a working directory.
THREAD = ("9 clone3({flags=CLONE_VM|CLONE_FS|CLONE_FILES|CLONE_SIGHAND|CLONE_THREAD, child_tid=0x1, "
          "exit_signal=0, stack=0x2, stack_size=0x3}, 88) = 7\n")


def test_children() -> None:
    """A child process starts where its parent was at the clone; a thread
    shares its parent's working directory."""
    t = spec_cache.paths_in('9 openat(AT_FDCWD</r>, "a", O_RDONLY) = 3\n'
                            '9 clone(child_stack=NULL, flags=CLONE_CHILD_CLEARTID|SIGCHLD, child_tidptr=0x1) = 7\n'
                            '7 access(".git/hooks/post-checkout", X_OK) = -1 ENOENT (No such file or directory)\n'
                            '7 chdir("/y") = 0\n7 stat("c", 0x1) = 0\n9 stat("d", 0x1) = 0\n')
    check("a forked child: a relative name where its parent was; its own chdir moves it alone",
          (sorted(t.paths), t.unplaced),
          (["/r/.git/hooks/post-checkout", "/r/a", "/r/d", "/y", "/y/c"], []))
    t = spec_cache.paths_in('9 openat(AT_FDCWD</r>, "a", O_RDONLY) = 3\n'
                            '9 clone(child_stack=NULL, flags=SIGCHLD <unfinished ...>\n'
                            '7 access(".git/hooks/post-checkout", X_OK) = -1 ENOENT (No such file or directory)\n'
                            '9 chdir("/later") = 0\n'
                            '9 <... clone resumed>, child_tidptr=0x1) = 7\n')
    check("  its calls before the clone returns (strace -f prints the child first): held, then placed where "
          "the parent was when it cloned, not where it went after",
          (t.paths.get("/r/.git/hooks/post-checkout"), t.unplaced), (False, []))
    t = spec_cache.paths_in('9 openat(AT_FDCWD</r>, "a", O_RDONLY) = 3\n9 vfork()                         = 7\n'
                            '7 execve("./x", ["./x"], 0x1 /* 1 var */) = 0\n')
    check("  vfork (posix_spawn): the same", (t.paths.get("/r/x"), t.unplaced), (False, []))
    t = spec_cache.paths_in('9 openat(AT_FDCWD</r>, "a", O_RDONLY) = 3\n9 clone(child_stack=NULL <unfinished ...>\n'
                            '7 openat(AT_FDCWD</own>, "o", O_RDONLY) = 3\n9 <... clone resumed>) = 7\n'
                            '7 stat("w", 0x1) = 0\n')
    check("  a child whose own AT_FDCWD came first: where that said, not its parent's",
          (t.paths.get("/own/w"), t.paths.get("/r/w")), (False, None))
    t = spec_cache.paths_in('9 openat(AT_FDCWD</r>, "a", O_RDONLY) = 3\n9 clone(child_stack=NULL <unfinished ...>\n'
                            '7 chdir("sub") = 0\n9 <... clone resumed>) = 7\n7 stat("z", 0x1) = 0\n')
    check("  a child that moved somewhere not known before the clone returned: not put back at its parent's",
          ([line.split("(")[0] for line in t.unplaced], "/r/z" in t.paths), (["7 chdir", "7 stat"], False))
    t = spec_cache.paths_in('9 openat(AT_FDCWD</r>, "a", O_RDONLY) = 3\n'
                            '9 clone(child_stack=NULL, flags=SIGCHLD) = -1 EAGAIN (Resource temporarily unavailable)\n'
                            '7 stat("q", 0x1) = 0\n')
    check("  a clone that failed: no child, and pid 7 is no one's", ([line.split("(")[0] for line in t.unplaced]),
          ["7 stat"])
    t = spec_cache.paths_in('9 clone(child_stack=NULL, flags=SIGCHLD) = 7\n7 stat("q", 0x1) = 0\n')
    check("  a parent whose own directory was not known: nor is the child's",
          [line.split("(")[0] for line in t.unplaced], ["7 stat"])
    t = spec_cache.paths_in('9 clone3({flags=CLONE_VM|CLONE_FS|CLONE_THREAD, exit_signal=0} <unfinished ...>\n'
                            '7 stat("t", 0x1) = 0\n9 <... clone3 resumed> => {parent_tid=[7]}, 88) = 7\n'
                            '9 openat(AT_FDCWD</r>, "a", O_RDONLY) = 3\n')
    check("a thread: what it named before its clone returned is placed by its process's next AT_FDCWD",
          (t.paths.get("/r/t"), t.unplaced), (False, []))
    t = spec_cache.paths_in(THREAD + '7 openat(AT_FDCWD</r>, "a", O_RDONLY) = 3\n9 stat("p", 0x1) = 0\n')
    check("  and a thread's AT_FDCWD says where its process is", (t.paths.get("/r/p"), t.unplaced), (False, []))


def test_lines_it_cannot_place() -> None:
    lines = ['9 openat(5, "sub", O_RDONLY) = 6', '9 openat(AT_FDCWD, "rel", O_RDONLY) = 6',
             '9 openat(AT_FDCWD</r>, 0x7f00, O_RDONLY) = 3']
    t = spec_cache.paths_in("\n".join(lines) + '\n9 openat(AT_FDCWD</r>, "/abs", O_RDONLY) = 3\n')
    check("a relative name under a directory strace could not name, and a traced call whose path it cannot "
          "read, are unplaced, each line as written; an absolute one needs no directory",
          (t.paths, t.unplaced), ({"/abs": True}, lines))
    t = spec_cache.paths_in('9 openat(AT_FDCWD</r>, "a", O_RDONLY) = 3\n9 openat(5, "x", O_RDONLY) = 6\n'
                            '9 stat("b", 0x1) = 0\n')
    check("  a line it cannot place that is no chdir: the working directory is still known",
          (t.paths, t.unplaced), ({"/r/a": True, "/r/b": False}, ['9 openat(5, "x", O_RDONLY) = 6']))


def test_reader_edges() -> None:
    t = spec_cache.paths_in('9 open("rel", O_RDONLY) = 3\n9 openat(AT_FDCWD</r>, "/e", O_RDONLY) = 3\n')
    check("a held open, once placed, is an open", t.paths, {"/r/rel": True, "/e": True})
    t = spec_cache.paths_in('9 newfstatat(3</r/f>, "", 0x1, AT_EMPTY_PATH <unfinished ...>\n'
                            '9 <... newfstatat resumed>) = 0\n')
    check("an empty path on a call still running: the descriptor's own file, found once it returns",
          (t.paths, t.found), ({"/r/f": False}, {"/r/f"}))
    t = spec_cache.paths_in('9 stat("b", 0x1) = 0\n9 chdir("sub") = 0\n9 openat(AT_FDCWD</r/sub>, "x", O_RDONLY) = 3\n')
    check("a held name, then a chdir from a directory not known: neither placed in the one that follows",
          (t.paths, t.unplaced), ({"/r/sub/x": True}, ['9 chdir("sub") = 0', '9 stat("b", 0x1) = 0']))
    t = spec_cache.paths_in('9 openat(AT_FDCWD</r>, "a", O_RDONLY <unfinished ...>\n'
                            '9 getdents64(3</r>, 0x1, 32768 <unfinished ...>\n'
                            '9 <... getdents64 resumed>) = 0\n')
    check("a call that never resumed (its process killed): not found by the next call's result",
          (t.paths, t.found), ({"/r/a": True}, set()))
    t = spec_cache.paths_in('9 execve("/usr/bin/x", ["x"], 0x1 /* 1 var */ <unfinished ...>\n'
                            '9 <... execve resumed>)           = ?\n')
    check("a result strace could not learn ('= ?', its process gone): named, not found",
          (t.paths, t.found), ({"/usr/bin/x": False}, set()))


def test_a_descriptor_itself() -> None:
    t = spec_cache.paths_in('9 statx(0<pipe:[15666906]>, NULL, AT_STATX_SYNC_AS_STAT, STATX_ALL, NULL) = -1 EFAULT\n'
                            '9 newfstatat(1<socket:[7]>, "", {st_mode=S_IFSOCK}, AT_EMPTY_PATH) = 0\n'
                            '9 statx(3</r/f>, NULL, AT_STATX_SYNC_AS_STAT|AT_EMPTY_PATH, STATX_ALL, {}) = 0\n'
                            '9 openat(AT_FDCWD</r>, NULL, O_RDONLY) = -1 EFAULT (Bad address)\n'
                            '9 open("", O_RDONLY) = -1 ENOENT (No such file or directory)\n')
    check("a NULL or empty path: the descriptor's own file when the call worked; a pipe or socket reads no "
          "file; one that failed read nothing",
          (t.paths, t.found, t.unplaced), ({"/r/f": False}, {"/r/f"}, []))


def test_found_across_unfinished_calls() -> None:
    t = spec_cache.paths_in('9 openat(AT_FDCWD</r>, "a", O_RDONLY <unfinished ...>\n'
                            '8 openat(AT_FDCWD</r>, "x", O_RDONLY) = 3\n'
                            '9 <... openat resumed>)           = 3</r/a>\n'
                            '9 access("/etc/ld.so.preload", R_OK <unfinished ...>\n'
                            '9 <... access resumed>)           = -1 ENOENT (No such file or directory)\n'
                            '9 getdents64(3</r>, 0x1 /* 0 entries */, 32768 <unfinished ...>\n'
                            '9 <... getdents64 resumed>)       = 0\n'
                            '7 stat("held", 0x1 <unfinished ...>\n'
                            '7 <... stat resumed>)             = 0\n'
                            '7 stat("held2", 0x1 <unfinished ...>\n'
                            '7 <... stat resumed>)             = -1 ENOENT (No such file or directory)\n'
                            '7 getdents64(3</q>, 0x1 /* 0 entries */, 32768 <unfinished ...>\n'
                            '7 <... getdents64 resumed>)       = 0\n'
                            '7 openat(AT_FDCWD</q>, "b", O_RDONLY) = -1 ENOENT (No such file or directory)\n')
    check("a call resumed: found by its result, padding and all; a failed one never claimed by a later "
          "call's result; one held for its directory by its own result once placed, not a later call's",
          (t.found, sorted(t.paths), t.unplaced),
          ({"/r/a", "/r/x", "/q/held"},
           ["/etc/ld.so.preload", "/q/b", "/q/held", "/q/held2", "/r/a", "/r/x"], []))


def test_mark() -> None:
    paths: dict[str, bool] = {}
    spec_cache.mark(paths, "a", False)
    spec_cache.mark(paths, "b", True)
    check("first seen: as that call left it", paths, {"a": False, "b": True})
    spec_cache.mark(paths, "a", True)
    spec_cache.mark(paths, "b", False)
    check("  then opened once any call opened it, in either order", paths, {"a": True, "b": True})


def test_sort_paths() -> None:
    paths = {"/w/copy/a.py": False, "/root/a.py": True, "/root/b.py": False, "/w/copy/.mypy_cache/x.db": True,
             "/w/copy/pkg/__pycache__/m.pyc": True, "/w/other/x": True, "/proc/self/maps": True,
             "/usr/lib/python3/os.py": True, "/usr/lib": False, "rel.py": False, "/w/copy": True}
    repo, ext = spec_cache.sort_paths(paths, "/w/copy", "/root", ("/w",))
    check("the copy's and the checkout's paths are the checkout's, relative; a relative one from the copy; "
          "opened if the copy's or the checkout's was",
          repo, {".": True, "a.py": True, "b.py": False, "rel.py": False})
    check("  a tool's cache, a skipped prefix and /proc are no input; the rest are outside inputs",
          ext, {"/usr/lib/python3/os.py": True, "/usr/lib": False})
    repo, _ = spec_cache.sort_paths({"/root/a.py": True, "/w/copy/a.py": False}, "/w/copy", "/root", ("/w",))
    check("  opened stays opened, in either order", repo, {"a.py": True})
    _, ext = spec_cache.sort_paths({"/usr/lib/": True, "/usr/lib": False, "/usr/x": False}, "/w/copy", "/root", ())
    check("  and outside, one path named two ways: opened if either was", ext, {"/usr/lib": True, "/usr/x": False})
    repo, ext = spec_cache.sort_paths({"/w/copy/a.py": True, "/root/.git/worktrees/m/HEAD": False,
                                       "/root/.git/HEAD": False}, "/w/copy", "/root", ("/w", "/root/.git/worktrees/m"))
    check("  a copy under a skipped prefix is still the checkout; a skipped prefix under the checkout (the "
          "run's gitdir) is dropped", (repo, ext), ({"a.py": True, ".git/HEAD": False}, {}))
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "mtab").symlink_to("/proc/self/mounts")
        (Path(td) / "real").write_text("", encoding="utf-8")
        (Path(td) / "link").symlink_to(Path(td) / "real")
        _, ext = spec_cache.sort_paths({str(Path(td) / n): False for n in ("mtab", "link")}, "/w/copy", "/root", ())
        check("  a link into /proc is no input, as /proc is not; a link elsewhere is, by its own name",
              ext, {str(Path(td) / "link"): False})
    with tempfile.TemporaryDirectory() as td:
        real = Path(td) / "real"
        for d in ("copy", "root", "skip"):
            (real / d).mkdir(parents=True)
        (Path(td) / "via").symlink_to(real)
        via = Path(td) / "via"
        repo, ext = spec_cache.sort_paths({str(real / "copy" / "a.py"): True, str(real / "root" / "b.py"): False,
                                           str(real / "skip" / "x"): True, "/usr/c": False},
                                          str(via / "copy"), str(via / "root"), (str(via / "skip"),))
        check("  a copy, checkout or skipped prefix named through a link: strace's resolved paths still "
              "sort to it", (repo, ext), ({"a.py": True, "b.py": False}, {"/usr/c": False}))


def test_fingerprints() -> None:
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "d").mkdir()
        (Path(td) / "f").write_text("one", encoding="utf-8")
        d = str(Path(td) / "d")
        f1, d1, x1 = spec_cache.repo_print(td, "f", False), spec_cache.repo_print(td, "d", True), spec_cache.ext_print(d, True)
        check("a missing path is absent, in and outside the checkout, opened or not",
              (spec_cache.repo_print(td, "none", True), spec_cache.repo_print(td, "none", False),
               spec_cache.ext_print(str(Path(td) / "none"), True), spec_cache.ext_print(str(Path(td) / "none"), False)),
              ("absent",) * 4)
        (Path(td) / "f").write_text("two", encoding="utf-8")
        (Path(td) / "d" / "new").write_text("", encoding="utf-8")
        check("a file by its content, a directory opened by its listing, in and outside the checkout",
              (spec_cache.repo_print(td, "f", False) != f1, spec_cache.repo_print(td, "d", True) != d1,
               spec_cache.ext_print(d, True) != x1), (True, True, True))
        check("  a directory only resolved through: a directory, whatever it holds",
              (spec_cache.repo_print(td, "d", False), spec_cache.ext_print(d, False)), ("dir", "dir"))
        e1 = spec_cache.ext_print(str(Path(td) / "f"), False)
        os.utime(Path(td) / "f", ns=(1, 1))
        check("outside, a file by size and mtime", spec_cache.ext_print(str(Path(td) / "f"), False) != e1, True)
        check("  opened or not, a file is the same print",
              (spec_cache.ext_print(str(Path(td) / "f"), True), spec_cache.repo_print(td, "f", True)),
              (spec_cache.ext_print(str(Path(td) / "f"), False), spec_cache.repo_print(td, "f", False)))
    with mock.patch("shutil.which", return_value=None):
        check("no strace: no prefix, so nothing is traced", spec_cache.strace_prefix("t"), None)


def test_exact_forms() -> None:
    """The strace command, fingerprints and sources, each exactly."""
    with mock.patch("shutil.which", side_effect=lambda name: "/x/strace" if name == "strace" else None):
        check("strace -f, quiet, descriptors as their paths, the traced calls, to the file named",
              spec_cache.strace_prefix("T"),
              ["/x/strace", "-f", "-qq", "-y", "-e", f"trace={spec_cache.CALLS}", "-o", "T"])
    check("  the calls: every one that names a path, the listing, the working directory's changes, and the "
          "ones that make a child",
          spec_cache.CALLS, "open,openat,openat2,creat,execve,execveat,stat,lstat,newfstatat,statx,access,"
          "faccessat,faccessat2,readlink,readlinkat,getdents64,chdir,fchdir,clone,clone3,fork,vfork")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "d").mkdir()
        for name in ("b", "a"):
            (Path(td) / "d" / name).write_text("", encoding="utf-8")
        (Path(td) / "f").write_bytes(b"x")
        listing = "dir:" + hashlib.sha256(b"a\0b").hexdigest()
        check("a directory opened: its names, sorted, NUL between, hashed",
              (spec_cache.repo_print(td, "d", True), spec_cache.ext_print(str(Path(td) / "d"), True)),
              (listing, listing))
        check("a directory not opened: dir",
              (spec_cache.repo_print(td, "d", False), spec_cache.ext_print(str(Path(td) / "d"), False)),
              ("dir", "dir"))
        check("a checkout file: its content hashed",
              spec_cache.repo_print(td, "f", False), "file:" + hashlib.sha256(b"x").hexdigest())
        st = (Path(td) / "f").stat()
        check("an outside file: size and mtime", spec_cache.ext_print(str(Path(td) / "f"), False),
              f"stat:1:{st.st_mtime_ns}")
        me = Path(spec_cache.__file__).resolve()
        check("sources: each file's content hashed, by path, and spec_cache's own, which decides what an "
              "entry's inputs are",
              spec_cache.key(str(Path(td) / "f")),
              {str(Path(td) / "f"): hashlib.sha256(b"x").hexdigest(), str(me): hashlib.sha256(me.read_bytes()).hexdigest()})


def test_matches() -> None:
    """An entry matches only with the sources asked for, fingerprints held as
    mappings, and every one of them still as recorded."""
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "f").write_bytes(b"x")
        out = str(Path(td) / "f")
        doc: dict[str, object] = {"sources": {"s": "1"}, "verdict": "V",
                                  "repo": {"f": spec_cache.repo_print(td, "f", True)},
                                  "ext": {out: spec_cache.ext_print(out, True)}}
        check("everything as recorded: its verdict", spec_cache._matches(doc, td, {"s": "1"}), "V")
        check("other sources: none", spec_cache._matches(doc, td, {"s": "2"}), None)
        check("other sources, even with nothing recorded to compare: none",
              spec_cache._matches({**doc, "repo": {}, "ext": {}}, td, {"s": "2"}), None)
        check("checkout prints not a mapping: none", spec_cache._matches({**doc, "repo": []}, td, {"s": "1"}), None)
        check("outside prints not a mapping: none", spec_cache._matches({**doc, "ext": []}, td, {"s": "1"}), None)
        check("an outside print that moved: none",
              spec_cache._matches({**doc, "ext": {out: "stat:0:0"}}, td, {"s": "1"}), None)
        (Path(td) / "d").mkdir()
        listed = {**doc, "repo": {"d": spec_cache.repo_print(td, "d", True)},
                  "ext": {str(Path(td) / "d"): spec_cache.ext_print(str(Path(td) / "d"), True)}}
        passed = {**doc, "repo": {"d": "dir"}, "ext": {str(Path(td) / "d"): "dir"}}
        check("a directory unchanged: its listing recorded, in or outside the checkout, still matches",
              (spec_cache._matches({**listed, "ext": {}}, td, {"s": "1"}),
               spec_cache._matches({**listed, "repo": {}}, td, {"s": "1"})), ("V", "V"))
        (Path(td) / "d" / "new").write_bytes(b"")
        check("a directory gains a file: a listing recorded no longer matches; a directory resolved through still does",
              (spec_cache._matches(listed, td, {"s": "1"}), spec_cache._matches(passed, td, {"s": "1"})), (None, "V"))
        (Path(td) / "d" / "new").unlink()
        (Path(td) / "d").rmdir()
        (Path(td) / "d").write_bytes(b"")
        check("  and once it is a file, neither", (spec_cache._matches(listed, td, {"s": "1"}),
                                                   spec_cache._matches(passed, td, {"s": "1"})), (None, None))


def _begun(root: Path | str) -> spec_cache.Start:
    """A start late enough that every file written so far is older than it,
    less MARGIN_NS, with ROOT as it is now."""
    since = time.time_ns() + spec_cache.MARGIN_NS + 1
    return spec_cache.Start(since, spec_cache.snapshot(str(root), since - spec_cache.MARGIN_NS))


def test_snapshot() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for d in (".git", "sub/.mypy_cache", "node_modules/p"):
            (root / d).mkdir(parents=True)
        for f in (".git/HEAD", "sub/a.py", "sub/.mypy_cache/x.db", "node_modules/p/i.js", "top"):
            (root / f).write_text("x", encoding="utf-8")
        (root / "sub" / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
        old = 10**30
        snap = spec_cache.snapshot(td, old)
        check("every path but the top .git and tools' caches, a nested .git file included",
              sorted(snap), ["node_modules", "node_modules/p", "node_modules/p/i.js", "sub", "sub/.git", "sub/a.py", "top"])
        st = os.lstat(root / "top")
        check("  each by type, size, mtime, ctime; older than RACY, not hashed", snap["top"],
              (st.st_mode, 1, st.st_mtime_ns, st.st_ctime_ns, ""))
        x = hashlib.sha256(b"x").hexdigest()
        st = os.lstat(root / "top")
        check("  a file changed at or after RACY: its content hashed as well; a directory never",
              (spec_cache.snapshot(td, max(st.st_mtime_ns, st.st_ctime_ns))["top"][4],
               spec_cache.snapshot(td, max(st.st_mtime_ns, st.st_ctime_ns) + 1)["top"][4],
               spec_cache.snapshot(td, 0)["sub"][4]), (x, "", ""))
        with mock.patch("os.walk", return_value=[(td, [], ["gone", "top"])]):
            check("  a path gone between the listing and its status: left out, and the ones after it kept",
                  sorted(spec_cache.snapshot(td, old)), ["top"])


def test_an_edit_the_status_cannot_show() -> None:
    """Two writes of one size inside one tick of the file system's clock
    leave the same status: a file that new is compared by content."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "f").write_text("b", encoding="utf-8")
        begun = spec_cache.start(td)
        check("CONTROL: as it is now, with the file new enough to be hashed: None",
              (spec_cache.moved(td, {}, {}, set(), begun), begun.tree["f"][4] != ""), (None, True))
        before = (*begun.tree["f"][:4], hashlib.sha256(b"a").hexdigest())
        check("the same status, other content before the start: the checkout changed",
              spec_cache.moved(td, {}, {}, set(), begun._replace(tree={"f": before})),
              "the checkout changed while its runs ran")


def test_moved() -> None:
    """What the runs read is held to how it was before they began."""
    with tempfile.TemporaryDirectory() as td:
        root, out = Path(td) / "root", Path(td) / "out"
        (root / ".git").mkdir(parents=True)
        (root / ".git" / "HEAD").write_text("a", encoding="utf-8")
        (root / "a.py").write_text("a", encoding="utf-8")
        for d in ("opened", "passed"):
            (out / d).mkdir(parents=True)
        (out / "f").write_text("a", encoding="utf-8")
        (out / "link").symlink_to(out / "f")
        (out / "dirlink").symlink_to(out / "passed")
        (out / "older").symlink_to(out / "opened")
        ext = {str(out / "f"): False, str(out / "opened"): True, str(out / "passed"): False,
               str(out / "link"): False, str(out / "dirlink"): False}
        repo = {"a.py": False, ".git/HEAD": False, "made-by-a-run": False}
        # Past MARGIN_NS: everything above is older than the start.
        time.sleep(1.1)
        start = spec_cache.start(str(root))
        st = str(root)
        check("nothing moved: None", spec_cache.moved(st, repo, ext, set(), start), None)
        check("  a checkout file a run made in its copy, not in ROOT, found: None",
              spec_cache.moved(st, repo, ext, {"made-by-a-run"}, start), None)
        (out / "passed" / "new").write_text("", encoding="utf-8")
        check("  a file added to a directory only resolved through, plain or by a link: None",
              spec_cache.moved(st, repo, ext, set(), start), None)
        for path, opened, why in ((out / "f", False, "an outside file"),
                                  (out / "opened", True, "an outside directory a run listed"),
                                  (root / ".git" / "HEAD", False, "a file in the checkout's .git")):
            inside = path.is_relative_to(root)
            r, e = ({".git/HEAD": False}, {}) if inside else ({}, {str(path): opened})
            if path.is_dir():
                (path / "added").write_text("", encoding="utf-8")
            else:
                path.write_text("changed", encoding="utf-8")
            check(f"{why}, changed since the start: named", spec_cache.moved(st, r, e, set(), start),
                  f"{path} changed while its runs ran")
        (out / "link").unlink()
        (out / "link").symlink_to(out / "older")
        check("a link pointed elsewhere since the start, at an older target: named",
              spec_cache.moved(st, {}, {str(out / "link"): False}, set(), start),
              f"{out / 'link'} changed while its runs ran")
        (out / "f").unlink()
        (root / ".git" / "HEAD").unlink()
        check("an outside path a run found, gone now: named",
              spec_cache.moved(st, {}, {str(out / "f"): False}, {str(out / "f")}, start),
              f"{out / 'f'} was there for a run and is gone")
        check("  and one in the checkout's .git",
              spec_cache.moved(st, {".git/HEAD": False}, {}, {".git/HEAD"}, start),
              f"{root / '.git' / 'HEAD'} was there for a run and is gone")
        check("  gone but no run found it: None",
              spec_cache.moved(st, {".git/HEAD": False}, {str(out / "f"): False}, set(), start), None)
        (root / "extra").write_text("", encoding="utf-8")
        check("a file added to the checkout since the start", spec_cache.moved(st, {}, {}, set(), start),
              "the checkout changed while its runs ran")
        (root / "extra").unlink()
        check("  as it was once that file is gone again: None", spec_cache.moved(st, {}, {}, set(), start), None)
        (root / "a.py").write_text("b", encoding="utf-8")
        check("  a checkout file edited, though no run's reads name it", spec_cache.moved(st, {}, {}, set(), start),
              "the checkout changed while its runs ran")


def test_changed_since_margin() -> None:
    """ctime is compared less MARGIN_NS: a change exactly that long before
    the start counts, a nanosecond earlier does not."""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "f"
        f.write_text("", encoding="utf-8")
        c = os.lstat(f).st_ctime_ns
        check("at the start less the margin: changed; a nanosecond before it: not",
              (spec_cache._changed_since(str(f), False, c + spec_cache.MARGIN_NS),
               spec_cache._changed_since(str(f), False, c + spec_cache.MARGIN_NS + 1)), (True, False))
        check("  the margin is one second", spec_cache.MARGIN_NS, 10**9)
        check("  a path not there: not", spec_cache._changed_since(str(Path(td) / "none"), True, 0), False)
        for unreachable in (NotADirectoryError("a parent is a file"), PermissionError("denied")):
            with mock.patch("os.lstat", side_effect=unreachable):
                check(f"  a path no run of this user could reach ({type(unreachable).__name__}): no status, so not",
                      spec_cache._changed_since(str(f), False, 0), False)
        with mock.patch("os.lstat", side_effect=OSError(5, "I/O error")):
            try:
                spec_cache._changed_since(str(f), False, 0)
                raised = ""
            except OSError as e:
                raised = e.strerror or ""
        check("  any other status it cannot read: raised, never read as unchanged", raised, "I/O error")
        # A link newer than its target, by more than the clock's tick.
        time.sleep(0.05)
        (Path(td) / "link").symlink_to(f)
        lc = os.lstat(Path(td) / "link").st_ctime_ns
        check("  a link changed at the start less the margin, its target older: changed",
              (lc > c, spec_cache._changed_since(str(Path(td) / "link"), False, lc + spec_cache.MARGIN_NS)), (True, True))
        (Path(td) / "dangling").symlink_to(Path(td) / "none")
        check("  a link to nothing, itself older: not",
              spec_cache._changed_since(str(Path(td) / "dangling"), False,
                                        os.lstat(Path(td) / "dangling").st_ctime_ns + spec_cache.MARGIN_NS + 1), False)


def test_keep_refuses_what_moved() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "root"
        root.mkdir()
        (root / "f").write_text("a", encoding="utf-8")
        start = spec_cache.start(str(root))
        (root / "f").write_text("b", encoding="utf-8")
        why = spec_cache.keep(td, "s", str(root), {}, {"f": False}, {}, "V", found=set(), begun=start)
        check("the checkout moved: the reason, and no entry",
              (why, (Path(td) / "s").exists()), ("the checkout changed while its runs ran", False))
        check("  as it was: kept, no reason",
              spec_cache.keep(td, "s", str(root), {}, {"f": False}, {}, "V", found=set(), begun=_begun(root)), None)


def test_a_kept_entry_is_touched() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "root"
        root.mkdir()
        spec_cache.keep(td, "s", str(root), {}, {}, {}, "V", found=set(), begun=_begun(root))
        entry = next((Path(td) / "s").glob("*.json"))
        os.utime(entry, ns=(10**9, 10**9))
        check("a hit: its verdict, and its entry's mtime is now",
              (spec_cache.kept(td, "s", str(root), {}), entry.stat().st_mtime_ns > 10**18), ("V", True))
        os.utime(entry, ns=(10**9, 10**9))
        err = io.StringIO()
        with mock.patch("os.utime", side_effect=PermissionError("read-only")), mock.patch("sys.stderr", err):
            check("  an entry it cannot touch: still its verdict", spec_cache.kept(td, "s", str(root), {}), "V")
        check("  and it says so, naming the entry and what is lost",
              err.getvalue(), f"spec_cache: {entry}: kept verdict used but not touched, so pruning may drop it "
              "as unused: read-only\n")
        check("  and untouched", entry.stat().st_mtime_ns, 10**9)


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
        (Path(td) / "s" / "a.json").write_text("{ not json", encoding="utf-8")
        entries = [Path(td) / "s" / "a.json", Path(td) / "s" / "b.json"]
        with mock.patch.object(Path, "glob", lambda _self, _pattern: iter(entries)):
            check("  an entry that does not parse, met first, is passed over",
                  spec_cache.kept(td, "s", td, {}), "V")


def test_entries_per_state_newest_kept() -> None:
    """Two states of the inputs (a branch and the default branch) each keep
    their entry; past KEEP entries the oldest go."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "root"
        root.mkdir()
        (root / "f").write_text("a", encoding="utf-8")
        spec_cache.keep(td, "s", str(root), {}, {"f": False}, {}, "A", found=set(), begun=_begun(root))
        (root / "f").write_text("b", encoding="utf-8")
        spec_cache.keep(td, "s", str(root), {}, {"f": False}, {}, "B", found=set(), begun=_begun(root))
        check("the state now: its own entry", spec_cache.kept(td, "s", str(root), {}), "B")
        (root / "f").write_text("a", encoding="utf-8")
        check("  and back at the other state: that one's, still there", spec_cache.kept(td, "s", str(root), {}), "A")
        for n in range(spec_cache.KEEP + 3):
            (root / "f").write_text(str(n), encoding="utf-8")
            spec_cache.keep(td, "s", str(root), {}, {"f": False}, {}, str(n), found=set(), begun=_begun(root))
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
        outdir, listed = Path(td) / "outdir", Path(td) / "listed"
        outdir.mkdir()
        listed.mkdir()
        cache = Path(td) / "a" / "b"
        repo = {"f": False, ".": True}
        ext = {str(outside): False, str(outdir): False, str(listed): True}
        check("written, so no reason",
              spec_cache.keep(str(cache), "s", str(root), {"p": "1"}, repo, ext, "V", found=set(), begun=_begun(root)),
              None)
        doc = {"sources": {"p": "1"}, "verdict": "V",
               "repo": {"f": spec_cache.repo_print(str(root), "f", False),
                        ".": spec_cache.repo_print(str(root), ".", True)},
               "ext": {str(outside): spec_cache.ext_print(str(outside), False), str(outdir): "dir",
                       str(listed): spec_cache.ext_print(str(listed), True)}}
        text = json.dumps(doc, sort_keys=True).encode()
        named = cache / "s" / f"{hashlib.sha256(text).hexdigest()[:16]}.json"
        check("written where no cache was, named by its sorted JSON, holding it, each path printed as it was "
              "read (the checkout's root and an outside directory listed, another resolved through)",
              named.read_bytes() if named.is_file() else None, text)
        spec_cache.keep(str(cache), "s", str(root), {"p": "1"}, repo, ext, "V", found=set(), begun=_begun(root))
        check("  the same state again: the same one entry", len(list((cache / "s").glob("*.json"))), 1)
        entries = cache / "s"
        for p in entries.glob("*.json"):
            p.unlink()
        names = [f"{c}.json" for c in "abcdefghijk"]
        for age, name in enumerate(names):
            (entries / name).write_text("{}", encoding="utf-8")
            os.utime(entries / name, ns=(10**9 * (100 - age), 10**9 * (100 - age)))
        (root / "f").write_bytes(b"new")
        spec_cache.keep(str(cache), "s", str(root), {}, {"f": False}, {}, "N", found=set(), begun=_begun(root))
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
    fh.write(f'2 newfstatat(AT_FDCWD, "{os.environ["FAKE_PARENT"]}", {{st_mode=S_IFDIR|0755}}, 0) = 0\\n')
    # Only the control lists it; a mutant's run resolves through it.
    if "--control-only" in cmd:
        fh.write(f'2 openat(AT_FDCWD, "{os.environ["FAKE_LISTED"]}", O_RDONLY|O_DIRECTORY) = 3\\n')
    else:
        fh.write(f'2 newfstatat(AT_FDCWD, "{os.environ["FAKE_LISTED"]}", {{st_mode=S_IFDIR|0755}}, 0) = 0\\n')
    if os.environ.get("FAKE_UNPLACED"):
        fh.write('3 openat(5, "sub", O_RDONLY) = 6\\n')
    if os.environ.get("FAKE_READ"):
        fh.write(f'3 openat(AT_FDCWD, "{os.environ["FAKE_READ"]}", O_RDONLY) = 3\\n')
    # git, in a copy that is a linked worktree, reads the copy's own gitdir.
    if os.path.isfile(os.path.join(tree, ".git")):
        gitdir = open(os.path.join(tree, ".git"), encoding="utf-8").read().split("gitdir: ")[1].strip()
        fh.write(f'3 openat(AT_FDCWD, "{gitdir}/HEAD", O_RDONLY) = 3\\n')
sys.exit(r.returncode)
'''

TEST = "import sys\nimport t\nif t.VALUE != 1:\n    print('FAIL value is one')\n    sys.exit(1)\nprint('ok')\n"
# The same, and when EDIT names a file, appends to it: the checkout edited
# while the runs run; when GONE names one, removes it.
EDIT_TEST = ("import os, sys\nimport t\n"
             "if os.environ.get('EDIT'):\n    open(os.environ['EDIT'], 'a').write('#\\n')\n"
             "if os.environ.get('GONE') and os.path.exists(os.environ['GONE']):\n    os.remove(os.environ['GONE'])\n"
             + TEST[len("import sys\nimport t\n"):])


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
    for d in ("out", "copies", "tmp", "listed"):
        (td / d).mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("MUTATE_")}
    # The runs' temporary directory is td/tmp, so td/outside.txt is an outside
    # input; under the system's /tmp it would be a temporary file, no input.
    # FAKE_PARENT, the checkout's parent, is resolved through as a path's
    # parent is; FAKE_LISTED is a directory a run lists.
    env.update(PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}", FAKE_OUTSIDE=str(outside),
               FAKE_PARENT=str(td), FAKE_LISTED=str(td / "listed"),
               MUTATE_CACHE_DIR=str(td / "cache"), MUTATE_OUT_DIR=str(td / "out"),
               MUTATE_COPY_DIR=str(td / "copies"), XDG_CACHE_HOME=str(td / "xdg"), TMPDIR=str(td / "tmp"))
    # Past spec_cache.MARGIN_NS: the outside inputs are older than any run's start.
    time.sleep(1.1)
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
        check("  with what its runs read: the checkout's paths and the outside ones, a directory resolved "
              "through as one",
              (sorted(doc["repo"]), sorted(doc["ext"]), doc["ext"][env["FAKE_PARENT"]]),
              (["absent.txt", "t.py", "test.py"], sorted([env["FAKE_OUTSIDE"], env["FAKE_PARENT"], env["FAKE_LISTED"]]),
               "dir"))
        rc, out, _ = _spec(tree, spec, env)
        check_true("second run, nothing changed: kept, still a pass to every reader",
                   rc == 0 and "every one killed on target, kept:" in out)
        (td / "proof.log").write_text("a log beside the checkout\n", encoding="utf-8")
        check_true("a file written beside the checkout, in a directory only resolved through: still kept",
                   "kept" in _spec(tree, spec, env)[1])
        (td / "listed" / "new").write_text("", encoding="utf-8")
        check_true("a file added to a directory one run listed and another resolved through: run again",
                   "kept" not in _spec(tree, spec, env)[1])
        # That run keeps nothing if the listing changed less than MARGIN_NS
        # before it began; past the margin, a run keeps it.
        time.sleep(1.1)
        _spec(tree, spec, env)
        check_true("  and kept again", "kept" in _spec(tree, spec, env)[1])
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


SEEN_TEST = ("import os, sys\nimport t\n"
             "open(os.environ['SEEN'], 'a').write(repr(os.environ.get('MUTATE_CACHE_DIR')) + '\\n')\n"
             "if t.VALUE != 1:\n    print('FAIL value is one')\n    sys.exit(1)\nprint('ok')\n")


def test_a_run_never_sees_the_cache() -> None:
    """The runs a cached spec starts get no MUTATE_CACHE_DIR: a test that runs
    mutate-spec itself must not trace and keep into this run's cache."""
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        tree, spec, env = _fixture(td)
        (tree / "test.py").write_text(SEEN_TEST, encoding="utf-8")
        env["SEEN"] = str(td / "seen.txt")
        rc, out, _ = _spec(tree, spec, env)
        check("killed and kept, and the control and the mutant each saw no cache",
              (rc, out.endswith("every one killed on target"),
               (td / "seen.txt").read_text(encoding="utf-8").splitlines()),
              (0, True, ["None", "None"]))


def test_nothing_is_kept_when_a_read_cannot_be_placed_or_moved() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        tree, spec, env = _fixture(td)
        rc, out, err = _spec(tree, spec, dict(env, FAKE_UNPLACED="1"))
        check_true("a traced call it cannot place: passes, nothing kept, and it says which line",
                   rc == 0 and out.endswith("every one killed on target") and not (td / "cache" / "spec").exists()
                   and 'cannot place, the first: 3 openat(5, "sub"' in err)
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        tree, spec, env = _fixture(td)
        (tree / "test.py").write_text(EDIT_TEST, encoding="utf-8")
        (tree / "other.txt").write_text("", encoding="utf-8")
        rc, out, err = _spec(tree, spec, dict(env, EDIT=str(tree / "other.txt")))
        check_true("the checkout edited while its runs ran: passes, nothing kept, and it says why",
                   rc == 0 and out.endswith("every one killed on target") and not (td / "cache" / "spec").exists()
                   and "verdict not kept: the checkout changed while its runs ran" in err)
        rc, out, _ = _spec(tree, spec, env)
        check_true("  left alone, the next run keeps it", rc == 0 and len(list((td / "cache" / "spec").glob("*.json"))) == 1)
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        tree, spec, env = _fixture(td)
        (tree / "test.py").write_text(EDIT_TEST, encoding="utf-8")
        rc, out, err = _spec(tree, spec, dict(env, GONE=env["FAKE_OUTSIDE"]))
        check_true("an outside file a run found, removed by a later run: passes, nothing kept, and it says which",
                   rc == 0 and out.endswith("every one killed on target") and not (td / "cache" / "spec").exists()
                   and f"verdict not kept: {env['FAKE_OUTSIDE']} was there for a run and is gone" in err)


def test_a_runs_own_gitdir_is_no_input() -> None:
    """A copy of a git checkout is a linked worktree with a gitdir of its
    own, removed with it: what its git reads there is not recorded."""
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        tree, spec, env = _fixture(td)
        for args in (("init", "-q"), ("add", "."), ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "t")):
            subprocess.run(["git", "-C", str(tree), *args], check=True, capture_output=True)
        rc, _, err = _spec(tree, spec, env)
        entries = list((td / "cache" / "spec").glob("*.json")) if (td / "cache" / "spec").is_dir() else []
        doc = json.loads(entries[0].read_text(encoding="utf-8")) if len(entries) == 1 else {"repo": {}, "ext": {}}
        check("kept, its reads with no path under any run's gitdir",
              (rc, len(entries), sorted(doc["repo"]), [p for p in doc["ext"] if "/.git/" in p]),
              (0, 1, ["absent.txt", "t.py", "test.py"], []))
        check("  and nothing said about it", err, "")
        extra = tree / ".git" / "extra"
        extra.write_text("", encoding="utf-8")
        (tree / "test.py").write_text(EDIT_TEST, encoding="utf-8")
        rc, _, err = _spec(tree, spec, dict(env, FAKE_READ=str(extra), GONE=str(extra)))
        check_true("a file in the checkout's .git a run found, removed by a later run: nothing kept, and it says which",
                   rc == 0 and f"verdict not kept: {extra} was there for a run and is gone" in err)


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
