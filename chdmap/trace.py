"""Duplicate hunks, grouped by the hunk they copy, and traced to tar members.

Tracing assumes the CHD was made from the tar with createraw, so a hunk's
logical offset is a byte offset in the tar. A hunk is attributed to the
member holding its first byte.
"""

from __future__ import annotations

import tarfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from chdmap.hunkmap import Hunk, Kind


@dataclass(frozen=True)
class Member:
    name: str
    offset: int  # where its data starts in the tar
    size: int


@dataclass
class Group:
    """The hunks that are copies of TARGET."""

    target: int
    target_offset: int
    referrers: list[int]
    target_file: str | None = None
    referencing_files: list[str] = field(default_factory=list)

    @property
    def cross_file(self) -> bool:
        """Does another member hold a copy of this member's data?"""
        return self.target_file is not None and any(
            f != self.target_file for f in self.referencing_files)


def tar_members(tar: Path) -> list[Member]:
    """Regular files in TAR, with where their data sits, in tar order."""
    with tarfile.open(tar, "r") as t:
        return [Member(m.name, m.offset_data, m.size) for m in t.getmembers() if m.isfile()]


def member_at(offset: int, members: list[Member]) -> str | None:
    """The member whose data holds byte OFFSET, or None (a header or padding)."""
    for m in members:
        if m.offset <= offset < m.offset + m.size:
            return m.name
    return None


def self_ref_groups(hunks: list[Hunk], hunk_size: int,
                    members: list[Member] | None = None) -> list[Group]:
    """Self references grouped by target, most-copied first."""
    by_target: dict[int, list[int]] = defaultdict(list)
    for h in hunks:
        if h.kind is Kind.SELF:
            by_target[h.offset].append(h.number)
    groups = [Group(t, t * hunk_size, refs) for t, refs in sorted(by_target.items())]
    if members is not None:
        for g in groups:
            g.target_file = member_at(g.target_offset, members)
            found = (member_at(r * hunk_size, members) for r in g.referrers)
            g.referencing_files = sorted({f for f in found if f is not None})
    groups.sort(key=lambda g: len(g.referrers), reverse=True)
    return groups


def shared_pairs(groups: list[Group]) -> Counter[tuple[str, str]]:
    """How many duplicate groups each pair of different members shares."""
    pairs: Counter[tuple[str, str]] = Counter()
    for g in groups:
        if g.target_file is None:
            continue
        for f in g.referencing_files:
            if f != g.target_file:
                a, b = sorted((g.target_file, f))
                pairs[(a, b)] += 1
    return pairs
