"""Recovery profiles, and the photorec `/cmd` line that selects their file types."""

from __future__ import annotations

PROFILES: dict[str, list[str]] = {
    "code": [
        "js", "html", "htm", "php", "py", "json", "xml",
        "css", "sql", "sh", "pl", "rb", "java", "c", "h",
        "cpp", "cs", "ts", "jsx", "tsx", "vue", "yaml", "yml",
        "toml", "ini", "conf", "cfg", "env", "gitignore",
    ],
    "art": [
        "png", "jpg", "jpeg", "tiff", "tif", "psd", "bmp",
        "gif", "svg", "webp", "xcf", "raw", "cr2", "nef",
        "ico", "ai", "eps",
    ],
    "writing": [
        "txt", "pdf", "doc", "docx", "odt", "rtf", "md",
        "tex", "epub", "csv", "ods", "xlsx", "xls", "pptx",
        "wpd",
    ],
    "roms": [
        "nes", "snes", "smc", "sfc", "gb", "gbc", "gba", "nds",
        "n64", "z64", "v64", "gen", "md", "sms", "gg",
        "iso", "bin", "cue", "img", "chd", "cso",
        "a26", "a78", "lnx", "pce", "ngp",
        "zip", "7z", "rar", "gz",
    ],
}
PROFILES["all"] = PROFILES["code"] + PROFILES["art"] + PROFILES["writing"] + PROFILES["roms"]

# Everything photorec can find, with no list.
FULL = "full"

# Our extensions mapped to photorec FAMILY names, which are not extensions.
#
# A family is a first-level signature group. `txt` is one family and carries
# 167 signatures -- html, php, py, xml, source of every kind -- so the right
# mapping for a source file is `txt`, not its own suffix. photorec's published
# format list (oob/photorec-file-formats/) documents what comes BACK, and
# names all of those individually.
#
# EVERY VALUE HERE MUST BE A FAMILY THE INSTALLED PHOTOREC ACCEPTS. One that
# is not takes the whole /cmd line down with it: photorec answers `Syntax
# error in command line`, recovers nothing, and exits 0. It does not skip the
# bad name and carry on. tests/test_photorec_fileopt.py drives the installed
# binary and fails on any value it refuses, because this list is version
# specific and nothing in it is guessable.
#
# An extension with no family is dropped, not passed through. Most ROM formats
# have no photorec signature at all -- it carves by content, and a .smc has no
# header to carve on -- so `roms` resolves to the container families.
PHOTOREC_FAMILIES = {
    # Source, markup and config: all of it is the txt family.
    "js": "txt", "html": "txt", "htm": "txt", "php": "txt",
    "py": "txt", "json": "txt", "xml": "xml", "css": "txt",
    "sql": "txt", "sh": "txt", "pl": "txt", "rb": "txt",
    "java": "txt", "c": "txt", "h": "txt", "cpp": "txt",
    "cs": "txt", "ts": "txt", "jsx": "txt", "tsx": "txt",
    "vue": "txt", "yaml": "txt", "yml": "txt", "toml": "txt",
    "ini": "txt", "conf": "txt", "cfg": "txt", "env": "txt",
    "gitignore": "txt", "md": "txt", "txt": "txt",
    # Images. svg is XML text, so it carves as text, not as a picture.
    "png": "png", "jpg": "jpg", "jpeg": "jpg", "tiff": "tif",
    "tif": "tif", "psd": "psd", "bmp": "bmp", "gif": "gif",
    "svg": "txt", "webp": "riff", "xcf": "xcf", "ico": "ico",
    # Camera raw. cr2 and nef are TIFF-based and come back under raw.
    "raw": "raw", "cr2": "raw", "nef": "raw",
    # PostScript, which is what an .ai and an .eps both are.
    "ai": "ps", "eps": "ps",
    # Documents. The OLE2 container is `doc` and holds xls and ppt too;
    # every OOXML and ODF format is a zip, and so is an epub.
    "pdf": "pdf", "doc": "doc", "xls": "doc", "wpd": "wpd",
    "docx": "zip", "xlsx": "zip", "pptx": "zip",
    "odt": "zip", "ods": "zip", "epub": "zip",
    "rtf": "txt", "tex": "txt", "csv": "txt",
    # Archives and disc images, which is most of what the roms profile can
    # actually ask for.
    "zip": "zip", "7z": "7z", "rar": "rar", "gz": "gz",
    "iso": "iso", "bin": "bin", "nes": "nes", "nds": "nds",
}

# One flat table cannot serve two profiles that spell the same extension
# differently. `md` is Markdown in `writing` and Sega Mega Drive in `roms`;
# mapping it to `txt` in `roms` would turn a ROM recovery into a carve of
# every text file on the volume, arriving in output that looks like a
# successful ROM scan.
#
# None means "this extension has no photorec family IN THIS PROFILE", which is
# the same state `smc`, `gb` and the rest of the ROM formats are already in.
PROFILE_FAMILY_OVERRIDES: dict[str, dict[str, str | None]] = {
    "roms": {"md": None},
}


def fileopt_command(profile_name: str, extensions: list[str]) -> tuple[str, str]:
    """(the types line printed, the `/cmd` string photorec is driven with).

    photorec's fileopt list is a sequence of PAIRS: every family name is
    followed by its own toggle, so three families are six tokens. A family
    name with no toggle after it is not "enabled by the next one" -- photorec
    refuses the whole line with `Syntax error in command line: ...` and
    recovers nothing, exit 0, so a run that was never parsed looks like a run
    that found nothing.

    Raises ValueError when no extension resolves to a family: the empty list
    builds `fileopt,everything,disable,,search`, whose empty slot photorec
    refuses exactly as it refuses a missing toggle.
    """
    if profile_name == FULL:
        return "everything,enable", "fileopt,everything,enable,search"
    overrides = PROFILE_FAMILY_OVERRIDES.get(profile_name, {})
    resolved = set()
    for ext in extensions:
        family = overrides[ext] if ext in overrides else PHOTOREC_FAMILIES.get(ext)
        if family is not None:
            resolved.add(family)
    if not resolved:
        raise ValueError(
            f"profile {profile_name!r} resolves to no photorec family, and an "
            f"empty fileopt list is refused by photorec rather than meaning "
            f"'everything'"
        )
    enable_line = ",".join(f"{family},enable" for family in sorted(resolved))
    return enable_line, f"fileopt,everything,disable,{enable_line},search"
