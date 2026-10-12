#!/usr/bin/env python3
"""Boot a disk image headless under KVM and screenshot what it does.

    vm-image-boot.py IMAGE [-w SECONDS] [-o shot.png] [--forward 12340:1234]

For triaging a bootable image you cannot plug into anything: does it boot, how
far does it get, and what is on screen when it stops. A serial console shows
only what the guest chooses to log; the framebuffer shows what actually
happened, including an X server dying with `no screens found`.

**Use a GPU device that provides a DRM node.** The default `-vga std` is a
legacy Bochs adapter with no `/dev/dri/card0`, and any guest whose Xorg is
configured for KMS dies with "Device(s) detected, but none match those in the
config file" -- which reads like a broken image and is not. `virtio-vga` (the
default here) exposes a DRM node and boots the same image straight to a desktop.

**An open forwarded port proves nothing about the guest.** QEMU's user-mode
networking binds `hostfwd` ports on the *host* the moment it starts, whether or
not anything listens inside. A connect succeeds and then resets. Judge a guest
service by its reply, never by the port being open.

Nothing here writes to the image unless `--writable` is passed: the default is
a throwaway qcow2 overlay, so a botched boot cannot damage the original.
"""
import argparse
import contextlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path
from typing import IO

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from platformtools.procname import name_process

OVMF_CANDIDATES = [
    "/usr/share/OVMF/OVMF_CODE_4M.fd",
    "/usr/share/OVMF/OVMF_CODE.fd",
    "/usr/share/edk2/ovmf/OVMF_CODE.fd",
]
VARS_CANDIDATES = [
    "/usr/share/OVMF/OVMF_VARS_4M.fd",
    "/usr/share/OVMF/OVMF_VARS.fd",
    "/usr/share/edk2/ovmf/OVMF_VARS.fd",
]

# The PNG format's fixed parts: the signature, a chunk's 4-byte length and CRC,
# and IHDR's fields (width, height, then bit depth, colour type, compression,
# filter and interlace bytes).
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_U32 = ">I"
PNG_IHDR = ">IIBBBBB"
# What qemu writes on stderr, read as text; anything not UTF-8 is replaced.
QEMU_TEXT = "utf-8"


def image_format(path: str) -> str:
    """The image's real container format, asked of qemu-img rather than assumed.

    Both disk paths depend on it: `-F` for the overlay's backing format, and
    `format=` for the --writable attach. Assuming `raw` against a qcow2 base,
    every layer reports success (create is silent, `qemu-img check` finds no
    errors) while the guest's sector 0 is the base's `QFI\xfb` magic instead of
    a boot sector, so the screenshot shows "no bootable device" and the image
    reads as dead. With --writable the guest would write raw sectors over
    qcow2 metadata. Same class as the two traps in the module docstring: a
    working image that presents as a broken one.
    """
    out = subprocess.run(["qemu-img", "info", "--output=json", path],
                         capture_output=True, text=True, check=False)
    if out.returncode != 0:
        sys.exit(f"qemu-img cannot read {path}: {out.stderr.strip()[:300]}")
    try:
        fmt = json.loads(out.stdout)["format"]
    except (ValueError, KeyError):
        sys.exit(f"qemu-img gave no format for {path}")
    return str(fmt)


def parse_forward(spec: str) -> tuple[int, int]:
    """HOST:GUEST as two ports, refusing anything else by name.

    Checked here because a value with no colon would build
    `hostfwd=tcp:127.0.0.1:1234-:`, which qemu rejects at startup with its own
    parse error rather than naming the argument that was wrong.
    """
    ports = spec.split(":")
    if len(ports) != 2 or not all(p.isdigit() for p in ports):
        sys.exit(f"--forward {spec}: expected HOST:GUEST, two port numbers")
    return int(ports[0]), int(ports[1])


