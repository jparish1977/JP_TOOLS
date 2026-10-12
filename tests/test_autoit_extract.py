#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_autoit_extract.py
autoit-extract.py, in-process, against PE files built byte by byte here.

WHAT IS CHECKED
    - pe_facts: what is a PE at all (MZ, length, the PE signature, the COFF
      header fitting), each at its exact boundary; machine, section names,
      the stub's build stamp, and where the last section ends (the overlay);
      a truncated section table reports the sections that fit.
    - find_payload: the AU3 marker, its tag, and whether it sits in the
      overlay (at or after the last section's end) or in a resource.
    - decode: hands autoit-ripper's parser the payload and the version the
      tag names (EA05, else EA06), and an empty parse is no records; with
      autoit-ripper missing it exits naming the install line. A stand-in
      package is used: the real one is an optional extra, not in CI.
    - scan: one finding per line, the first indicator kind that matches,
      case-blind; Func/EndFunc/comment lines skipped; line numbers from 1.
    - main: the report, the scripts written under safe names, and each way
      it stops (not a PE, no marker, nothing decoded).

    python3 tests/test_autoit_extract.py
"""

import contextlib
import io
import struct
import sys
import tempfile
import types
from pathlib import Path
from typing import Any
from unittest import mock

from _harness import check, check_true, load_script, run

ae = load_script("autoit-extract.py")

TAG = b"AU3!EA06"


def _pe(sections: list[tuple[bytes, int, int]], machine: int = 0x14C, stamp: int = 0,
        lfanew: int = 0x40, nsec: int | None = None) -> bytes:
    """MZ header, PE signature, COFF header (no optional header), and a
    section table of (name, raw size, raw pointer)."""
    hdr = bytearray(lfanew)
    hdr[0:2] = b"MZ"
    coff = b"PE\0\0" + struct.pack("<HHIIIHH", machine, len(sections) if nsec is None else nsec,
                                   stamp, 0, 0, 0, 0)
    table = b"".join(name.ljust(8, b"\0") + struct.pack("<IIII", 0, 0, size, ptr) + bytes(16)
                     for name, size, ptr in sections)
    data = bytearray(hdr + coff + table)
    struct.pack_into("<I", data, 0x3C, lfanew)
    return bytes(data)


def _sample() -> tuple[bytes, int]:
    """A PE whose sections end at 0x300, then an overlay holding the payload."""
    pe = _pe([(b".text", 0x100, 0x200), (b".rsrc", 0x80, 0x100)])
    body = pe.ljust(0x300, b"\0")
    return body + ae.MARKER + TAG + b"PAYLOAD", 0x300


def test_pe_facts() -> None:
    pe = _pe([(b".text", 0x100, 0x200), (b".rsrc", 0x80, 0x100)], stamp=1_000_000_000)
    facts, end = ae.pe_facts(pe)
    check("a PE: machine, sections, stub stamp, size", facts,
          {"machine": "x86", "sections": ".text, .rsrc", "stub built": "2001-09-09 01:46:40 UTC",
           "file size": len(pe)})
    check("  the overlay starts where the furthest section ends, whichever is listed first", end, 0x300)
    check("x64", ae.pe_facts(_pe([], machine=0x8664))[0]["machine"], "x64")
    check("any other machine, as hex", ae.pe_facts(_pe([], machine=0x1C4))[0]["machine"], "0x1c4")
    check("no sections: none named, the overlay is the whole file",
          ae.pe_facts(_pe([], stamp=0)), ({"machine": "x86", "sections": "", "stub built":
                                          "1970-01-01 00:00:00 UTC", "file size": 0x58}, 0))


def test_what_is_not_a_pe() -> None:
    pe = _pe([])
    check("no MZ", ae.pe_facts(b"ZM" + pe[2:]), None)
    check("MZ but shorter than a DOS header", ae.pe_facts(pe[:0x3F]), None)
    check("no PE signature where e_lfanew points", ae.pe_facts(pe[:0x40] + b"NE\0\0" + pe[0x44:]), None)
    check("the COFF header cut one byte short", ae.pe_facts(pe[:0x40 + 23]), None)
    check_true("the COFF header exactly fitting is a PE", ae.pe_facts(pe[:0x40 + 24]) is not None)
    tiny = bytearray(0x40)
    tiny[0:2], tiny[4:8] = b"MZ", b"PE\0\0"
    struct.pack_into("<I", tiny, 0x3C, 4)
    check_true("a PE exactly a DOS header long (header inside it) is a PE",
               ae.pe_facts(bytes(tiny)) is not None)


def test_a_truncated_section_table() -> None:
    whole = _pe([(b".a", 0x10, 0x1000), (b".b", 0x10, 0x2000)])
    check("the table cut inside the second section: the first is reported",
          ae.pe_facts(whole[:-1]), ({"machine": "x86", "sections": ".a", "stub built":
                                     "1970-01-01 00:00:00 UTC", "file size": len(whole) - 1}, 0x1010))
    check("the second exactly fitting: both", ae.pe_facts(whole)[0]["sections"], ".a, .b")
    check("a name in latin-1, NUL padding dropped",
          ae.pe_facts(_pe([(b"\xe9t\xe9", 1, 1)]))[0]["sections"], "\xe9t\xe9")


def test_find_payload() -> None:
    data, end = _sample()
    at = data.find(ae.MARKER)
    check("no marker", ae.find_payload(b"MZ" * 40, end), (None, "no AU3 marker anywhere in the file", ""))
    check("at the overlay's start: the overlay, with the bytes after the 24-byte header",
          ae.find_payload(data, at), (b"PAYLOAD", f"overlay at offset {at}, tag AU3!EA06", "AU3!EA06"))
    check("before the overlay: a resource",
          ae.find_payload(data, at + 1)[1], f"resource/section at offset {at}, tag AU3!EA06")
    check("no overlay offset: a resource", ae.find_payload(data, 0)[1],
          f"resource/section at offset {at}, tag AU3!EA06")


def _ripper(result: Any) -> tuple[dict[str, types.ModuleType], list[tuple[Any, Any]]]:
    """A stand-in autoit-ripper whose parser records what it was given."""
    calls: list[tuple[Any, Any]] = []
    pkg = types.ModuleType("autoit_ripper")
    pkg.AutoItVersion = types.SimpleNamespace(EA05="v5", EA06="v6")  # type: ignore[attr-defined]
    unpack = types.ModuleType("autoit_ripper.autoit_unpack")

    def parse_all(stream: Any, version: Any) -> Any:
        calls.append((stream, version))
        return result

    unpack.parse_all = parse_all  # type: ignore[attr-defined]
    utils = types.ModuleType("autoit_ripper.utils")
    utils.ByteStream = lambda b: ("stream", b)  # type: ignore[attr-defined]
    return {"autoit_ripper": pkg, "autoit_ripper.autoit_unpack": unpack, "autoit_ripper.utils": utils}, calls


def test_decode() -> None:
    mods, calls = _ripper([("s", b"x")])
    with mock.patch.dict(sys.modules, mods):
        check("EA05 in the tag: parsed as EA05", ae.decode(b"P", "AU3!EA05"), [("s", b"x")])
        ae.decode(b"Q", "AU3!EA06")
    check("  the payload handed over as a stream, with the version", calls,
          [(("stream", b"P"), "v5"), (("stream", b"Q"), "v6")])
    mods, _ = _ripper(None)
    with mock.patch.dict(sys.modules, mods):
        check("nothing parsed: no records", ae.decode(b"P", "AU3!EA06"), [])
    said = "did not exit"
    with mock.patch.dict(sys.modules, {"autoit_ripper": None}):
        try:
            ae.decode(b"P", "AU3!EA06")
        except SystemExit as e:
            said = str(e)
    check("autoit-ripper missing: exits naming the install", said,
          "needs autoit-ripper:  pip install autoit-ripper==1.2.0")


def test_scan() -> None:
    text = "\n".join([
        "Func InetGet_wrapper()",
        "  ; InetGet(\"http://x.example/in-a-comment\")",
        "EndFunc",
        "InetGet(\"http://x.example/a.exe\", \"a.exe\")",
        "Global Const $U = \"https://x.example/payload\"",
        "RegWrite(\"HKCU\\\\Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Run\", \"x\")",
        "Run(\"cmd.exe\")",
        "FileInstall(\"x.exe\", @TempDir & \"\\x.exe\")",
        "Execute(Binary(\"0x41\"))",
        "inetget(\"q\")",
        "   Run(\"" + "a" * 200 + "\")   ",
        "MsgBox(0, \"hi\", \"there\")",
    ])
    check("one finding per line, first kind that matches, line numbers from 1", ae.scan(text), [
        ("network", 4, "InetGet(\"http://x.example/a.exe\", \"a.exe\")"),
        ("url", 5, "Global Const $U = \"https://x.example/payload\""),
        ("persistence", 6, "RegWrite(\"HKCU\\\\Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Run\", \"x\")"),
        ("execution", 7, "Run(\"cmd.exe\")"),
        ("drops files", 8, "FileInstall(\"x.exe\", @TempDir & \"\\x.exe\")"),
        ("obfuscation", 9, "Execute(Binary(\"0x41\"))"),
        ("network", 10, "inetget(\"q\")"),
        ("execution", 11, ("Run(\"" + "a" * 200)[:150]),
    ])


def _main(argv: list[str], records: list[tuple[str, bytes]] | None = None) -> tuple[Any, str, list[Any]]:
    """Run main() with ARGV; decode stood in for, returning RECORDS. Returns
    (exit code or SystemExit message, stdout, the calls name_process got)."""
    named: list[Any] = []
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["autoit-extract.py", *argv]), \
            mock.patch.object(ae, "name_process", side_effect=named.append), \
            mock.patch.object(ae, "decode", return_value=records or []), \
            contextlib.redirect_stdout(out):
        try:
            result: Any = ae.main()
        except SystemExit as e:
            result = e.code
    return result, out.getvalue(), named


def test_main_writes_and_reports() -> None:
    data, end = _sample()
    at = data.find(ae.MARKER)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "tool.exe"
        f.write_bytes(data)
        outdir = Path(td) / "out" / "deeper"
        records = [(">>>AUTOIT SCRIPT<<<", b"InetGet(\"http://x.example/a\")\nMsgBox(0)\n"),
                   ("", b"MsgBox(0)\n")]
        rc, out, named = _main([str(f), "-o", str(outdir)], records)
        check("exit 0", rc, 0)
        check("named itself", named, ["autoit-extract"])
        check("the report", out.splitlines(), [
            "  machine      x86",
            "  sections     .text, .rsrc",
            "  stub built   1970-01-01 00:00:00 UTC",
            f"  file size    {len(data)}",
            f"  overlay      {len(data) - end} bytes",
            "",
            f"  payload: overlay at offset {at}, tag AU3!EA06",
            f"  wrote {outdir / 'AUTOIT_SCRIPT___'} ({len(records[0][1])} bytes, 3 lines)",
            "",
            "    network      :1      InetGet(\"http://x.example/a\")",
            f"  wrote {outdir / 'script.au3'} (10 bytes, 2 lines)",
            "    no network, persistence, dropped files or obfuscation found",
            "",
            "  1 indicator line(s). Read them -- a flashing tool",
            "  legitimately runs other binaries; what matters is which, and from where.",
        ])
        check("each script written under its safe name, its bytes as decoded",
              [(outdir / "AUTOIT_SCRIPT___").read_bytes(), (outdir / "script.au3").read_bytes()],
              [records[0][1], records[1][1]])
        rc, out, _ = _main([str(f), "--quiet", "-o", str(outdir)], records)
        check_true("--quiet: no PE facts, the findings still", out.startswith("  payload: ")
                   and "network      :1" in out)
        with contextlib.chdir(td):
            _main([str(f)], [("a.au3", b"x\n")])
            check_true("no -o: written in the current directory", (Path(td) / "a.au3").is_file())


def test_main_stops() -> None:
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "x.bin"
        f.write_bytes(b"not a pe at all" * 10)
        check("not a PE: exits saying so", _main([str(f)])[0], "not a PE file")
        f.write_bytes(_pe([(b".t", 0x10, 0x100)]).ljust(0x200, b"\0"))
        check("no marker: exits with the reason", _main([str(f)])[0], "  no AU3 marker anywhere in the file")
        f.write_bytes(_sample()[0])
        check("nothing decoded: exits saying so", _main([str(f)], [])[0],
              "  located the payload but could not decode it")


def test_help() -> None:
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["autoit-extract.py", "--help"]), \
            mock.patch.object(ae, "name_process"), contextlib.redirect_stdout(out), \
            contextlib.suppress(SystemExit):  # reason: --help prints, then argparse exits 0; the printed text is what is checked
        ae.main()
    text = " ".join(out.getvalue().split())
    for want in ("Extract and triage the AutoIt script inside a compiled PE.",
                 "where to write scripts", "findings only"):
        check_true(f"--help says: {want}", want in text)


if __name__ == "__main__":
    sys.exit(run(globals(), "autoit-extract"))
