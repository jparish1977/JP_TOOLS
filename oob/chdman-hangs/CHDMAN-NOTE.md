# chdman hangs: what upstream says, and what it does not

Kept because `chd.py` runs chdman under a watchdog (`platformtools.tools.run_watched`,
`chdtool/chdman.py`), and the question it raises is whether a newer chdman
makes the hang go away. PROVENANCE.tsv records where and when each page was
fetched, with its hash.

## The hang this find is about

chdman 0.264 (Ubuntu 24.04's `mame-tools`) making a child CHD against a parent
(`createraw --outputparent`) sometimes never finishes: one thread spins at
100% while every worker waits on a futex, and the output stays at its 124-byte
header for as long as the process is left. `tools/lab/chdman-delta-hang.sh`
counts it: 0 hung in 100 runs one at a time on an idle 32-thread box; 2 hung
in 320 runs with eight copies at once on another.

## What the pages say

- `mametesters-04709.html`: "chdman: random deadlocks", MAME Testers #04709.
  Still open, no fixed-in version. The hang it describes is in the progress
  output's flush during `createcd`; it does not name `createraw` or a parent,
  so it may be another bug.
- `mame-issue-9006-chdman-threading.json`: "Fix chdman threading on cpus with
  many cores", merged 2021-12-17. A race in the work queue left threads
  unwoken when tasks were queued quickly; it also made chdman use every
  thread. Its symptom is the closest to the one above, but 0.264 already
  carries it.
- `mame-issue-12948-cue-toc-gdi-parsers.json`: a hang at nan% on a cue or toc
  with no final newline, merged 2024-11-08. Another input, another hang.
- `mamedev-release.html`: the current release, **0.289** (the page gives no
  date).

The two pull requests are GitHub's issue records for them (the API's
issues endpoint), not their web pages: the pages carry per-response tokens
a repository should not hold, and the pulls endpoint's record carries each
repository's licence under a field named key, which reads as a credential.
The issue record has the title, the text, the author and the merge date. Two secondary pages
dating 0.288 and 0.289 are not kept: one refused the fetch (HTTP 403), the
other carries cookie and token values in its scripts, and mamedev's own page
settles the version.

## What none of them answers, measured

No release note or issue found says this hang is fixed after 0.264, so it
was measured: chdman 0.289 built from source (`tools/lab/chdman-build.sh`)
against the packaged 0.264, the repro run as eight copies at once, 200 runs
each, on one 32-thread box, one version straight after the other. **0.289:
13 of 1600 children hung; 0.264: 7 of 1600**, every one stuck at the
124-byte header. A newer chdman does not remove it, so `chd.py` keeps its
watchdog (kill, say so, run once more) whichever version is installed.

## Where it hangs

`backtrace-0.289-1.txt` to `-4.txt` are four hangs of chdman 0.289 built
with symbols, caught by `tools/lab/chdman-hang-backtrace.sh` (sixteen at
once; the first hung at its 8th run): every thread's stack, all four alike.
The main thread loops in `compress_common` (`src/tools/chdman.cpp:1513`)
calling `progress()`, and all 17 workers wait in `worker_thread_entry`
(`src/osd/osdsync.cpp:686`). They are this project's own output, not
fetched, so they have no PROVENANCE rows; the home directory in their paths
is written as `~`. `UPSTREAM-ISSUE-DRAFT.md` is a report built from them,
not filed.
