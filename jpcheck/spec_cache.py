"""A mutant spec's verdict, kept while nothing its runs read has changed.

mutate-spec.py runs every spec in full on every proof and CI run, though a
spec's verdict can only change when something its runs read changes. With a
cache directory, each run of a spec (its control and every mutant) runs under
`strace -f`, which names every path the runs and their children open, stat,
list or execute, external tools included. A spec whose every mutant was
killed on target is kept with a fingerprint of each of those paths:

- under the checkout: a file by its content (sha256), a missing path as
  missing;
- anywhere else (the interpreter, installed tools): a file by size and mtime,
  a missing path as missing;
- a directory a run opened, which is how a listing is read, by its listing
  (so a test that globs sees a file added); one it only stat'd, accessed or
  readlink'd, as resolving a path does to every parent of the checkout, as a
  directory, whatever is in it. Otherwise a log written beside the checkout,
  or a dotfile in the home directory, would run every spec again;
- and the spec and both drivers (mutate-spec.py, mutate-on-target.py) by
  content.

A later run reuses the verdict only when every fingerprint still matches;
anything else runs again and is traced afresh. A spec that was not all killed
is never kept, and nothing is kept without strace. Temporary directories, the
run's own copy and gitdir, and /proc, /sys and /dev are not inputs and are
not kept.

WHERE A PATH IS. strace runs with -y, so a call made relative to a directory
(openat(5</repo>, "sub"), as find and anything using *at calls do) prints
that directory, and AT_FDCWD prints the caller's working directory: a name is
joined to the directory its call names. A call that takes no directory (open,
stat, execve) with a relative name is joined to its process's working
directory as its last AT_FDCWD call printed it, forgotten at any chdir. A
traced call whose path it cannot place keeps nothing for that run: a read it
cannot name is a read it cannot fingerprint.

AS IT WAS WHEN THE RUNS READ IT. Each run reads its copy as the checkout was
when that copy was made, and outside paths as they were then, while the
fingerprints are taken once every run has ended. So a verdict is kept only
when nothing moved in between (start(), keep()): the checkout, .git aside,
must be as it was before the first run began (each path's type, size, mtime
and status-change time); and every other path a run read, outside paths and
the checkout's .git, must not have changed status since then (its ctime, a
link's own and its target's; a directory only resolved through, whatever it
holds, aside) nor be gone when a run found it. ctime is compared less one
second, for file systems that keep whole seconds.

NOT AN INPUT: the environment. A run's variables are not part of the key, so
a changed PATH voids a verdict only through the paths the old one led its
runs to look in: a tool put in a directory earlier on the new PATH, which no
run looked in, voids nothing.
"""

import codecs
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import NamedTuple

CALLS = ("open,openat,openat2,creat,execve,execveat,stat,lstat,newfstatat,statx,access,"
         "faccessat,faccessat2,readlink,readlinkat,getdents64,chdir,fchdir")
# A traced call's pid and name, as strace -f writes them.
_CALL = re.compile(r'(?:\[pid\s+)?(\d+)\]?\s+(\w+)\(')
# Its first arguments: the directory one, when the call takes one (AT_FDCWD
# or a number, with -y the directory's path in <>, a ">" in it escaped), then
# its path: quoted, or NULL (statx(fd, NULL, ...) is glibc's fstat).
_ARGS = re.compile(r'(?:(AT_FDCWD|\d+)(?:<((?:[^>\\]|\\.)*)>)?, )?(?:"((?:[^"\\]|\\.)*)"|NULL\b)')
# fchdir's one argument, a directory by its number and path.
_FD = re.compile(r'\d+<((?:[^>\\]|\\.)*)>')
_RESUMED = re.compile(r'(?:\[pid\s+)?(\d+)\]?\s+<\.\.\. \w+ resumed>')
# The calls that open a path: a directory opened is a directory listed.
OPENS = {"open", "openat", "openat2", "creat"}
NOT_INPUTS = ("/proc/", "/sys/", "/dev/")
# Tools' own caches in a checkout: what they hold changes on every run and no
# verdict depends on it (a cache only saves work), so they are no input.
CACHES = {".mypy_cache", ".ruff_cache", ".pytest_cache", "__pycache__", ".hypothesis"}
# ctime on a file system that keeps whole seconds can read up to one second
# before the change it records.
MARGIN_NS = 10**9


def strace_prefix(trace: str) -> list[str] | None:
    """The command prefix that traces a run into TRACE, or None without strace."""
    exe = shutil.which("strace")
    return [exe, "-f", "-qq", "-y", "-e", f"trace={CALLS}", "-o", trace] if exe else None


