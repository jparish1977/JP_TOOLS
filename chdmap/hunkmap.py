"""The CHD v5 hunk map: where each hunk's data is, and how it is stored.

A compressed CHD's map is a 16-byte header followed by a bitstream: first
every hunk's compression type, Huffman-coded with run-length repeats, then
per hunk the fields that type needs. Some types are shorthand ("the last
self reference plus one") and decode to a base type. The map header carries
a CRC16 over the decoded map, so a decode that disagrees with chdman's
encoder is detected rather than reported as data.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import BinaryIO

from chdmap.bits import BitReader, HuffmanDecoder, crc16
from chdmap.header import Header


class Kind(IntEnum):
    CODEC_0 = 0
    CODEC_1 = 1
    CODEC_2 = 2
    CODEC_3 = 3
    NONE = 4  # stored uncompressed
    SELF = 5  # a copy of an earlier hunk in this CHD
    PARENT = 6  # a copy of data in the parent CHD
    # Shorthands, which only appear in the stream:
    RLE_SMALL = 7
    RLE_LARGE = 8
    SELF_0 = 9  # SELF, same target as the last SELF
    SELF_1 = 10  # SELF, the last SELF's target plus one
    PARENT_SELF = 11  # PARENT, at this hunk's own position
    PARENT_0 = 12  # PARENT, same as the last PARENT
    PARENT_1 = 13  # PARENT, the last PARENT plus one hunk


@dataclass(frozen=True)
class Hunk:
    """One map entry. For SELF, `offset` is the hunk copied; for PARENT, a unit
    number in the parent; otherwise a byte offset in this file."""

    number: int
    kind: Kind
    length: int
    offset: int
    crc: int

    def packed(self) -> bytes:
        """The 12-byte form the map CRC is computed over."""
        return (bytes([self.kind]) + self.length.to_bytes(3, "big")
                + self.offset.to_bytes(6, "big") + self.crc.to_bytes(2, "big"))


@dataclass(frozen=True)
class _MapHeader:
    length: int
    first_offset: int
    crc: int
    length_bits: int
    self_bits: int
    parent_bits: int


def _read_map_header(f: BinaryIO, at: int) -> _MapHeader:
    f.seek(at)
    raw = f.read(16)
    if len(raw) < 16:
        raise ValueError("Map header is truncated")
    length, crc = struct.unpack(">I6xH4x", raw)
    return _MapHeader(length, int.from_bytes(raw[4:10], "big"), crc, raw[12], raw[13], raw[14])


def _decode_kinds(bits: BitReader, count: int) -> list[Kind]:
    """Every hunk's type as written, shorthands included, run-length repeats expanded."""
    decoder = HuffmanDecoder(16, 8)
    decoder.import_tree_rle(bits)
    kinds: list[Kind] = []
    last = Kind.CODEC_0
    while len(kinds) < count:
        value = Kind(decoder.decode(bits))
        if value is Kind.RLE_SMALL:
            repeat = 3 + decoder.decode(bits)
        elif value is Kind.RLE_LARGE:
            repeat = 3 + 16 + (decoder.decode(bits) << 4) + decoder.decode(bits)
        else:
            last, repeat = value, 1
        kinds += [last] * min(repeat, count - len(kinds))
    return kinds


class _Cursor:
    """The running state the shorthand types are relative to."""

    def __init__(self, header: Header, mh: _MapHeader) -> None:
        self.header = header
        self.mh = mh
        self.offset = mh.first_offset
        self.last_self = 0
        self.last_parent = 0

    def entry(self, number: int, kind: Kind, bits: BitReader) -> Hunk:
        if kind <= Kind.NONE:
            length = bits.read(self.mh.length_bits) if kind < Kind.NONE else self.header.hunk_size
            hunk = Hunk(number, kind, length, self.offset, bits.read(16))
            self.offset += length
            return hunk
        if kind in (Kind.SELF, Kind.SELF_0, Kind.SELF_1):
            return Hunk(number, Kind.SELF, 0, self._self(kind, bits), 0)
        return Hunk(number, Kind.PARENT, 0, self._parent(number, kind, bits), 0)

    def _self(self, kind: Kind, bits: BitReader) -> int:
        if kind is Kind.SELF:
            self.last_self = bits.read(self.mh.self_bits)
        elif kind is Kind.SELF_1:
            self.last_self += 1
        return self.last_self

    def _parent(self, number: int, kind: Kind, bits: BitReader) -> int:
        units_per_hunk = self.header.hunk_size // self.header.unit_size
        if kind is Kind.PARENT:
            self.last_parent = bits.read(self.mh.parent_bits)
        elif kind is Kind.PARENT_SELF:
            self.last_parent = number * units_per_hunk
        elif kind is Kind.PARENT_1:
            self.last_parent += units_per_hunk
        elif kind is not Kind.PARENT_0:
            raise ValueError(f"Hunk {number}: {kind.name} is not a hunk type")
        return self.last_parent


def read_compressed_map(f: BinaryIO, header: Header) -> list[Hunk]:
    """Decode a compressed CHD's map. Raises ValueError if it does not check out."""
    mh = _read_map_header(f, header.map_offset)
    bits = BitReader(f.read(mh.length))
    kinds = _decode_kinds(bits, header.hunk_count)
    cursor = _Cursor(header, mh)
    hunks = [cursor.entry(n, k, bits) for n, k in enumerate(kinds)]
    if bits.overflowed:
        raise ValueError("Map is truncated: the bitstream ran out")
    if crc16(b"".join(h.packed() for h in hunks)) != mh.crc:
        raise ValueError("Map CRC does not match: this map was not decoded correctly")
    return hunks


def read_uncompressed_map(f: BinaryIO, header: Header) -> list[Hunk]:
    """An uncompressed CHD's map: 4 bytes per hunk, its position in hunks, 0 for absent."""
    f.seek(header.map_offset)
    raw = f.read(4 * header.hunk_count)
    if len(raw) < 4 * header.hunk_count:
        raise ValueError("Map is truncated")
    return [Hunk(n, Kind.NONE, header.hunk_size, slot * header.hunk_size, 0)
            for n, (slot,) in enumerate(struct.iter_unpack(">I", raw))]


def read_map(f: BinaryIO, header: Header) -> list[Hunk]:
    return (read_compressed_map if header.compressed else read_uncompressed_map)(f, header)
