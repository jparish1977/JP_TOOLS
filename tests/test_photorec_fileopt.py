#!/usr/bin/env python
"""
JP_TOOLS/tests/test_photorec_fileopt.py
Tests recover.py's photorec `/cmd` string, against a real photorec.

Plants two files of different families in a raw image, drives photorec with
the string recover.py builds, and checks both come back. No pytest, no
dependencies -- the toolbox has none and this should not add the first.

THE BUG THIS PINS DOWN
    photorec's fileopt list is PAIRS: each family name is followed by its own
    toggle. recover.py wrote the family names as a list and appended one
    toggle to the end -- "jpg,png,enable" -- which photorec 7.1 rejects with
    `Syntax error in command line: jpg,png,enable,search`. It then recovers
    nothing and exits 0, so a scan that was never parsed is indistinguishable
    from a disk with nothing on it.

    It bit only at TWO OR MORE families, which is why it survived: `roms`
    resolves to one family and `full` sends no list, and both worked. The
    DEFAULT profile, `all`, resolves to more than twenty.

    Measured on this fixture before the fix: 0 files recovered. After: 2.

Skips rather than fails when the environment cannot support it:
  - no photorec              -> skip (testdisk not installed)
  - no fixture images on disk -> skip (nothing to plant)
The pure string checks always run; they need no photorec at all.

    python tests/test_photorec_fileopt.py
"""

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent

# Planted at these sector offsets, which become photorec's recovered names.
JPEG_SECTOR = 200
PNG_SECTOR = 2000
SECTOR = 512
IMAGE_MB = 12


def load(name: str, path: Path) -> ModuleType:
    """Import a hyphenated or suffixless script by path."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def find_fixture(suffix: str) -> Path | None:
    """A small real image of this type, from whatever this machine ships."""
    for base in (Path("/usr/share"), Path("/usr/lib")):
        if not base.is_dir():
            continue
        for candidate in base.rglob(f"*{suffix}"):
            try:
                if candidate.is_file() and 0 < candidate.stat().st_size < 200_000:
                    return candidate
            except OSError:
                continue
    return None


def plant(directory: Path, jpeg: Path, png: Path) -> Path:
    """A raw image holding one jpg and one png at known sector offsets."""
    image = directory / "disk.raw"
    with open(image, "wb") as handle:
        handle.truncate(IMAGE_MB * 1024 * 1024)
        handle.seek(JPEG_SECTOR * SECTOR)
        handle.write(jpeg.read_bytes())
        handle.seek(PNG_SECTOR * SECTOR)
        handle.write(png.read_bytes())
    return image


def accepted(image: Path, directory: Path, family: str) -> bool:
    """Does the installed photorec accept this family in a fileopt list?

    Each probe gets its own directory because photorec APPENDS to
    photorec.log in the CURRENT directory. Sharing one, a probe reads the
    previous probe's verdict: this check reported a valid family as refused
    until the directory was made per-probe.
    """
    with tempfile.TemporaryDirectory(dir=directory) as probe:
        subprocess.run(
            ["photorec", "/log", "/d", str(Path(probe) / "recup"), "/cmd",
             str(image), f"fileopt,everything,disable,{family},enable,search"],
            check=False, capture_output=True, cwd=probe,
        )
        log = Path(probe) / "photorec.log"
        return not (log.exists() and "Syntax error" in log.read_text(errors="replace"))


def carve(image: Path, directory: Path, cmd_str: str, label: str) -> set[str]:
    """Run photorec with one /cmd string; return the suffixes it recovered."""
    out = directory / f"out-{label}"
    out.mkdir()
    # check=False: photorec exits 0 on a refused command line, so the return
    # code is not the signal here. What was recovered is.
    subprocess.run(
        ["photorec", "/d", str(out / "recup"), "/cmd", str(image), cmd_str],
        check=False,
        capture_output=True,
    )
    return {
        p.suffix.lstrip(".").lower()
        for p in out.rglob("*")
        if p.is_file() and p.name != "report.xml"
    }


def main() -> int:
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    recover = load("recover", ROOT / "recover.py")
    checks = 0

    # The string itself. These run everywhere and need no photorec.
    _, full_cmd = recover.fileopt_command("full", [])
    check(full_cmd == "fileopt,everything,enable,search",
          f"full profile builds {full_cmd!r}")
    checks += 1

    for profile, extensions in recover.PROFILES.items():
        line, cmd_str = recover.fileopt_command(profile, extensions)
        if not line:
            continue
        tokens = line.split(",")
        # The whole bug in one assertion: pairs, so an even count, and every
        # second token is the toggle. "jpg,png,enable" is three tokens.
        check(len(tokens) % 2 == 0,
              f"profile {profile}: odd token count in {line!r}")
        check(all(t == "enable" for t in tokens[1::2]),
              f"profile {profile}: a family has no toggle after it: {line!r}")
        check(cmd_str.startswith("fileopt,everything,disable,")
              and cmd_str.endswith(",search"),
              f"profile {profile}: malformed /cmd {cmd_str!r}")
        checks += 3

    if shutil.which("photorec") is None:
        print(f"SKIP: photorec not installed; {checks} string checks ran")
        return report(failures)

    # Every family in the table, against the installed binary. This is the
    # check that matters: a value photorec does not know is not skipped, it
    # refuses the entire /cmd line, so ONE wrong entry disables the profile it
    # appears in. The names are version specific and undocumented, so there is
    # nothing to compare against except photorec itself.
    with tempfile.TemporaryDirectory() as raw:
        blank = Path(raw) / "blank.raw"
        with open(blank, "wb") as handle:
            handle.truncate(64 * 1024)
        for family in sorted(set(recover.PHOTOREC_FAMILIES.values())):
            check(accepted(blank, Path(raw), family),
                  f"photorec refuses the family {family!r}, which "
                  f"PHOTOREC_FAMILIES maps to")
            checks += 1

    jpeg, png = find_fixture(".jpg"), find_fixture(".png")
    if jpeg is None or png is None:
        print(f"SKIP: no fixture images on this machine; {checks} string checks ran")
        return report(failures)

    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        image = plant(directory, jpeg, png)

        # recover.py's own string, for a profile with more than one family.
        _, cmd_str = recover.fileopt_command("art", recover.PROFILES["art"])
        got = carve(image, directory, cmd_str, "fixed")
        check("jpg" in got and "png" in got,
              f"recover.py's own /cmd recovered {sorted(got)}, expected jpg and png")
        checks += 1

        # The positive control this test exists for: the OLD string, built the
        # way the bug built it. Without this the test passes against code that
        # never had the bug and proves nothing about the fix.
        families = sorted({
            recover.PHOTOREC_FAMILIES[e]
            for e in recover.PROFILES["art"]
            if e in recover.PHOTOREC_FAMILIES
        })
        old = f"fileopt,everything,disable,{','.join(families)},enable,search"
        check(carve(image, directory, old, "old") == set(),
              "the pre-fix /cmd string recovered something; "
              "this fixture no longer demonstrates the bug")
        checks += 1

    print(f"PASS: {checks} checks" if not failures else "FAIL")
    return report(failures, printed=True)


def report(failures: list[str], printed: bool = False) -> int:
    if not failures:
        return 0
    if not printed:
        print("FAIL")
    for failure in failures:
        print(f"  - {failure}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
