#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_scan_image.py
scan-image.py's ROM scan on planted images: where a hit says the file
starts, one hit per signature across chunk boundaries, the saved results,
and a read error part-way keeping what was found.

    python tests/test_scan_image.py
"""

import io
import json
import os
import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, load_script, run
from fake_tools import fake_tools, run_main
from jpexit import NOT_RUN, OK

SCAN = load_script("scan-image.py")

# A binwalk that prints one line and exits with the code in BINWALK_RC.
FAKE_BINWALK = "print('DECIMAL HEXADECIMAL DESCRIPTION')\nsys.exit(int(os.environ.get('BINWALK_RC', '0')))\n"


def _image(size: int, plants: dict[int, bytes]) -> bytes:
    data = bytearray(size)
    for at, magic in plants.items():
        data[at:at + len(magic)] = magic
    return bytes(data)


def test_offsets_are_file_starts() -> None:
    """Game Boy's magic sits at 0x104 within the file, so the file starts 0x104 earlier."""
    data = _image(64 * 1024, {0x1000: b"NES\x1a", 0x2104: b"\xce\xed\x66\x66\xcc\x0d\x00\x0b"})
    hits = {(h.type, h.offset) for h in SCAN.hits_in(io.BytesIO(data), len(data))}
    check("hits", hits, {("NES", 0x1000), ("Game Boy", 0x2000)})


def test_a_signature_in_the_overlap_is_reported_once() -> None:
    """Chunks overlap by 256 bytes: a magic inside the overlap is in two chunks, one hit."""
    chunk = 4096
    data = _image(3 * chunk, {chunk - 100: b"MComprHD", chunk - 3: b"CISO"})
    hits = sorted((h.type, h.magic_at) for h in SCAN.hits_in(io.BytesIO(data), len(data), chunk))
    check("each once, the spanning one found",
          hits, [("CHD (MAME)", chunk - 100), ("CSO (compressed ISO)", chunk - 3)])


def test_a_magic_before_its_own_offset_is_no_file() -> None:
    data = _image(4096, {0x10: b"SEGA"})  # Genesis magic is at 0x100 within a file
    check("no hit", list(SCAN.hits_in(io.BytesIO(data), len(data))), [])


class _Failing(io.BytesIO):
    """Reads fine up to FAIL_AT, then raises, like a failing drive."""

    FAIL_AT = 8192

    def read(self, n: int | None = -1) -> bytes:
        if self.tell() >= self.FAIL_AT:
            raise OSError(5, "Input/output error")
        return super().read(n)


def test_saved_results_and_a_read_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "disk.img"
        img.write_bytes(_image(16384, {0x100: b"NES\x1a", 0x3000: b"LYNX"}))
        code, _ = run_main(SCAN.main, str(img), "--signatures", "roms", "--output", tmp)
        check("a ROM scan that finished: exit", code, OK)
        saved = json.loads((Path(tmp) / "rom_scan_results.json").read_text())
        check("saved", [(r["type"], r["hex_offset"]) for r in saved["results"]],
              [("NES", "0x100"), ("Atari Lynx", "0x3000")])
        check("not truncated", saved["results_truncated"], False)
    failing = _Failing(_image(16384, {0x100: b"NES\x1a", 0x3000: b"LYNX"}))
    found = []
    try:
        for hit in SCAN.hits_in(failing, 16384, 4096):
            found.append(hit)
    except OSError:
        # reason: the error is expected; what matters is what came before it
        pass
    check_true("hits before the error kept", [h.type for h in found] == ["NES"])


def test_a_rom_scan_that_could_not_read_is_not_run() -> None:
    """The source cannot be opened: no scan happened, so not a pass, and the
    report says it stopped rather than "Scan complete"."""
    with tempfile.TemporaryDirectory() as tmp:
        for name, source in (("missing", str(Path(tmp) / "nope.img")), ("a directory", tmp)):
            code, out = run_main(SCAN.main, source, "--signatures", "roms")
            check(f"{name}: exit", code, NOT_RUN)
            check_true(f"{name}: says it stopped", "stopped short" in out)
            check_true(f"{name}: never says complete", "Scan complete" not in out)


def test_binwalk_that_did_not_run_is_not_run() -> None:
    """The default scan is binwalk's: not installed, or exiting non-zero, is a
    scan that did not happen. Control: a binwalk exiting 0 is OK."""
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "disk.img"
        img.write_bytes(bytes(4096))
        with fake_tools():
            code, out = run_main(SCAN.main, str(img))
        check("binwalk not installed: exit", code, NOT_RUN)
        check_true("binwalk not installed: named", "binwalk" in out and "did not run" in out)
        with fake_tools(binwalk=FAKE_BINWALK):
            code, _ = run_main(SCAN.main, str(img))
            check("control, binwalk exits 0: exit", code, OK)
            os.environ["BINWALK_RC"] = "1"
            try:
                code, out = run_main(SCAN.main, str(img))
            finally:
                del os.environ["BINWALK_RC"]
        check("binwalk exits 1: exit", code, NOT_RUN)
        check_true("binwalk exits 1: names the code", "binwalk exited 1" in out)


if __name__ == "__main__":
    sys.exit(run(globals(), "scan-image"))