def mark(paths: dict[str, bool], path: str, opened: bool) -> None:
    """Record PATH in PATHS: opened once any call opened it."""
    if opened:
        paths[path] = True
    else:
        paths.setdefault(path, False)


class Trace(NamedTuple):
    """What a trace's calls name: each path, absolute, with whether any call
    opened it; the paths a call found (it returned no error); and the lines
    naming a path that could not be placed."""
    paths: dict[str, bool]
    found: set[str]
    unplaced: list[str]


def _text(escaped: str) -> str:
    """A path as strace writes it, back to the path: a byte it will not print
    is \\xNN or octal, so back to bytes, then the file system's encoding."""
    raw: bytes = codecs.escape_decode(escaped.encode())[0]
    return os.fsdecode(raw)


# A call's result, after its closing parenthesis and strace's padding.
_RESULT = re.compile(r'\)\s*= (-?\d+|\?)')


def _succeeded(line: str) -> bool:
    """Whether LINE's call returned, and without an error."""
    results = _RESULT.findall(line)
    return bool(results) and results[-1] not in ("-1", "?")


class _Reader:
    """paths_in's state, line by line: each process's working directory as
    its calls last printed it; a call still running (unfinished), by pid;
    relative names from calls with no directory, waiting for their process's
    next AT_FDCWD to say where they were."""

    def __init__(self) -> None:
        self.out: dict[str, bool] = {}
        self.found: set[str] = set()
        self.unplaced: list[str] = []
        self.cwd: dict[str, str] = {}
        self.running: dict[str, str] = {}
        self.held: dict[str, list[tuple[str, str, str]]] = {}

    def place(self, pid: str, call: str, path: str, line: str, held: bool = False) -> None:
        """PATH, named by PID's CALL on LINE, as read; found if it succeeded.
        A call still running is waited for, unless it was HELD: its result
        has already been joined to its line."""
        path = os.path.normpath(path)
        mark(self.out, path, call in OPENS)
        if _succeeded(line):
            self.found.add(path)
        elif "<unfinished ...>" in line and not held:
            self.running[pid] = path

    def moved(self, pid: str, now: str | None) -> None:
        """A chdir or fchdir by PID: threads share a working directory and
        strace names a thread as it names a process, so every one known is
        forgotten and every held name can no longer be placed; PID's is NOW
        when it is known."""
        self.cwd.clear()
        for lines in self.held.values():
            self.unplaced.extend(line for _, _, line in lines)
        self.held.clear()
        if now is not None:
            self.cwd[pid] = now

    def at(self, pid: str, cwd: str) -> None:
        """PID's working directory, as an AT_FDCWD printed it: names it held
        were there (a working directory outlives execve)."""
        self.cwd[pid] = cwd
        for call, name, line in self.held.pop(pid, []):
            self.place(pid, call, os.path.join(cwd, name), line, held=True)

    def resumed(self, line: str) -> None:
        """A '<... call resumed>' LINE: the result of PID's call still running."""
        r = _RESUMED.match(line)
        if not r:
            return
        pid = r.group(1)
        if (path := self.running.pop(pid, None)) and _succeeded(line):
            self.found.add(path)
        elif ((held := self.held.get(pid)) and "<unfinished ...>" in held[-1][2]
              and " resumed>" not in held[-1][2]):
            # A held call's result: joined to its line, read when placed.
            call, name, start = held[-1]
            held[-1] = (call, name, f"{start} {line}")

    def resolve(self, pid: str, call: str, args: re.Match[str], line: str) -> str | None:
        """Where the path in ARGS (PID's CALL, on LINE) is; None when it names
        no file, or is held or cannot be placed (each recorded here)."""
        dirfd, where, name = args.group(1), args.group(2), _text(args.group(3) or "")
        if not name and not _succeeded(line) and "<unfinished ...>" not in line:
            # An empty or NULL path names the descriptor itself, and only
            # when the call worked; one that failed read nothing.
            return None
        if os.path.isabs(name):
            return name
        if where is not None:
            # A descriptor that is no file system path (a pipe, a socket):
            # its own status, or a name under it, reads no file.
            return os.path.join(_text(where), name) if where.startswith("/") else None
        if not name:
            # No path and no descriptor: the call failed and read nothing.
            return None
        if dirfd is None and pid in self.cwd:
            return os.path.join(self.cwd[pid], name)
        if dirfd is None and call != "chdir":
            self.held.setdefault(pid, []).append((call, name, line))
            return None
        self.unplaced.append(line)
        if call == "chdir":
            self.moved(pid, None)
        return None

    def line(self, line: str) -> None:
        m = _CALL.match(line)
        if not m:
            self.resumed(line)
            return
        pid, call = m.groups()
        self.running.pop(pid, None)
        if call == "getdents64":
            return
        if call == "fchdir":
            fd = _FD.match(line, m.end())
            self.moved(pid, _text(fd.group(1)) if fd and _succeeded(line) else None)
            return
        a = _ARGS.match(line, m.end())
        if not a:
            self.unplaced.append(line)
            return
        if a.group(1) == "AT_FDCWD" and a.group(2) is not None:
            self.at(pid, _text(a.group(2)))
        path = self.resolve(pid, call, a, line)
        if path is None:
            return
        self.place(pid, call, path, line)
        if call == "chdir":
            self.moved(pid, os.path.normpath(path) if _succeeded(line) else None)


