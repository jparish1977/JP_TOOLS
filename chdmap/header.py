"""The CHD v5 header: the 124 bytes at the start of the file."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO

MAGIC = b"MComprHD"
_LAYOUT = struct.Struct(">8sII4s4s4s4sQQQII20s20s20s")


@dataclass(frozen=True)
class Header:
    version: int
    codecs: tuple[str, ...]  # empty for an uncompressed CHD
    logical_size: int
    map_offset: int
    meta_offset: int
    hunk_size: int
    unit_size: int
    raw_sha1: str
    sha1: str
    parent_sha1: str

    @property
    def hunk_count(self) -> int:
        return (self.logical_size + self.hunk_size - 1) // self.hunk_size

    @property
    def compressed(self) -> bool:
        return bool(self.codecs)


def read_header(f: BinaryIO) -> Header:
    """Parse the header at the start of F. Raises ValueError for anything but CHD v5."""
    raw = f.read(_LAYOUT.size)
    if len(raw) < _LAYOUT.size or not raw.startswith(MAGIC):
        raise ValueError(f"Not a CHD file (starts {raw[:8]!r})")
    (_magic, _length, version, *codecs, logical, map_offset, meta_offset,
     hunk_size, unit_size, raw_sha1, sha1, parent_sha1) = _LAYOUT.unpack(raw)
    if version != 5:
        raise ValueError(f"Only CHD v5 is supported, this is v{version}")
    if hunk_size == 0 or unit_size == 0:
        raise ValueError("Hunk or unit size is 0")
    return Header(
        version=version,
        codecs=tuple(c.decode("ascii", "replace") for c in codecs if c != b"\0\0\0\0"),
        logical_size=logical,
        map_offset=map_offset,
        meta_offset=meta_offset,
        hunk_size=hunk_size,
        unit_size=unit_size,
        raw_sha1=raw_sha1.hex(),
        sha1=sha1.hex(),
        parent_sha1=parent_sha1.hex(),
    )
