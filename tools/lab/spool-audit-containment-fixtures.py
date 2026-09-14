#!/usr/bin/env python3
"""Build the fixtures that answer "can --purge reach outside the directory it
was told about, and does it claim more than it achieved".

WHY THIS EXISTS. Reviewing JP_TOOLS PR #37 across three rounds, the two
questions that actually decide whether spool-audit is safe are not readable
from the source. Round 1 I read the anchoring and reported containment as
BELIEVED, not measured, because building the fixtures by hand looked like more
work than it was. Round 2 I built them in about four minutes and the answer
changed from an opinion into a result.

WHAT IT COST TO LEARN, so the next person does not re-derive it:

  - --purge only removes files whose FIRST BYTES are print data and which
    carry no job id. A fixture file must start with one of DOCUMENT_MAGIC
    ("%!PS", "%PDF", "\x1b%-12345X", "\x04%!", "@PJL", "\x1b*") or it is
    classified UNRECOGNISED and never purged, and the test proves nothing.
  - The interesting cases are all about a name that is not what it looks
    like. A plain file is the control, not the test.
  - The hard-link case is the one most likely to be got wrong by an
    implementation, because unlinking succeeds and destroys nothing. Put the
    OTHER name outside the tree so "did the content die" is answerable.
  - A symlinked SUBDIRECTORY is a separate case from a symlinked FILE. An
    implementation can refuse the second and still walk through the first.

USAGE

    python3 spool-audit-containment-fixtures.py /tmp/probe
    python3 spool-audit.py --spool /tmp/probe/spool --purge
    python3 spool-audit-containment-fixtures.py /tmp/probe --verify

`--verify` prints, for each case, whether the content still exists. Every line
except the control should say SURVIVED. A DESTROYED line on any of the outside
cases is a containment failure and blocks.

EXPECT THE PURGE RUN TO EXIT 2, NOT 0, AND DO NOT READ THAT AS FAILURE.
Verified against spool-audit.py:291, not assumed: exit 2 is "unknown", returned
whenever `not complete` -- and this fixture deliberately plants two symlinks
that the audit refuses to examine, so the run is INCOMPLETE by construction.
That is the fixture working. Exit 0 from a purge over these fixtures would mean
the symlinks were examined, which is the failure this harness exists to catch.

NOT A CLAIM ABOUT ROOT. Everything here runs unprivileged against a scratch
directory. It does not exercise a live /var/spool/cups with cupsd writing
concurrently, and that gap is real.
"""
import os
import sys

MAGIC = "%PDF-1.4 "


def build(root: str) -> None:
    spool, outside, elsewhere = (os.path.join(root, d)
                                 for d in ("spool", "outside", "elsewhere"))
    for d in (os.path.join(spool, "tmp"), outside, elsewhere,
              os.path.join(outside, "subdir")):
        os.makedirs(d, exist_ok=True)

    def doc(path: str, what: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(MAGIC + what + "\n")

    # control: a genuine in-spool document. SHOULD be removed.
    doc(os.path.join(spool, "tmp", "d00001-001"), "plain in-spool document")
    # content outside the tree, reachable only through a symlink inside it
    doc(os.path.join(outside, "secret.pdf"), "content OUTSIDE the spool")
    os.symlink(os.path.join(outside, "secret.pdf"),
               os.path.join(spool, "tmp", "d00002-001"))
    # hard link: unlinking the spool name destroys nothing
    doc(os.path.join(elsewhere, "keepme.pdf"), "hard-linked content")
    os.link(os.path.join(elsewhere, "keepme.pdf"),
            os.path.join(spool, "tmp", "d00003-001"))
    # a symlinked DIRECTORY, distinct from a symlinked file
    doc(os.path.join(outside, "subdir", "d00004-001"), "inside a linked dir")
    os.symlink(os.path.join(outside, "subdir"),
               os.path.join(spool, "tmp", "linkeddir"))
    print(f"built under {root}; now run --purge against {spool}")


def verify(root: str) -> int:
    cases = [
        ("control, in-spool plain file", os.path.join(root, "spool", "tmp", "d00001-001"), False),
        ("content outside, via symlink", os.path.join(root, "outside", "secret.pdf"), True),
        ("hard-linked content elsewhere", os.path.join(root, "elsewhere", "keepme.pdf"), True),
        ("file inside symlinked dir", os.path.join(root, "outside", "subdir", "d00004-001"), True),
    ]
    bad = 0
    for label, path, must_survive in cases:
        alive = os.path.exists(path)
        if must_survive:
            state = "SURVIVED" if alive else "*** DESTROYED -- CONTAINMENT FAILURE ***"
            bad += 0 if alive else 1
        else:
            state = "removed (expected)" if not alive else "still present"
        print(f"  {label:32} {state}")
    return 1 if bad else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    # A bare --help used to be taken as a positional path, so the tool built a
    # directory literally called "--help". Caught by jp-tools-advisor running
    # it. Positional paths are now anything that is not a flag.
    if not args or "-h" in args or "--help" in args:
        sys.exit(__doc__)
    paths = [a for a in args if not a.startswith("-")]
    if not paths:
        sys.exit("need a target directory\n" + __doc__)
    target = paths[0]
    sys.exit(verify(target) if "--verify" in args else (build(target) or 0))