def paths_in(trace_text: str) -> Trace:
    """Every path a trace's calls name, each placed in the directory its call
    names (WHERE A PATH IS), with whether any of those calls opened it."""
    r = _Reader()
    for line in trace_text.splitlines():
        r.line(line)
    for lines in r.held.values():
        r.unplaced.extend(line for _, _, line in lines)
    return Trace(r.out, r.found, r.unplaced)


def sort_paths(paths: dict[str, bool], tree: str, root: str,
               skip: tuple[str, ...]) -> tuple[dict[str, bool], dict[str, bool]]:
    """(paths in the checkout, relative to it; other inputs, absolute), each
    with whether it was opened. In this order, each as named or resolved
    (strace's -y prints a directory resolved): a path under TREE (the run's
    copy) is the checkout's; one under a SKIP prefix is dropped, though TREE
    is under one and a SKIP (the run's gitdir) may be under ROOT; one under
    ROOT is the checkout's; one not an input (NOT_INPUTS) as named or where
    it resolves (/etc/mtab is /proc/self/mounts, whose mtime is the reading
    process's) is dropped; the rest are outside inputs. A checkout path
    inside a tool's cache (CACHES) is dropped."""
    repo: dict[str, bool] = {}
    ext: dict[str, bool] = {}

    def under(a: str, bases: tuple[str, ...]) -> str | None:
        """The first of BASES, as given or resolved, that A is under."""
        for base in dict.fromkeys((*bases, *(os.path.realpath(b) for b in bases))):
            if a == base or a.startswith(base + os.sep):
                return base
        return None

    for p, opened in paths.items():
        a = os.path.normpath(os.path.join(tree, p))
        base = under(a, (tree,))
        if base is None:
            if under(a, skip) is not None:
                continue
            base = under(a, (root,))
        if base is not None:
            rel = os.path.relpath(a, base)
            if not CACHES & set(Path(rel).parts):
                mark(repo, rel, opened)
        elif not a.startswith(NOT_INPUTS) and not os.path.realpath(a).startswith(NOT_INPUTS):
            mark(ext, a, opened)
    return repo, ext


def _directory(path: Path, opened: bool) -> str:
    """A directory opened, by its listing; one only resolved through, as one."""
    if not opened:
        return "dir"
    return "dir:" + hashlib.sha256("\0".join(sorted(os.listdir(path))).encode()).hexdigest()


def repo_print(root: str, rel: str, opened: bool) -> str:
    """A checkout path's fingerprint: its content, a directory, or absent."""
    p = Path(root) / rel
    try:
        if p.is_dir():
            return _directory(p, opened)
        return "file:" + hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:  # reason: missing or unreadable is a state of its own, compared as such
        return "absent"


def ext_print(path: str, opened: bool) -> str:
    """An outside path's fingerprint: size and mtime, a directory, or absent."""
    p = Path(path)
    try:
        if p.is_dir():
            return _directory(p, opened)
        st = p.stat()
        return f"stat:{st.st_size}:{st.st_mtime_ns}"
    except OSError:  # reason: missing or unreadable is a state of its own, compared as such
        return "absent"


def key(*files: str) -> dict[str, str]:
    """The spec's and drivers' contents, by path."""
    return {f: hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files}


# Each spec keeps its newest few entries, one per state of what it read, so
# branches that differ (a pull request and the default branch) do not
# overwrite each other's.
KEEP = 8


def _matches(doc: dict[str, object], root: str, want: dict[str, str]) -> str | None:
    """DOC's verdict when its sources are WANT and every fingerprint still matches."""
    repo, ext = doc["repo"], doc["ext"]
    if doc["sources"] != want or not isinstance(repo, dict) or not isinstance(ext, dict):
        return None
    # A recorded listing says the run opened that directory.
    if any(repo_print(root, rel, fp.startswith("dir:")) != fp for rel, fp in repo.items()):
        return None
    if any(ext_print(p, fp.startswith("dir:")) != fp for p, fp in ext.items()):
        return None
    return str(doc["verdict"])


