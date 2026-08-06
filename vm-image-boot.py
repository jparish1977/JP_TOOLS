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
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib

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


def first_existing(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def ppm_to_png(ppm_path, png_path):
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

    def chunk(tag, payload):
        body = tag + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    with open(png_path, "wb") as f:
        f.write(png)
    return width, height


def screendump(monitor_path, out_ppm, timeout=20):
    """Ask the QEMU monitor for the framebuffer."""
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(timeout)
    sock.connect(monitor_path)
    time.sleep(1)
    with contextlib.suppress(OSError):
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


def main():
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
    ap.add_argument("--keep", action="store_true", help="leave the VM running")
    args = ap.parse_args()

    if not os.path.exists(args.image):
        sys.exit(f"no such image: {args.image}")

    workdir = tempfile.mkdtemp(prefix="vmboot-")
    monitor = os.path.join(workdir, "mon.sock")
    ppm = os.path.join(workdir, "screen.ppm")

    if args.writable:
        disk = args.image
    else:
        disk = os.path.join(workdir, "overlay.qcow2")
        subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-F", "raw",
                        "-b", os.path.abspath(args.image), disk], check=True)

    cmd = ["qemu-system-x86_64", "-enable-kvm",
           "-m", args.memory, "-smp", args.cpus, "-machine", "q35"]

    if not args.bios:
        code = first_existing(OVMF_CANDIDATES)
        varsrc = first_existing(VARS_CANDIDATES)
        if not code or not varsrc:
            sys.exit("OVMF firmware not found -- install ovmf, or pass --bios")
        nvram = os.path.join(workdir, "OVMF_VARS.fd")
        with open(varsrc, "rb") as s, open(nvram, "wb") as d:
            d.write(s.read())
        cmd += ["-drive", f"if=pflash,format=raw,readonly=on,file={code}",
                "-drive", f"if=pflash,format=raw,file={nvram}"]

    fmt = "raw" if args.writable else "qcow2"
    cmd += ["-drive", f"file={disk},format={fmt},id=disk0,if=none",
            "-device", "ahci,id=ahci",
            "-device", "ide-hd,drive=disk0,bus=ahci.0"]

    netdev = "user,id=n0"
    for f in args.forward:
        host, _, guest = f.partition(":")
        netdev += f",hostfwd=tcp:127.0.0.1:{host}-:{guest}"
    cmd += ["-netdev", netdev, "-device", "virtio-net-pci,netdev=n0"]

    cmd += ["-device", args.vga] if "-" in args.vga else ["-vga", args.vga]
    cmd += ["-vnc", "127.0.0.1:1",
            "-monitor", f"unix:{monitor},server,nowait"]

    print(f"  booting {args.image}")
    print(f"  gpu     {args.vga}")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    try:
        for remaining in range(args.wait, 0, -15):
            if proc.poll() is not None:
                err = proc.stderr.read().decode("utf-8", "replace")
                sys.exit(f"  qemu exited early:\n{err[:600]}")
            time.sleep(min(15, remaining))

        if not screendump(monitor, ppm):
            sys.exit("  screendump produced nothing")
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
