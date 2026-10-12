#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_vm_image_boot.py
vm-image-boot.py, in-process. qemu, qemu-img, the monitor socket and the
clock are stood in for, so every decision runs without KVM: what the tool
asks of them and what it does with each answer is what is checked.

WHAT IS CHECKED
    - image_format asks qemu-img, and exits naming the image when it cannot.
    - --forward takes HOST:GUEST ports only; first_existing picks the first
      path that exists.
    - ppm_to_png writes a PNG whose header, chunks, CRCs and pixel rows are
      exactly the PPM's; a non-P6 file is refused.
    - screendump sends the monitor command and waits for a non-empty dump,
      on a clock that only moves when the code sleeps; drain and
      drain_nonblocking return what qemu said, even from a dead process.
    - build_command: the overlay (never the image) by default, its backing
      format; OVMF code and a private copy of the vars, or --bios; forwarded
      ports; a device GPU or a -vga one; VNC and the monitor.
    - boot: waits in steps of at most 15 s watching for an early exit, then
      the screenshot, and always stops qemu unless --keep.
    - main: refuses a missing image; the workdir is removed after a boot,
      after a failed one too, and kept with --keep.

    python3 tests/test_vm_image_boot.py
"""

import argparse
import contextlib
import io
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any
from unittest import mock

from _harness import check, check_true, load_script, run

vm = load_script("vm-image-boot.py")


def _exit_message(fn: Any, *args: Any) -> str:
    try:
        fn(*args)
    except SystemExit as e:
        return str(e)
    return "did not exit"


def test_image_format() -> None:
    asked: list[Any] = []

    def fake(stdout: str = "", rc: int = 0, stderr: str = "") -> Any:
        def run_(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
            asked.append((argv, kw))
            return subprocess.CompletedProcess(argv, rc, stdout, stderr)
        return run_

    with mock.patch.object(vm.subprocess, "run", fake('{"format": "qcow2"}')):
        check("the format qemu-img reports", vm.image_format("/i.img"), "qcow2")
    check("  asked as JSON, its output captured as text, its exit read here",
          asked, [(["qemu-img", "info", "--output=json", "/i.img"],
                   {"capture_output": True, "text": True, "check": False})])
    with mock.patch.object(vm.subprocess, "run", fake('{"format": 7}')):
        check("a non-text format comes back as text", vm.image_format("/i.img"), "7")
    with mock.patch.object(vm.subprocess, "run", fake(rc=1, stderr="  " + "e" * 400 + "  ")):
        check("qemu-img failing: exits naming the image and its first 300 characters",
              _exit_message(vm.image_format, "/i.img"), "qemu-img cannot read /i.img: " + "e" * 300)
    for out in ("not json", '{"other": 1}'):
        with mock.patch.object(vm.subprocess, "run", fake(out)):
            check(f"no format in {out!r}: exits saying so",
                  _exit_message(vm.image_format, "/i.img"), "qemu-img gave no format for /i.img")


def test_parse_forward_and_first_existing() -> None:
    check("HOST:GUEST", vm.parse_forward("12340:1234"), (12340, 1234))
    for bad in ("1234", ":1", "1:", "a:1", "1:b", "1:2:3"):
        check(f"{bad!r} refused by name", _exit_message(vm.parse_forward, bad),
              f"--forward {bad}: expected HOST:GUEST, two port numbers")
    with tempfile.TemporaryDirectory() as td:
        a, b = Path(td) / "a", Path(td) / "b"
        b.write_text("", encoding="utf-8")
        check("the first that exists", vm.first_existing([str(Path(td) / "gone"), str(b), str(a)]), str(b))
        a.write_text("", encoding="utf-8")
        check("  in order", vm.first_existing([str(a), str(b)]), str(a))
        check("none: None", vm.first_existing([str(Path(td) / "gone")]), None)


def _chunks(png: bytes) -> list[tuple[bytes, bytes, bool]]:
    """(tag, payload, CRC right) for each chunk after the signature."""
    out, at = [], 8
    while at < len(png):
        n = struct.unpack(">I", png[at:at + 4])[0]
        tag, body = png[at + 4:at + 8], png[at + 8:at + 8 + n]
        crc = struct.unpack(">I", png[at + 8 + n:at + 12 + n])[0]
        out.append((tag, body, crc == zlib.crc32(tag + body)))
        at += 12 + n
    return out


def test_ppm_to_png() -> None:
    pixels = bytes(range(18))  # 3 wide, 2 high, RGB
    with tempfile.TemporaryDirectory() as td:
        ppm, png = Path(td) / "s.ppm", Path(td) / "s.png"
        ppm.write_bytes(b"P6\n3 2\n255\n" + pixels)
        check("returns the size", vm.ppm_to_png(str(ppm), str(png)), (3, 2))
        data = png.read_bytes()
        check("the PNG signature", data[:8], b"\x89PNG\r\n\x1a\n")
        chunks = _chunks(data)
        check("IHDR, IDAT, IEND, each CRC right", [(t, ok) for t, _, ok in chunks],
              [(b"IHDR", True), (b"IDAT", True), (b"IEND", True)])
        check("IHDR: 3x2, 8-bit RGB, no interlace", chunks[0][1], struct.pack(">IIBBBBB", 3, 2, 8, 2, 0, 0, 0))
        check("IDAT: each row filter 0 then its pixels", zlib.decompress(chunks[1][1]),
              b"\x00" + pixels[:9] + b"\x00" + pixels[9:])
        check("IEND empty", chunks[2][1], b"")
        ppm.write_bytes(b"P3\n1 1\n255\n0 0 0\n")
        try:
            vm.ppm_to_png(str(ppm), str(png))
            said = "accepted"
        except ValueError as e:
            said = str(e)
        check("an ASCII PPM is refused", said, "not a binary PPM (P6)")


class _Pipe:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data


def test_drain() -> None:
    check("a closed pipe says nothing", vm.drain(None), "")
    check("bytes as text, bad bytes replaced", vm.drain(_Pipe(b"ok \xff")), "ok �")
    calls: list[Any] = []

    class Proc:
        stderr = _Pipe(b"died")

        def __init__(self, terminate: Exception | None, wait: Exception | None) -> None:
            self.t, self.w = terminate, wait

        def terminate(self) -> None:
            calls.append("terminate")
            if self.t:
                raise self.t

        def wait(self, timeout: int) -> None:
            calls.append(("wait", timeout))
            if self.w:
                raise self.w

    check("a live qemu: stopped, waited 5 s, then read", (vm.drain_nonblocking(Proc(None, None)), calls),
          ("died", ["terminate", ("wait", 5)]))
    calls.clear()
    check("already gone (terminate fails): still read",
          (vm.drain_nonblocking(Proc(ProcessLookupError(), None)), calls), ("died", ["terminate"]))
    calls.clear()
    check("too slow to stop: still read",
          vm.drain_nonblocking(Proc(None, subprocess.TimeoutExpired("q", 5))), "died")


class _Clock:
    """time.time and time.sleep: the clock moves only when the code sleeps.
    ON_SLEEP runs after each sleep (qemu writing its dump, say)."""

    def __init__(self, on_sleep: Any = None) -> None:
        self.now = 100.0
        self.slept: list[float] = []
        self.on_sleep = on_sleep

    def time(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s
        if self.on_sleep:
            self.on_sleep(len(self.slept))


class _Sock:
    def __init__(self, banner: bool) -> None:
        self.banner = banner
        self.calls: list[Any] = []

    def __call__(self, family: Any) -> "_Sock":
        self.calls.append(("socket", family))
        return self

    def settimeout(self, t: float) -> None:
        self.calls.append(("settimeout", t))

    def connect(self, path: str) -> None:
        self.calls.append(("connect", path))

    def recv(self, n: int) -> bytes:
        self.calls.append(("recv", n))
        if not self.banner:
            raise TimeoutError
        return b"QEMU monitor"

    def sendall(self, b: bytes) -> None:
        self.calls.append(("sendall", b))

    def close(self) -> None:
        self.calls.append(("close",))


def _screendump(ppm: Path, banner: bool, write_at: int | None, content: bytes = b"P6",
                timeout: int = 2) -> tuple[bool, list[Any], list[float]]:
    def qemu_writes(n: int) -> None:
        if n == write_at:
            ppm.write_bytes(content)

    clock, sock = _Clock(qemu_writes), _Sock(banner)
    with mock.patch.object(vm.time, "time", clock.time), mock.patch.object(vm.time, "sleep", clock.sleep), \
            mock.patch.object(vm.socket, "socket", sock):
        ok = vm.screendump("/w/mon.sock", str(ppm), timeout)
    return ok, sock.calls, clock.slept


def test_screendump() -> None:
    with tempfile.TemporaryDirectory() as td:
        ppm = Path(td) / "s.ppm"
        ok, calls, slept = _screendump(ppm, banner=True, write_at=2)
        check("the dump appears: True", ok, True)
        check("  the monitor asked, its banner read, the command sent, the socket closed", calls, [
            ("socket", vm.socket.AF_UNIX), ("settimeout", 2), ("connect", "/w/mon.sock"), ("recv", 65536),
            ("sendall", b"screendump " + str(ppm).encode() + b"\n"), ("close",)])
        check("  a second for the banner, half-seconds until the dump, a second to finish",
              slept, [1, 0.5, 1])
        ppm.unlink()
        ok, calls, slept = _screendump(ppm, banner=False, write_at=None)
        check("no banner and no dump: False after the timeout, the command still sent",
              (ok, ("sendall", b"screendump " + str(ppm).encode() + b"\n") in calls, slept),
              (False, True, [1, 0.5, 0.5, 0.5, 0.5]))
        ok, _, _ = _screendump(ppm, banner=True, write_at=2, content=b"")
        check("an empty dump is not a dump", ok, False)
        ppm.unlink()
        ok, _, _ = _screendump(ppm, banner=True, write_at=2, content=b"x")
        check("one byte is", ok, True)
    with tempfile.TemporaryDirectory() as td:
        clock, sock = _Clock(), _Sock(True)
        with mock.patch.object(vm.time, "time", clock.time), mock.patch.object(vm.time, "sleep", clock.sleep), \
                mock.patch.object(vm.socket, "socket", sock):
            ok = vm.screendump("/w/mon.sock", str(Path(td) / "never.ppm"))
        check("no timeout given: 20 s, on the socket and for the dump",
              (ok, ("settimeout", 20) in sock.calls, clock.slept), (False, True, [1] + [0.5] * 40))


def _args(*argv: str) -> argparse.Namespace:
    ns: argparse.Namespace = vm.build_parser().parse_args(["/img/disk.raw", *argv])
    return ns


def test_the_parser() -> None:
    check("defaults", vars(_args()), {
        "image": "/img/disk.raw", "wait": 90, "out": "screen.png", "memory": "4096", "cpus": "4",
        "vga": "virtio-vga", "bios": False, "forward": [], "writable": False, "vnc": "1", "keep": False})
    check("every option", vars(_args("-w", "5", "-o", "x.png", "-m", "512", "-c", "2", "--vga", "std",
                                     "--bios", "--forward", "1:2", "--forward", "3:4", "--writable",
                                     "--vnc", "7", "--keep")),
          {"image": "/img/disk.raw", "wait": 5, "out": "x.png", "memory": "512", "cpus": "2", "vga": "std",
           "bios": True, "forward": ["1:2", "3:4"], "writable": True, "vnc": "7", "keep": True})
    text = " ".join(vm.build_parser().format_help().split())
    for want in ("Boot a disk image headless under KVM and screenshot what it does.",
                 "disk image, or a block device", "seconds to let it boot before the screenshot",
                 ("GPU device (default virtio-vga -- see the note about DRM nodes; 'std' is the one that "
                  "breaks KMS guests)"), "legacy BIOS boot instead of UEFI/OVMF", "HOST:GUEST",
                 "forward a TCP port, repeatable",
                 "write to the image directly. Default is a throwaway overlay so the original cannot be damaged",
                 "DISPLAY", ("VNC display number on 127.0.0.1 (default 1, TCP 5901). A second concurrent run "
                             "needs its own."), "leave the VM running"):
        check_true(f"help says: {want}", want in text)


def _build(args: argparse.Namespace, workdir: str, firmware: tuple[list[str], list[str]]
           ) -> tuple[Any, list[Any]]:
    made: list[Any] = []

    def qemu_img(argv: list[str], **kw: Any) -> None:
        made.append((argv, kw))

    with mock.patch.object(vm.subprocess, "run", qemu_img), \
            mock.patch.object(vm, "OVMF_CANDIDATES", firmware[0]), \
            mock.patch.object(vm, "VARS_CANDIDATES", firmware[1]):
        try:
            cmd: Any = vm.build_command(args, "raw", workdir, "/w/mon.sock")
        except SystemExit as e:
            cmd = str(e)
    return cmd, made


def test_build_command() -> None:
    with tempfile.TemporaryDirectory() as td:
        code, varsrc = Path(td) / "CODE.fd", Path(td) / "VARS.fd"
        code.write_bytes(b"code")
        varsrc.write_bytes(b"vars")
        fw = ([str(Path(td) / "gone"), str(code)], [str(varsrc)])
        wd = Path(td) / "wd"
        wd.mkdir()
        cmd, made = _build(_args(), str(wd), fw)
        overlay = str(wd / "overlay.qcow2")
        check("by default: an overlay backed by the image, in its own format", made,
              [(["qemu-img", "create", "-q", "-f", "qcow2", "-F", "raw", "-b", "/img/disk.raw", overlay],
                {"check": True})])
        check("  the whole command: UEFI from the first OVMF there, the overlay, virtio-vga", cmd, [
            "qemu-system-x86_64", "-enable-kvm", "-m", "4096", "-smp", "4", "-machine", "q35",
            "-drive", f"if=pflash,format=raw,readonly=on,file={code}",
            "-drive", f"if=pflash,format=raw,file={wd / 'OVMF_VARS.fd'}",
            "-drive", f"file={overlay},format=qcow2,id=disk0,if=none",
            "-device", "ahci,id=ahci", "-device", "ide-hd,drive=disk0,bus=ahci.0",
            "-netdev", "user,id=n0", "-device", "virtio-net-pci,netdev=n0",
            "-device", "virtio-vga",
            "-vnc", "127.0.0.1:1", "-monitor", "unix:/w/mon.sock,server,nowait"])
        check("  the vars copied, never the shared original", (wd / "OVMF_VARS.fd").read_bytes(), b"vars")
        cmd, made = _build(_args("--bios", "--writable", "--forward", "8080:80", "--forward", "2222:22",
                                 "--vga", "std", "--vnc", "3", "-m", "512", "-c", "2"), str(wd), fw)
        check("--writable makes no overlay", made, [])
        check("  --bios, --writable, two forwards, -vga std", cmd, [
            "qemu-system-x86_64", "-enable-kvm", "-m", "512", "-smp", "2", "-machine", "q35",
            "-drive", "file=/img/disk.raw,format=raw,id=disk0,if=none",
            "-device", "ahci,id=ahci", "-device", "ide-hd,drive=disk0,bus=ahci.0",
            "-netdev", "user,id=n0,hostfwd=tcp:127.0.0.1:8080-:80,hostfwd=tcp:127.0.0.1:2222-:22",
            "-device", "virtio-net-pci,netdev=n0",
            "-vga", "std",
            "-vnc", "127.0.0.1:3", "-monitor", "unix:/w/mon.sock,server,nowait"])
        msg = "OVMF firmware not found -- install ovmf, or pass --bios"
        check("no OVMF code: exits saying how", _build(_args(), str(wd), ([], [str(varsrc)]))[0], msg)
        check("no OVMF vars: the same", _build(_args(), str(wd), ([str(code)], []))[0], msg)


class _Proc:
    def __init__(self, polls: list[int | None], wait_times_out: bool = False) -> None:
        self.polls, self.pid = polls, 4242
        self.calls: list[Any] = []
        self.stderr = _Pipe(b"E" * 700)
        self.wait_times_out = wait_times_out

    def poll(self) -> int | None:
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]

    def terminate(self) -> None:
        self.calls.append("terminate")

    def wait(self, timeout: int) -> None:
        self.calls.append(("wait", timeout))
        if self.wait_times_out:
            raise subprocess.TimeoutExpired("qemu", timeout)

    def kill(self) -> None:
        self.calls.append("kill")


def _boot(args: argparse.Namespace, proc: _Proc, shot: bool = True) -> tuple[Any, str, list[Any], list[float]]:
    clock = _Clock()
    popen: list[Any] = []
    pngs: list[Any] = []

    def popen_(cmd: list[str], **kw: Any) -> _Proc:
        popen.append((cmd, kw))
        return proc

    def to_png(ppm: str, png: str) -> tuple[int, int]:
        pngs.append((ppm, png))
        return 640, 480

    out = io.StringIO()
    with mock.patch.object(vm, "build_command", lambda a, f, w, m: ["qemu", f, w, m]), \
            mock.patch.object(vm.subprocess, "Popen", popen_), \
            mock.patch.object(vm.time, "sleep", clock.sleep), \
            mock.patch.object(vm, "screendump", lambda mon, ppm: shot), \
            mock.patch.object(vm, "drain_nonblocking", lambda p: "qemu said why"), \
            mock.patch.object(vm, "ppm_to_png", to_png), \
            contextlib.redirect_stdout(out):
        try:
            result: Any = vm.boot(args, "raw", "/w")
        except SystemExit as e:
            result = str(e)
    return result, out.getvalue(), popen + pngs, clock.slept


def test_boot() -> None:
    proc = _Proc([None])
    rc, out, calls, slept = _boot(_args("-w", "20", "-o", "/o/s.png", "--forward", "1:2"), proc)
    check("a boot: exit 0", rc, 0)
    check("  qemu run with its stderr kept, then the dump converted", calls, [
        (["qemu", "raw", "/w", "/w/mon.sock"], {"stdout": vm.subprocess.DEVNULL, "stderr": vm.subprocess.PIPE}),
        ("/w/screen.ppm", "/o/s.png")])
    check("  waited in steps of at most 15 s", slept, [15, 5])
    check("  what it says", out.splitlines(), [
        "  booting /img/disk.raw", "  gpu     virtio-vga", "  wrote   /o/s.png  (640x480)",
        "  note: a forwarded port answers on the host whether or not",
        "  the guest listens -- check the service's reply, not the port"])
    check("  and qemu stopped, waited for 10 s", proc.calls, ["terminate", ("wait", 10)])
    proc = _Proc([None], wait_times_out=True)
    _, out, _, slept = _boot(_args("-w", "30"), proc)
    check("no forward: no note; 30 s is two 15 s steps", (out.splitlines()[-1], slept),
          ("  wrote   screen.png  (640x480)", [15, 15]))
    check("  qemu slow to stop: killed", proc.calls, ["terminate", ("wait", 10), "kill"])
    proc = _Proc([None, 1])
    said, _, _, slept = _boot(_args("-w", "40"), proc)
    check("qemu exiting early: exits with its first 600 characters", said,
          "  qemu exited early:\n" + "E" * 600)
    check("  noticed at the next step, and nothing left to stop", (slept, proc.calls), ([15], []))
    proc = _Proc([None])
    said, _, _, _ = _boot(_args("-w", "1"), proc, shot=False)
    check("no dump: exits with what qemu said", said, "  screendump produced nothing\nqemu said why")
    check("  and qemu stopped", proc.calls, ["terminate", ("wait", 10)])
    proc = _Proc([None])
    _, out, _, _ = _boot(_args("-w", "1", "--keep"), proc)
    check("--keep: left running, said where", (out.splitlines()[-1], proc.calls),
          ("  VM left running (pid 4242), monitor at /w/mon.sock", []))


def test_clean_workdir() -> None:
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td) / "vmboot-x"
        wd.mkdir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            vm.clean_workdir(str(wd), True)
        check("--keep: kept, and said", (wd.exists(), out.getvalue()), (True, f"  workdir {wd} (yours to remove)\n"))
        vm.clean_workdir(str(wd), False)
        check_true("otherwise removed", not wd.exists())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            vm.clean_workdir(str(wd), False)
        check_true("one that cannot be removed: said on stderr, not raised",
                   err.getvalue().startswith(f"  could not remove {wd}: "))


def _main(argv: list[str], boot: Any) -> tuple[Any, list[Any]]:
    named: list[Any] = []
    with mock.patch.object(sys, "argv", ["vm-image-boot.py", *argv]), \
            mock.patch.object(vm, "name_process", side_effect=named.append), \
            mock.patch.object(vm, "image_format", lambda p: "qcow2"), \
            mock.patch.object(vm, "boot", boot):
        try:
            result: Any = vm.main()
        except SystemExit as e:
            result = str(e)
    return result, named


def test_main() -> None:
    with tempfile.TemporaryDirectory() as td:
        img = Path(td) / "d.img"
        img.write_bytes(b"x")
        seen: list[Any] = []

        def boot(args: argparse.Namespace, fmt: str, workdir: str) -> int:
            seen.append((args.image, fmt, Path(workdir).name.startswith("vmboot-"), Path(workdir).is_dir()))
            Path(workdir, "overlay.qcow2").write_bytes(b"o")
            return 7

        rc, named = _main([str(img)], boot)
        check("boot's code returned, named itself", (rc, named), (7, ["vm-image-boot"]))
        check("  boot given the image, its format, and a fresh vmboot- workdir", seen, [(str(img), "qcow2", True, True)])

        def fails(args: argparse.Namespace, fmt: str, workdir: str) -> int:
            seen.append(workdir)
            sys.exit("boom")

        seen.clear()
        check("a boot that exits: its message", _main([str(img)], fails)[0], "boom")
        check_true("  and its workdir removed all the same", not Path(seen[0]).exists())
        seen.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            _main([str(img), "--keep"], fails)
        check_true("--keep: the workdir stays", Path(seen[0]).is_dir())
        shutil.rmtree(seen[0])
        check("a missing image: exits naming it", _main([str(Path(td) / "gone")], boot)[0],
              f"no such image: {Path(td) / 'gone'}")


if __name__ == "__main__":
    sys.exit(run(globals(), "vm-image-boot"))
