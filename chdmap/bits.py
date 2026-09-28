"""The v5 map's bit-level encodings: an MSB-first bitstream, the canonical
Huffman code its compression types are written in, and the CRC16 that seals
the decoded map. Each follows MAME's implementation of the same name.
"""

from __future__ import annotations


class BitReader:
    """Read bits most-significant first. Past the end, bits read as 0, as in MAME."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0  # in bits

    def read(self, count: int) -> int:
        result = 0
        for _ in range(count):
            byte = self.pos >> 3
            bit = (self.data[byte] >> (7 - (self.pos & 7))) & 1 if byte < len(self.data) else 0
            result = (result << 1) | bit
            self.pos += 1
        return result

    @property
    def overflowed(self) -> bool:
        return self.pos > len(self.data) * 8


class HuffmanDecoder:
    """A canonical Huffman code over NUM_CODES symbols, at most MAX_BITS long.

    The code lengths are read from the stream (import_tree_rle), the codes are
    assigned canonically from them, and symbols are decoded one at a time.
    """

    def __init__(self, num_codes: int = 16, max_bits: int = 8) -> None:
        self.num_codes = num_codes
        self.max_bits = max_bits
        self.codes: dict[tuple[int, int], int] = {}  # (length, code) -> symbol

    def import_tree_rle(self, bits: BitReader) -> None:
        """Read the code lengths: raw values, with 1 escaping a repeat. Raises ValueError."""
        width = 5 if self.max_bits >= 16 else 4 if self.max_bits >= 8 else 3
        lengths: list[int] = []
        while len(lengths) < self.num_codes:
            value = bits.read(width)
            if value != 1:
                lengths.append(value)
                continue
            value = bits.read(width)
            if value == 1:
                lengths.append(1)
                continue
            repeat = bits.read(width) + 3
            if len(lengths) + repeat > self.num_codes:
                raise ValueError("Huffman tree: a repeat runs past the last symbol")
            lengths += [value] * repeat
        self._assign_canonical(lengths)

    def _assign_canonical(self, lengths: list[int]) -> None:
        if max(lengths) > self.max_bits:
            raise ValueError("Huffman tree: a code is longer than the limit")
        histogram = [0] * 33
        for n in lengths:
            histogram[n] += 1
        start = 0
        for length in range(32, 0, -1):
            total = start + histogram[length]
            if length != 1 and total % 2:
                raise ValueError("Huffman tree: the code lengths do not form a code")
            histogram[length] = start
            start = total >> 1
        self.codes = {}
        for symbol, n in enumerate(lengths):
            if n > 0:
                self.codes[(n, histogram[n])] = symbol
                histogram[n] += 1

    def decode(self, bits: BitReader) -> int:
        """One symbol. Raises ValueError on a bit pattern that is no code."""
        code = 0
        for length in range(1, self.max_bits + 1):
            code = (code << 1) | bits.read(1)
            symbol = self.codes.get((length, code))
            if symbol is not None:
                return symbol
        raise ValueError("Huffman stream: no code matches")


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (polynomial 0x1021, initial 0xFFFF), MAME's crc16."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc
