#!/usr/bin/env python3
"""Extract and triage the AutoIt script inside a compiled PE.

AutoIt's Aut2Exe bolts a compiled script onto a stock interpreter stub, so an
AutoIt binary tells you almost nothing from the outside. Its imports list
`InternetOpenA`, `URLDownloadToFile` and `HttpSendRequestA` no matter what the
script does, because the stub embeds the whole language -- the uppercase names
are literally its builtin-function table. Antivirus reads that surface and
emits generic verdicts like `Win.Trojan.Autoit-73`. **The only evidence that
settles anything is the decoded script.**

Two payload layouts exist, and the common tooling handles one of them:

* **Resource** -- the script lives in a PE resource. `autoit-ripper` reads this.
* **Overlay** -- the script is appended after the last section. Older Aut2Exe
  builds do this, and `autoit-ripper` 1.2.0 reports "Couldn't find any
  appropiate PE resource directory" and stops. That is a *layout* miss being
  reported as a failure, and it is what this script exists for.

Both layouts store the same structure -- a 16-byte marker, `AU3!EA06`, then the
records -- so once the payload is located, the same parser decodes it.

    autoit-extract.py FILE [-o OUTDIR] [--quiet]

Prints PE facts, where the payload was found, the decoded script, and an
indicator scan (network, persistence, external execution, dropped files).
Nothing is executed: the input is read as bytes and never run.

Needs `autoit-ripper` 1.2.0 for the decoder (pip install autoit-ripper==1.2.0).
It uses that package's internal modules, so pyproject.toml pins the release.
"""
import argparse
import datetime
import os
import re
import struct
import sys
from pathlib import Path
from typing import Any

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from platformtools.procname import name_process

MARKER = bytes.fromhex("a3484bbe986c4aa9994c530a86d6487d")
HEADER = 0x18                      # 16-byte marker + 8-byte "AU3!EAxx"

# Grouped so a hit reads as a finding rather than a grep result. AutoIt is a
# GUI automation language, so Run/ShellExecute alone is not suspicious -- what
# matters is *what* is run and from where.
INDICATORS = [
    ("network", (r"\b(InetGet|InetRead|HttpSetProxy|FtpSetProxy|TCPConnect|"
                 r"TCPSend|UDPOpen|UDPSend|ObjCreate\s*\(\s*[\"']winhttp)")),
    ("url", r"(https?://|ftp://)[^\s\"']{4,80}"),
    ("persistence", (r"(RegWrite[^\n]{0,120}(CurrentVersion\\\\?Run|RunOnce)|"
                     r"@StartupDir|schtasks|\bsc\s+create\b)")),
    ("execution", r"^\s*(RunWait|Run|ShellExecuteWait|ShellExecute|RunAs)\s*\("),
    ("drops files", r"\bFileInstall\s*\("),
    ("obfuscation", (r"\b(Execute|Call)\s*\(\s*(Binary|Chr|StringReverse|"
                     r"_?B?ase64)")),
]


def pe_facts(data: bytes) -> tuple[dict[str, Any], int] | None:
    """-> (dict, overlay_offset), or None if this is not a PE.

    One optional rather than a pair of them: returning (None, None) made
    every caller test the first half and then subtract the second.
    """
    # Length-checked before every unpack, not just the magic. Truncated and
    # carved samples are ordinary input here, and b"MZ" alone would reach the
    # e_lfanew read and raise struct.error where "not a PE file" is meant.
    if data[:2] != b"MZ" or len(data) < 0x40:
        return None
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0" or len(data) < pe + 24:
        return None
    machine, nsec, stamp = struct.unpack_from("<HHI", data, pe + 4)
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    sect = pe + 24 + opt_size
    end = 0
    names = []
    for i in range(nsec):
        b = sect + 40 * i
        if len(data) < b + 40:
            break                     # section table truncated; report what we have
        names.append(data[b:b + 8].rstrip(b"\0").decode("latin1", "replace"))
        raw_size, raw_ptr = struct.unpack_from("<II", data, b + 16)
        end = max(end, raw_ptr + raw_size)
    built = datetime.datetime.fromtimestamp(stamp, datetime.UTC)
    return {
        "machine": "x86" if machine == 0x14C else "x64" if machine == 0x8664
                   else hex(machine),
        "sections": ", ".join(names),
        # The stub's build date, NOT the script's. A 2007 stamp on a 2010 tool
        # is normal and says nothing about when the script was written.
        "stub built": built.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "file size": len(data),
    }, end


def find_payload(data: bytes, overlay_at: int | None) -> tuple[bytes | None, str, str]:
    """-> (bytes_after_header, where, tag) or (None, reason, "").

    The tag comes back as its own value, so decode() never reads it out of
    the human sentence: rewording a message must not silently decode every
    EA05 payload as EA06.
    """
    at = data.find(MARKER)
    if at < 0:
        return None, "no AU3 marker anywhere in the file", ""
    tag_text = data[at + 16:at + 24].decode("latin1", "replace")
    where = "overlay" if overlay_at and at >= overlay_at else "resource/section"
    return (data[at + HEADER:],
            f"{where} at offset {at}, tag {tag_text}",
            tag_text)


def decode(payload: bytes, tag: str) -> list[tuple[str, bytes]]:
    """-> [(name, bytes)]. Uses autoit-ripper's parser on located bytes."""
    try:
        from autoit_ripper import AutoItVersion
        from autoit_ripper.autoit_unpack import parse_all
        from autoit_ripper.utils import ByteStream
    except ImportError:
        sys.exit("needs autoit-ripper:  pip install autoit-ripper==1.2.0")
    version = AutoItVersion.EA05 if "EA05" in tag else AutoItVersion.EA06
    return parse_all(ByteStream(payload), version) or []


def scan(text: str) -> list[tuple[str, int, str]]:
    """-> [(kind, line_no, line)] for anything worth a human look."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        # Aut2Exe inlines the whole standard UDF library, thousands of lines
        # of someone else's code. Structure and comments are skipped; the
        # CONSTANT TABLE is not, because that is where the URLs live.
        # `Global Const $URL = "http://..."` is a common shape in compiled
        # AutoIt; dropping it would show the operator `InetGet($URL, ...)`
        # with no way to see what $URL was, the one fact they need. The
        # indicators are specific enough to be their own filter: a UDF
        # constant matches none of them.
        if re.match(r"^\s*(Func |EndFunc|;)", line):
            continue
        for kind, pattern in INDICATORS:
            if re.search(pattern, line, re.I):
                hits.append((kind, n, line.strip()[:150]))
                break
    return hits


def main() -> int:
    name_process("autoit-extract")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file")
    ap.add_argument("-o", "--outdir", default=".", help="where to write scripts")
    ap.add_argument("--quiet", action="store_true", help="findings only")
    args = ap.parse_args()

    with open(args.file, "rb") as f:
        data = f.read()

    parsed = pe_facts(data)
    if parsed is None:
        sys.exit("not a PE file")
    facts, overlay_at = parsed
    if not args.quiet:
        for k, v in facts.items():
            print(f"  {k:<12} {v}")
        print(f"  {'overlay':<12} {len(data) - overlay_at} bytes")
        print()

    payload, where, tag = find_payload(data, overlay_at)
    if payload is None:
        sys.exit(f"  {where}")
    print(f"  payload: {where}")

    records = decode(payload, tag)
    if not records:
        sys.exit("  located the payload but could not decode it")

    os.makedirs(args.outdir, exist_ok=True)
    findings = 0
    for name, blob in records:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name.lstrip(">")) or "script.au3"
        path = os.path.join(args.outdir, safe)
        with open(path, "wb") as f:
            f.write(blob)
        text = blob.decode("utf-8", "replace")
        lines = text.count("\n") + 1
        print(f"  wrote {path} ({len(blob)} bytes, {lines} lines)")

        hits = scan(text)
        findings += len(hits)
        if not hits:
            print("    no network, persistence, dropped files or "
                  "obfuscation found")
            continue
        print()
        for kind, n, line in hits:
            print(f"    {kind:<12} :{n:<6} {line}")

    print()
    print(f"  {findings} indicator line(s). Read them -- a flashing tool")
    print("  legitimately runs other binaries; what matters is which, and from where.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
