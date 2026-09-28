#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_photorec_fileopt.py
The photorec `/cmd` string recover.py builds, and against a real photorec.

photorec's fileopt list is PAIRS: each family name is followed by its own
toggle. "jpg,png,enable" is refused with `Syntax error in command line`,
after which photorec recovers nothing and exits 0, so a scan that was never
parsed looks like a disk with nothing on it. It only shows at two or more
families, which is every profile but `roms` and `full`.

The string checks always run. With photorec installed, every family name is
tried against it, and a raw image with a jpg and a png planted in it is
carved with recover.py's own string; the pair-less string is carved too, as
the control that shows the fixture can tell them apart. Without photorec, or
without a small jpg and png on this machine to plant, those print SKIP.

    python tests/test_photorec_fileopt.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, run
from recovery.profiles import PHOTOREC_FAMILIES, PROFILES, fileopt_command

# Planted at these sector offsets, which become photorec's recovered names.
JPEG_SECTOR = 200
PNG_SECTOR = 2000
SECTOR = 512
IMAGE_MB = 12


def test_full_profile_sends_no_list() -> None:
    check("full", fileopt_command("full", [])[1], "fileopt,everything,enable,search")


def test_every_profile_is_pairs() -> None:
    for profile, extensions in PROFILES.items():
        line, cmd_str = fileopt_command(profile, extensions)
        tokens = line.split(",")
        check(f"{profile}: even token count", len(tokens) % 2, 0)
        check(f"{profile}: every second token is the toggle",
              set(tokens[1::2]), {"enable"})
        check_true(f"{profile}: /cmd shape",
                   cmd_str.startswith("fileopt,everything,disable,") and cmd_str.endswith(",search"))


def test_roms_do_not_enable_txt() -> None:
    """`md` is Markdown in `writing` and Mega Drive in `roms`; roms must not carve text."""
    check_true("no txt", "txt" not in fileopt_command("roms", PROFILES["roms"])[0].split(","))


def test_an_empty_family_set_is_refused() -> None:
    try:
        fileopt_command("code", ["smc", "gb"])
    except ValueError:
        # reason: the refusal is the result under test; reaching the line
        # below instead is the failure
        return
    check_true("an empty family set built a /cmd line instead of refusing", False)


def _find_fixture(suffix: str) -> Path | None:
    """A small real image of this type, from whatever this machine ships."""
    for base in (Path("/usr/share"), Path("/usr/lib")):
        if not base.is_dir():
            continue
        for candidate in base.rglob(f"*{suffix}"):
            try:
                if candidate.is_file() and 0 < candidate.stat().st_size < 200_000:
                    return candidate
            except OSError:  # reason: an unreadable candidate is just not a fixture
                continue
    return None


def _accepted(image: Path, directory: Path, family: str) -> bool:
    """Does the installed photorec accept FAMILY in a fileopt list?

    Each probe gets its own directory: photorec APPENDS to photorec.log in
    the current directory, and a shared one would hold the previous verdict.
    """
    with tempfile.TemporaryDirectory(dir=directory) as probe:
        subprocess.run(
            ["photorec", "/log", "/d", str(Path(probe) / "recup"), "/cmd",
             str(image), f"fileopt,everything,disable,{family},enable,search"],
            check=False, capture_output=True, cwd=probe,
        )
        log = Path(probe) / "photorec.log"
        return not (log.exists() and "Syntax error" in log.read_text(errors="replace"))


def _carve(image: Path, directory: Path, cmd_str: str, label: str) -> set[str]:
    """Run photorec with one /cmd string; the suffixes it recovered.

    photorec exits 0 on a refused command line, so what was recovered is the
    signal, not the exit status.
    """
    out = directory / f"out-{label}"
    out.mkdir()
    subprocess.run(["photorec", "/d", str(out / "recup"), "/cmd", str(image), cmd_str],
                   check=False, capture_output=True)
    return {p.suffix.lstrip(".").lower() for p in out.rglob("*")
            if p.is_file() and p.name != "report.xml"}


def photorec_test_every_family_is_accepted() -> None:
    """One family photorec does not know refuses the whole line, so each is tried."""
    with tempfile.TemporaryDirectory() as raw:
        blank = Path(raw) / "blank.raw"
        with open(blank, "wb") as handle:
            handle.truncate(64 * 1024)
        for family in sorted(set(PHOTOREC_FAMILIES.values())):
            check_true(f"photorec accepts the family {family!r}",
                       _accepted(blank, Path(raw), family))


def photorec_test_the_art_profile_recovers_two_families() -> None:
    jpeg, png = _find_fixture(".jpg"), _find_fixture(".png")
    if jpeg is None or png is None:
        print("SKIP: no small jpg and png on this machine to plant")
        return
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        image = directory / "disk.raw"
        with open(image, "wb") as handle:
            handle.truncate(IMAGE_MB * 1024 * 1024)
            handle.seek(JPEG_SECTOR * SECTOR)
            handle.write(jpeg.read_bytes())
            handle.seek(PNG_SECTOR * SECTOR)
            handle.write(png.read_bytes())
        got = _carve(image, directory, fileopt_command("art", PROFILES["art"])[1], "pairs")
        check_true(f"recovered {sorted(got)}, expected jpg and png", {"jpg", "png"} <= got)
        families = sorted({PHOTOREC_FAMILIES[e] for e in PROFILES["art"] if e in PHOTOREC_FAMILIES})
        unpaired = f"fileopt,everything,disable,{','.join(families)},enable,search"
        check("the unpaired string recovers nothing (the control)",
              _carve(image, directory, unpaired, "unpaired"), set())


if __name__ == "__main__":
    tests = dict(globals())
    if shutil.which("photorec") is None:
        print("SKIP: photorec not installed; the string checks run alone")
    else:
        tests.update({k.removeprefix("photorec_"): v for k, v in globals().items()
                      if k.startswith("photorec_test_")})
    sys.exit(run(tests, "photorec fileopt"))
