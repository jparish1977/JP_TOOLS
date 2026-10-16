"""A mutant spec's verdict, kept while nothing its runs read has changed.

mutate-spec.py runs every spec in full on every proof and CI run, though a
spec's verdict can only change when something its runs read changes. With a
cache directory, each run of a spec (its control and every mutant) runs under
`strace -f`, which names every path the runs and their children open, stat,
list or execute, external tools included. A spec whose every mutant was
killed on target is kept with a fingerprint of each of those paths:

- under the checkout: a file by its content (sha256), a directory by its
  listing (so a test that globs sees a file added), a missing path as missing;
- anywhere else (the interpreter, installed tools): a file by size and mtime,
  a directory by its listing, a missing path as missing;
- and the spec and both drivers (mutate-spec.py, mutate-on-target.py) by
  content.

A later run reuses the verdict only when every fingerprint still matches;
anything else runs again and is traced afresh. A spec that was not all killed
is never kept, and nothing is kept without strace. Temporary directories, the
run's own copy and /proc, /sys and /dev are not inputs and are not kept.
"""

import codecs
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

CALLS = ("open,openat,openat2,creat,execve,execveat,stat,lstat,newfstatat,statx,access,"
         "faccessat,faccessat2,readlink,readlinkat,getdents64")
# The first quoted argument of a traced call: its path. getdents64 names a
# file descriptor, not a path, and is traced only so a listing shows as the
# open of its directory, which openat already gives.
_PATH = re.compile(r'^(?:\[pid\s+)?\d+\]?\s+\w+\((?:AT_FDCWD, |\d+<[^>]*>, )?"((?:[^"\\]|\\.)*)"')
NOT_INPUTS = ("/proc/", "/sys/", "/dev/")
# Tools' own caches in a checkout: what they hold changes on every run and no
# verdict depends on it (a cache only saves work), so they are no input.
CACHES = {".mypy_cache", ".ruff_cache", ".pytest_cache", "__pycache__", ".hypothesis"}


def strace_prefix(trace: str) -> list[str] | None:
    """The command prefix that traces a run into TRACE, or None without strace."""
    exe = shutil.which("strace")
    return [exe, "-f", "-qq", "-e", f"trace={CALLS}", "-o", trace] if exe else None


def paths_in(trace_text: str) -> set[str]:
    """Every path a trace's calls name, unescaped."""
    out = set()
    for line in trace_text.splitlines():
        m = _PATH.match(line)
        if m:
            # strace writes a byte it will not print as \xNN: back to bytes,
            # then the file system's own encoding.
            raw: bytes = codecs.escape_decode(m.group(1).encode())[0]
            out.add(os.fsdecode(raw))
    return out


def sort_paths(paths: set[str], tree: str, root: str, skip: tuple[str, ...]) -> tuple[set[str], set[str]]:
    """(paths in the checkout, relative to it; other inputs, absolute). A path
    under TREE (the run's copy) or ROOT is the checkout's; one under a SKIP
    prefix, or not an input (NOT_INPUTS), is dropped, and so is a checkout
    path inside a tool's cache (CACHES). A relative path is taken from TREE,
    where the runner runs."""
    repo, ext = set(), set()
    for p in paths:
        a = os.path.normpath(os.path.join(tree, p))
        for base in (tree, root):
            if a == base or a.startswith(base + os.sep):
                rel = os.path.relpath(a, base)
                if not CACHES & set(Path(rel).parts):
                    repo.add(rel)
                break
        else:
            if not a.startswith(NOT_INPUTS) and not any(a == s or a.startswith(s + os.sep) for s in skip):
                ext.add(a)
    return repo, ext


def _listing(path: Path) -> str:
    return "dir:" + hashlib.sha256("\0".join(sorted(os.listdir(path))).encode()).hexdigest()


def repo_print(root: str, rel: str) -> str:
    """A checkout path's fingerprint: its content, its listing, or absent."""
    p = Path(root) / rel
    try:
        if p.is_dir():
            return _listing(p)
        return "file:" + hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:  # reason: missing or unreadable is a state of its own, compared as such
        return "absent"


def ext_print(path: str) -> str:
    """An outside path's fingerprint: size and mtime, its listing, or absent."""
    p = Path(path)
    try:
        if p.is_dir():
            return _listing(p)
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


def _entries(cache: str, stem: str) -> list[Path]:
    """STEM's kept entries, newest first."""
    try:
        found = list((Path(cache) / stem).glob("*.json"))
    except OSError:  # reason: no cache directory yet is no entry
        return []
    return sorted(found, key=lambda p: p.stat().st_mtime_ns, reverse=True)


def _matches(doc: dict[str, object], root: str, want: dict[str, str]) -> str | None:
    """DOC's verdict when its sources are WANT and every fingerprint still matches."""
    repo, ext = doc["repo"], doc["ext"]
    if doc["sources"] != want or not isinstance(repo, dict) or not isinstance(ext, dict):
        return None
    if any(repo_print(root, rel) != fp for rel, fp in repo.items()):
        return None
    if any(ext_print(p) != fp for p, fp in ext.items()):
        return None
    return str(doc["verdict"])


def kept(cache: str, stem: str, root: str, want: dict[str, str]) -> str | None:
    """The verdict of a kept entry for STEM whose key is WANT and whose every
    fingerprint still matches; None to run it."""
    for entry in _entries(cache, stem):
        try:
            verdict = _matches(json.loads(entry.read_bytes()), root, want)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):  # reason: an entry this cannot read is no match
            continue
        if verdict:
            return verdict
    return None


def keep(cache: str, stem: str, root: str, want: dict[str, str], repo: set[str], ext: set[str],
         verdict: str) -> None:
    """Keep STEM's VERDICT with the fingerprints of what its runs read, named
    by its content and written whole (a file beside it renamed in), so a
    reader never sees half; then only the newest KEEP of STEM's entries stay."""
    doc = {"sources": want, "verdict": verdict,
           "repo": {r: repo_print(root, r) for r in sorted(repo)},
           "ext": {p: ext_print(p) for p in sorted(ext)}}
    text = json.dumps(doc, sort_keys=True)
    where = Path(cache) / stem
    where.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=where, prefix=".new.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, where / f"{hashlib.sha256(text.encode()).hexdigest()[:16]}.json")
    for old in _entries(cache, stem)[KEEP:]:
        old.unlink(missing_ok=True)
