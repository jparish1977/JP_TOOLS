#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_chd_hunkmap.py
chd-hunkmap.py: decoding CHD v5 maps as chdman writes them. The fixtures in
tests/fixtures/chd/ were made by chdman (make_fixtures.py there); the counts
asserted here are chdman's own, and every compressed map also has to pass
the CRC16 it carries.

    python tests/test_chd_hunkmap.py
"""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile

from _harness import ROOT, check, check_true, run
from chdmap.bits import BitReader, HuffmanDecoder, crc16
from chdmap.header import Header, read_header
from chdmap.hunkmap import Hunk, Kind, read_map
from chdmap.trace import self_ref_groups, shared_pairs, tar_members

FIX = ROOT / "tests" / "fixtures" / "chd"


def _read(name: str) -> tuple[Header, list[Hunk]]:
    with (FIX / name).open("rb") as f:
        header = read_header(f)
        return header, read_map(f, header)


def _cli(*args: str) -> tuple[int, str]:
    spec = importlib.util.spec_from_file_location("chd_hunkmap", ROOT / "chd-hunkmap.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = module.main(list(args))
    return code, out.getvalue()


def test_crc16_is_ccitt_false() -> None:
    check("check value", crc16(b"123456789"), 0x29B1)


def test_canonical_codes_start_from_the_longest() -> None:
    """Lengths 1,2,3,3 give codes 1, 01, 000, 001, as MAME assigns them."""
    decoder = HuffmanDecoder(4, 8)
    decoder._assign_canonical([1, 2, 3, 3])
    bits = BitReader(bytes([0b10100000, 0b10000000]))  # 1 01 000 001
    check("symbols", [decoder.decode(bits) for _ in range(4)], [0, 1, 2, 3])


def test_self_references() -> None:
    header, hunks = _read("selfref.chd")
    check("hunks", header.hunk_count, 16)
    check("codecs", header.codecs, ("lzma", "zlib", "huff", "flac"))
    check("compressed", [h.number for h in hunks if h.kind is Kind.CODEC_0], [0, 1, 2, 3])
    check("targets", [(h.number, h.offset) for h in hunks if h.kind is Kind.SELF],
          [(4, 0), (5, 0), (6, 0), (7, 0), (8, 0), (9, 1), (10, 2), (11, 3),
           (12, 1), (13, 1), (14, 2), (15, 3)])


def test_parent_references_are_units() -> None:
    """A child hunk at its own position in the parent is unit number * 8 here."""
    header, hunks = _read("child.chd")
    check("units per hunk", header.hunk_size // header.unit_size, 8)
    check("own data", [h.number for h in hunks if h.kind is not Kind.PARENT], [9])
    check("parent units", [h.offset for h in hunks if h.kind is Kind.PARENT],
          [n * 8 for n in range(16) if n != 9])


def test_uncompressed_map_is_an_offset_table() -> None:
    header, hunks = _read("plain.chd")
    check("no codecs", header.compressed, False)
    check("kinds", {h.kind for h in hunks}, {Kind.NONE})
    offsets = [h.offset for h in hunks]
    check_true("byte offsets, distinct, hunk aligned",
               len(set(offsets)) == 4 and all(o and o % header.hunk_size == 0 for o in offsets))


def test_a_map_that_fails_its_crc_is_an_error_not_a_summary() -> None:
    """The stored CRC is changed and nothing else, so the map still decodes:
    only the CRC comparison can catch it."""
    raw = bytearray((FIX / "selfref.chd").read_bytes())
    with (FIX / "selfref.chd").open("rb") as f:
        at = read_header(f).map_offset
    raw[at + 10] ^= 0x01  # the map header's CRC16 is bytes 10-11
    with tempfile.NamedTemporaryFile(suffix=".chd") as bad:
        bad.write(raw)
        bad.flush()
        code, out = _cli(bad.name)
    check("exit", code, 1)
    check_true("said", "Map CRC does not match" in out)


def test_trace_to_tar_members() -> None:
    header, hunks = _read("members.chd")
    groups = self_ref_groups(hunks, header.hunk_size, tar_members(FIX / "members.tar"))
    shared = {(g.target_file, tuple(g.referencing_files)) for g in groups if g.cross_file}
    check("cross-file", shared, {("a.bin", ("c.bin",)), ("a.bin", ("b.bin",))})
    check("pairs", dict(shared_pairs(groups)), {("a.bin", "c.bin"): 1, ("a.bin", "b.bin"): 1})


def test_cli_json_is_only_json() -> None:
    code, out = _cli(str(FIX / "selfref.chd"), "--self-refs", "--json")
    check("exit", code, 0)
    doc = json.loads(out)
    check("self refs", doc["total_self_refs"], 12)
    check("groups", [g["target"] for g in doc["self_ref_groups"]], [0, 1, 2, 3])


def test_cli_refuses_what_is_not_a_chd() -> None:
    check("tar", _cli(str(FIX / "members.tar"))[0], 1)
    check("missing", _cli(str(FIX / "nope.chd"))[0], 1)


if __name__ == "__main__":
    sys.exit(run(globals(), "chd hunkmap"))