def kept(cache: str, stem: str, root: str, want: dict[str, str]) -> str | None:
    """The verdict of a kept entry for STEM whose key is WANT and whose every
    fingerprint still matches; None to run it. In any order: every entry
    that matches is a pass kept for exactly this state. The entry used is
    touched, so pruning by age (keep) keeps what is in use."""
    for entry in (Path(cache) / stem).glob("*.json"):
        try:
            verdict = _matches(json.loads(entry.read_bytes()), root, want)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):  # reason: an entry this cannot read is no match
            continue
        if verdict:
            try:
                os.utime(entry)
            except OSError as e:  # reason: the verdict still stands; what is lost is said
                sys.stderr.write(f"spec_cache: {entry}: kept verdict used but not touched, so pruning may "
                                 f"drop it as unused: {e}\n")
            return verdict
    return None


def snapshot(root: str) -> dict[str, tuple[int, int, int, int]]:
    """Every path in ROOT but .git (what a copy holds; its vendor/ and
    node_modules/ are read through links, so as ROOT holds them), by type,
    size, mtime and ctime, links not followed; a tool's cache (CACHES) aside."""
    out: dict[str, tuple[int, int, int, int]] = {}
    for d, dirs, files in os.walk(root):
        if d == root and ".git" in dirs:
            dirs.remove(".git")
        dirs[:] = [n for n in dirs if n not in CACHES]
        for n in (*dirs, *files):
            p = os.path.join(d, n)
            try:
                st = os.lstat(p)
            except FileNotFoundError:  # reason: gone since it was listed: absent, as a later snapshot will find it
                continue
            out[os.path.relpath(p, root)] = (st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    return out


class Start(NamedTuple):
    """The clock and the checkout before any run began (AS IT WAS)."""
    since: int
    tree: dict[str, tuple[int, int, int, int]]


def start(root: str) -> Start:
    """Now, then ROOT's snapshot: the clock first, so the window only widens."""
    return Start(time.time_ns(), snapshot(root))


def _changed_since(path: str, opened: bool, since: int) -> bool:
    """Whether PATH's status changed at or after SINCE (less MARGIN_NS): a
    link's own and its target's, a file's, a directory's when it was opened;
    a directory only resolved through, whatever it holds, never; a path not
    there, never (found() answers for that)."""
    cutoff = since - MARGIN_NS
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            if st.st_ctime_ns >= cutoff:
                return True
            st = os.stat(path)
    except FileNotFoundError:  # reason: a path not there, or a link to none, has no status to compare; a run that found it is answered by moved(). Any other error propagates, and keep() keeps nothing
        return False
    if stat.S_ISDIR(st.st_mode) and not opened:
        return False
    return st.st_ctime_ns >= cutoff


def moved(root: str, repo: dict[str, bool], ext: dict[str, bool], found: set[str],
          begun: Start) -> str | None:
    """Why what the runs read may differ from what is fingerprinted now, or
    None (AS IT WAS). FOUND holds the checkout's paths relative and others
    absolute, as REPO and EXT do."""
    if snapshot(root) != begun.tree:
        return "the checkout changed while its runs ran"
    others = [(os.path.join(root, r), r, o) for r, o in repo.items() if Path(r).parts[:1] == (".git",)]
    others += [(p, p, o) for p, o in ext.items()]
    for path, name, opened in others:
        if _changed_since(path, opened, begun.since):
            return f"{path} changed while its runs ran"
        if name in found and not os.path.lexists(path):
            return f"{path} was there for a run and is gone"
    return None


def keep(cache: str, stem: str, root: str, want: dict[str, str], repo: dict[str, bool],
         ext: dict[str, bool], verdict: str, *, found: set[str], begun: Start) -> str | None:
    """Keep STEM's VERDICT with the fingerprints of what its runs read, then
    only the newest KEEP of STEM's entries (by modification time); unless
    what they read moved since BEGUN (moved()), when nothing is kept and the
    reason is returned.

    An entry is named by its content, so it needs no temporary file: a
    reader that meets one half written cannot parse it and passes it over
    (kept), and two runs keeping the same state write the same bytes. A
    removal that loses a race with another run's raises, as any OSError
    here does; the caller keeps nothing then and runs on."""
    if why := moved(root, repo, ext, found, begun):
        return why
    doc = {"sources": want, "verdict": verdict,
           "repo": {r: repo_print(root, r, repo[r]) for r in sorted(repo)},
           "ext": {p: ext_print(p, ext[p]) for p in sorted(ext)}}
    text = json.dumps(doc, sort_keys=True).encode()
    where = Path(cache) / stem
    where.mkdir(parents=True, exist_ok=True)
    (where / f"{hashlib.sha256(text).hexdigest()[:16]}.json").write_bytes(text)
    entries = sorted(where.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for old in entries[KEEP:]:
        old.unlink()
    return None