def first_existing(paths: list[str]) -> str | None:
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def ppm_to_png(ppm_path: str, png_path: str) -> tuple[int, int]:
    """Convert QEMU's screendump to PNG with nothing but the stdlib.

    Avoids pulling in Pillow or ImageMagick for one conversion on a machine
    that may have neither.
    """
    with open(ppm_path, "rb") as f:
        data = f.read()
    if not data.startswith(b"P6"):
        raise ValueError("not a binary PPM (P6)")
    parts = data.split(b"\n", 3)
    width, height = (int(x) for x in parts[1].split())
    pixels = parts[3]
    raw = b"".join(b"\x00" + pixels[y * width * 3:(y + 1) * width * 3]
                   for y in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return (struct.pack(PNG_U32, len(payload)) + body
                + struct.pack(PNG_U32, zlib.crc32(body) & 0xFFFFFFFF))

    png = (PNG_SIGNATURE
           + chunk(b"IHDR", struct.pack(PNG_IHDR, width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    with open(png_path, "wb") as out:
        out.write(png)
    return width, height


def drain_nonblocking(proc: "subprocess.Popen[bytes]") -> str:
    """Whatever qemu has said so far, without blocking on a process still up.

    Reading a live process's stderr to EOF hangs until it exits. Terminate
    first, then read: this is only called on a path that is already failing.
    """
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):  # reason: qemu already gone or too slow; either way stderr is what we came for
        proc.terminate()
        proc.wait(timeout=5)
    return drain(proc.stderr)


def drain(pipe: IO[bytes] | None) -> str:
    """Whatever a pipe has to say, as text. A closed pipe says nothing.

    Popen.stderr is Optional whether or not PIPE was asked for, and the caller
    reads it on the path where qemu has already died -- the one path where the
    message is the entire point.
    """
    return pipe.read().decode(QEMU_TEXT, "replace") if pipe else ""


def screendump(monitor_path: str, out_ppm: str, timeout: int = 20) -> bool:
    """Ask the QEMU monitor for the framebuffer."""
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(timeout)
    sock.connect(monitor_path)
    time.sleep(1)
    with contextlib.suppress(OSError):  # reason: the monitor banner is optional; screendump is sent either way
        sock.recv(65536)                      # banner
    sock.sendall(b"screendump " + out_ppm.encode() + b"\n")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(out_ppm) and os.path.getsize(out_ppm) > 0:
            time.sleep(1)                     # let it finish writing
            break
        time.sleep(0.5)
    sock.close()
    return os.path.exists(out_ppm) and os.path.getsize(out_ppm) > 0


def build_parser() -> argparse.ArgumentParser:
    """Every option this tool takes. Lifted out so main reads as the
    boot sequence it is, rather than opening with thirty lines of
    argument declarations."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("image", help="disk image, or a block device")
    ap.add_argument("-w", "--wait", type=int, default=90,
                    help="seconds to let it boot before the screenshot")
    ap.add_argument("-o", "--out", default="screen.png")
    ap.add_argument("-m", "--memory", default="4096")
    ap.add_argument("-c", "--cpus", default="4")
    ap.add_argument("--vga", default="virtio-vga",
                    help="GPU device (default virtio-vga -- see the note about "
                         "DRM nodes; 'std' is the one that breaks KMS guests)")
    ap.add_argument("--bios", action="store_true",
                    help="legacy BIOS boot instead of UEFI/OVMF")
    ap.add_argument("--forward", action="append", default=[],
                    metavar="HOST:GUEST", help="forward a TCP port, repeatable")
    ap.add_argument("--writable", action="store_true",
                    help="write to the image directly. Default is a throwaway "
                         "overlay so the original cannot be damaged")
    ap.add_argument("--vnc", default="1", metavar="DISPLAY",
                    help="VNC display number on 127.0.0.1 (default 1, TCP "
                         "5901). A second concurrent run needs its own.")
    ap.add_argument("--keep", action="store_true", help="leave the VM running")
    return ap


def clean_workdir(workdir: str, keep: bool) -> None:
    """Remove the scratch directory, or say why it could not be.

    Not ignore_errors=True, which would be a silent failure: a workdir that
    cannot be removed is a disk leak the operator should hear about once, so
    it prints and carries on rather than either raising or saying nothing.
    """
    if keep:
        print(f"  workdir {workdir} (yours to remove)")
        return
    try:
        shutil.rmtree(workdir)
    except OSError as exc:
        print(f"  could not remove {workdir}: {exc}", file=sys.stderr)


def main() -> int:
    name_process("vm-image-boot")
    args = build_parser().parse_args()

    if not os.path.exists(args.image):
        sys.exit(f"no such image: {args.image}")

    base_format = image_format(args.image)
    # Everything from here on runs inside boot(), so that ONE finally covers
    # every exit, including a sys.exit before qemu starts (OVMF missing,
    # --forward refused), which would otherwise leave the workdir with its
    # overlay behind. SystemExit is an exception, so a finally still runs on it.
    workdir = tempfile.mkdtemp(prefix="vmboot-")
    try:
        return boot(args, base_format, workdir)
    finally:
        clean_workdir(workdir, args.keep)


def build_command(args: argparse.Namespace, base_format: str,
                  workdir: str, monitor: str) -> list[str]:
    """The whole qemu invocation, including the overlay it may have to create."""
    if args.writable:
        disk = args.image
    else:
        disk = os.path.join(workdir, "overlay.qcow2")
        subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2",
                        "-F", base_format, "-b", os.path.abspath(args.image),
                        disk], check=True)

    cmd = ["qemu-system-x86_64", "-enable-kvm",
           "-m", args.memory, "-smp", args.cpus, "-machine", "q35"]

    if not args.bios:
        code = first_existing(OVMF_CANDIDATES)
        varsrc = first_existing(VARS_CANDIDATES)
        if not code or not varsrc:
            sys.exit("OVMF firmware not found -- install ovmf, or pass --bios")
        nvram = os.path.join(workdir, "OVMF_VARS.fd")
        with open(varsrc, "rb") as src, open(nvram, "wb") as dst:
            dst.write(src.read())
        cmd += ["-drive", f"if=pflash,format=raw,readonly=on,file={code}",
                "-drive", f"if=pflash,format=raw,file={nvram}"]

    fmt = base_format if args.writable else "qcow2"
    cmd += ["-drive", f"file={disk},format={fmt},id=disk0,if=none",
            "-device", "ahci,id=ahci",
            "-device", "ide-hd,drive=disk0,bus=ahci.0"]

    netdev = "user,id=n0"
    for f in args.forward:
        host, guest = parse_forward(f)
        netdev += f",hostfwd=tcp:127.0.0.1:{host}-:{guest}"
    cmd += ["-netdev", netdev, "-device", "virtio-net-pci,netdev=n0"]

    cmd += ["-device", args.vga] if "-" in args.vga else ["-vga", args.vga]
    cmd += ["-vnc", f"127.0.0.1:{args.vnc}",
            "-monitor", f"unix:{monitor},server,nowait"]
    return cmd


def boot(args: argparse.Namespace, base_format: str, workdir: str) -> int:
    """Run qemu against the image and screenshot where it got to."""
    monitor = os.path.join(workdir, "mon.sock")
    ppm = os.path.join(workdir, "screen.ppm")
    cmd = build_command(args, base_format, workdir, monitor)

    print(f"  booting {args.image}")
    print(f"  gpu     {args.vga}")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    try:
        for remaining in range(args.wait, 0, -15):
            if proc.poll() is not None:
                err = drain(proc.stderr)
                sys.exit(f"  qemu exited early:\n{err[:600]}")
            time.sleep(min(15, remaining))

        if not screendump(monitor, ppm):
            # Drain here too. qemu is still up on this path, so the early-exit
            # read never ran, and this is the one moment a qemu message would
            # explain why the framebuffer never arrived.
            sys.exit(f"  screendump produced nothing\n{drain_nonblocking(proc)}")
        w, h = ppm_to_png(ppm, args.out)
        print(f"  wrote   {args.out}  ({w}x{h})")
        if args.forward:
            print("  note: a forwarded port answers on the host whether or not")
            print("  the guest listens -- check the service's reply, not the port")
    finally:
        if not args.keep and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        elif args.keep:
            print(f"  VM left running (pid {proc.pid}), monitor at {monitor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
