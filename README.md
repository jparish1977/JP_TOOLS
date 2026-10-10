# JP_TOOLS

[![Code Quality](https://github.com/jparish1977/JP_TOOLS/actions/workflows/check.yml/badge.svg)](https://github.com/jparish1977/JP_TOOLS/actions/workflows/check.yml)

Developer toolbox for code quality, data recovery, file analysis, and archive management.
Built with pluggable interfaces -- every tool is coded to contracts with swappable adapters.

> **Read [METHODOLOGY.md](METHODOLOGY.md) first.**
> JP_TOOLS is a set of practices, not just a set of scripts. The methodology is
> the asset. Every tool in here, and every project the toolbox installs itself
> into, is an application of the same discipline: hexagonal ports/adapters,
> immutable value objects, a zero-framework-deps domain, enforced quality gates.
> New projects adopting `install-hooks.py` or `init-ci.py` are expected to
> follow the methodology.

## Quick Start

```bash
git clone https://github.com/jparish1977/JP_TOOLS.git
cd JP_TOOLS

# Code quality tools
pip install ruff mypy pip-audit         # Python linting/typing
npm install                             # JS/TS/CSS (eslint, typescript-eslint, stylelint, prettier)
composer install                        # PHP (phpstan, phpcs, rector, phpunit)

# Recovery & analysis tools (Linux/WSL)
bash install-recovery.sh

# Recovery & analysis tools (Windows native)
powershell install-recovery.ps1
```

### Private libraries: find-dupes.php

`find-dupes.php` is the one tool that needs `iteration8/utilities` (and through
it `iteration8/core`), private libraries. Both are dev requirements in
`composer.json`, so `composer install` needs to know where they are. Say so once,
in your global composer config, with a git URL you can read:

```bash
composer config -g repositories.iteration8-core vcs https://github.com/jparish1977/iteration8-core.git
composer config -g repositories.iteration8-utilities vcs https://github.com/jparish1977/iteration8-utilities.git
composer install
```

Without access to them, `composer install` stops on the two packages.

## Tools

### Code Quality

| Script | Purpose |
|---|---|
| `check.py` | Run quality checks, output JSON report (what other tools may rely on in it: `docs/check-output.md`) |
| `fix.py` | Auto-fix what's fixable, report what isn't |
| `coverage-gate.py` | Fail under a line-coverage threshold, read from a Clover report (PHP; see `docs/coverage.md`) |
| `check-pins.py` | Check that the gate's tools are pinned to one version in `requirements-dev.txt`, `composer.json` and `package.json`, and installed at it |
| `list-exemptions.py` | Inventory every `# pragma: no cover` and its reason, the ones the gate lets pass included |
| `list-suppressions.py` | Inventory every suppression in every language (`noqa`, `type: ignore`, shellcheck, cppcheck, phpstan, phpcs, eslint, stylelint) and its reason, the ones the gate lets pass included |
| `install-hooks.py` | Install the pre-commit and commit-msg hooks into any git repo |
| `init-ci.py` | Copy GitHub Actions CI template into a repo |

### File Analysis (PHP -- hex architecture)

| Script | Purpose |
|---|---|
| `find-dupes.php` | Find duplicate files or compare directories |
| | Pluggable hashers, caches (memory, filesystem, SQLite), output formats |
| | Worker pool for parallel hashing, persistent hash DB for instant re-scans |

`find-dupes.php` depends on `iteration8/utilities`, which lives in a private repo -- see
[Private libraries: find-dupes.php](#private-libraries-find-dupesphp) for installing it.

### Data Recovery (Python)

| Script | Purpose |
|---|---|
| `recover.py` | File recovery via photorec with profile-based filtering |
| `undelete.py` | Recover deleted files from NTFS/ext filesystems |
| `image-disk.py` | Forensic disk imaging via ddrescue |
| `scan-image.py` | Scan raw images for ROM signatures and embedded files |
| `ntfs-inventory.py` | List an **unmountable** NTFS volume to TSV -- resumable, metadata only |
| `ntfs-size.py` | Size a subtree on an unmountable volume, before committing to a copy |
| `ntfs-extract.py` | Copy a tree off an unmountable volume -- resumable, skips unreadable files |
| `inventory-drive.py` | Catalogue a **mounted** volume to the same TSV format, for offline diffing |

The last four cover a gap between imaging and carving: **the volume will not
mount, but the live files are intact.** `image-disk.py` needs somewhere to put a
full image, and `undelete.py` looks for deleted files -- neither helps when the
files are fine and only the filesystem metadata is damaged. Inventory first,
size what you might want, then copy. Comparing that inventory against drives you
already own is usually what shrinks the job most.

### System Hygiene (Python)

| Script | Purpose |
|---|---|
| `spool-audit.py` | Report documents CUPS keeps after printing, including its TempDir, and remove (`--purge`) the content-proven leftovers `cancel` cannot reach. Never touches config, daemon, job files, or anything it could not identify -- it names the CUPS commands that clear those |

Printing sends the whole document through CUPS, and CUPS may keep a copy after
the job finishes. Print a password, a recovery sheet or a private key and that
copy outlives the paper, on a machine that may not be yours.

It exists as a tool rather than a one-liner because the obvious one-liner
reports danger as safety: `sudo ls /var/spool/cups | grep -E 'd0*85' || echo
CLEAN` prints CLEAN when sudo fails, making a check that never ran
indistinguishable from a clean result.

**Why the problem exists:** CUPS documents `PreserveJobFiles` as defaulting to
`No`, so completed jobs should leave nothing behind. In a clean Ubuntu 24.04
container with the stock config and no such directive, the document file is
still present 60 seconds after the job finishes. Writing `PreserveJobFiles No`
explicitly stops it. An unset directive and an explicit `No` behave differently,
which matches [apple/cups#6083](https://github.com/apple/cups/issues/6083)
(open, no root cause, repo archived March 2026 -- reported there on macOS, and
this reproduces it on Linux). The explicit `No` is the fix, and the report
says so rather than writing it for you.

It audits `tmp/` as well as the top level. CUPS `TempDir` holds document
content during filtering, so a top-level-only check reports clean while
readable data sits one directory down. Passing job ids highlights those jobs in
the report; the exit code still answers the wider question, because "the job
you asked about is gone" and "the spool is empty" are different claims.

A file it cannot identify is reported and blocks a clean verdict. Over-reporting
is the safe direction for a report, and the wrong one for anything that deletes.

**What CUPS can clear, CUPS clears.** This tool reports; it does not delete.
Clearing the spool is `cancel -a -x` and one config line, work CUPS already does
correctly, so the report names the commands and leaves them to you:

```
cancel -a -x                      # cancel every job and its documents
PreserveJobFiles No               # in /etc/cups/cupsd.conf, then restart cups
```

**`--purge` is back, for the one job those commands cannot do.** Files whose
own first bytes prove they are print data but which carry no job id -- TempDir
leftovers, a document copied to `d00085-001.bak` -- are unreachable by
`cancel`, and without this flag the report can only tell you to remove them by
hand, with filenames that may contain newlines and terminal escapes. `--purge`
removes exactly those files and nothing else. The delete set is the
classifier's evidence Kind, so job files, unidentified files and runtime
artifacts are excluded by type rather than by a check; it refuses job ids
(nothing it removes has one); it writes no file and touches no daemon, so the
two `--fix` bugs have no code to live in; and it cannot traverse a symlink --
every path component is opened `O_NOFOLLOW` against a directory descriptor, so
the escape that killed the old purge is the kernel's ELOOP rather than a path
comparison that can be raced. Unidentified files stay yours to judge:
over-reporting is the safe direction for a report, and it was the wrong
direction for the old delete set, which destroyed a plain-text README on
exactly that confusion.

It reads directly and has no privilege-escalation path, so run it under `sudo`
for the real spool. That is deliberate: an internal sudo fallback was a second
implementation of the same listing, the two disagreed repeatedly about depth
limits, directories and unreadable files, and that divergence caused a large
share of the tool's bug history. One code path with one set of rules is worth
more than the convenience.

### Binary Triage (Python)

| Script | Purpose |
|---|---|
| `autoit-extract.py` | Decode the AutoIt script inside a compiled PE and scan it for network, persistence and execution indicators |

An AutoIt binary tells you nothing from the outside: every stub imports
`InternetOpenA` and `URLDownloadToFile` because it embeds the whole language,
so antivirus emits generic verdicts like `Win.Trojan.Autoit-73` off that
surface alone. Only the decoded script settles it.

`autoit-ripper` reads the resource layout; this also handles the **overlay**
layout used by older Aut2Exe builds, where the payload is appended after the
last section and `autoit-ripper` stops with "Couldn't find any appropiate PE
resource directory" -- a layout miss reported as a failure.

```bash
pip install autoit-ripper==1.2.0    # the version pyproject.toml declares
python3 autoit-extract.py SUSPECT.exe -o ./out
```

Nothing is executed; the input is read as bytes. Read the indicator lines
rather than counting them -- a device-flashing tool legitimately runs other
binaries, and what matters is which ones and from where.

### VM / Image Triage (Python)

| Script | Purpose |
|---|---|
| `vm-image-boot.py` | Boot a disk image headless under KVM and screenshot what it does |

For an image you cannot plug into anything: does it boot, how far does it get,
and what is on screen when it stops. A serial console shows only what the guest
chooses to log; the framebuffer shows what actually happened.

```bash
sudo apt install qemu-system-x86 qemu-utils ovmf
python3 vm-image-boot.py disk.img -w 110 -o screen.png --forward 12340:1234
```

Three things it encodes, all learned the hard way, and all three read as a
broken image when they are not:

- **`virtio-vga`, not `-vga std`.** The default Bochs adapter has no
  `/dev/dri/card0`, so any guest with a KMS-configured Xorg dies with
  `no screens found`.
- **A forwarded port proves nothing.** QEMU binds `hostfwd` ports on the *host*
  immediately, whether or not the guest listens; the connect succeeds and then
  resets. Judge a guest service by its reply.
- **The container format is probed, never assumed.** Hardcoding `raw` against a
  qcow2 base is silent at every layer -- create says nothing, `qemu-img check`
  finds no errors -- and hands the guest the base's `QFI` magic where a boot
  sector should be.

Defaults to a throwaway qcow2 overlay, so a botched boot cannot damage the
original image. PPM to PNG conversion is stdlib-only: no Pillow, no ImageMagick.

### Archive Management (Python)

| Script | Purpose |
|---|---|
| `chd.py` | CHD archive management: create, verify, extract, import, delta, merge, batch |
| `chd-hunkmap.py` | CHD v5 hunk map: how each hunk is stored, duplicates, and the tar members they belong to |

### Deployment

| Script | Purpose |
|---|---|
| `deploy-s3.py` | Deploy static sites to S3 |
| `deploy-all.py` | S3 + GitHub Pages deploy |

## Usage

```bash
# Check a whole project (auto-detects languages, runs correct tools per file type)
python check.py /path/to/project --pretty

# Check a single file
python check.py src/app.py --pretty

# Force language
python check.py app.py --lang python

# Run specific tools only
python check.py app.py --tools ruff,mypy

# Include security audit
python check.py /path/to/project --audit --pretty

# Auto-fix
python fix.py src/app.py

# Dry run (show what would change, don't write)
python fix.py src/ --dry-run --pretty

# Install pre-commit hook
python install-hooks.py /path/to/repo

# Install CI workflow
python init-ci.py /path/to/repo
```

## Multi-Language Directory Scanning

When pointed at a directory, `check.py` scans all files, groups them by language,
and runs that language's tools. Which tools each language gets is
`DEFAULT_TOOLS` (and `AUDIT_TOOLS` for `--audit`) in `jpcheck/registry.py`,
the one place it is kept.

| Language | Extensions |
|---|---|
| Python | `.py`, or a `#!` naming python |
| JavaScript / TypeScript | `.js`, `.mjs`, `.cjs`, `.jsx`, `.ts`, `.tsx` |
| CSS | `.css`, `.scss`, `.less` |
| HTML | `.html`, `.htm` |
| PHP | `.php` |
| Shell | `.sh`, `.bash`, or a `#!` naming a shell |
| PowerShell | `.ps1`, `.psm1`, `.psd1`, or a `#!` naming pwsh |
| C / C++ | `.c`, `.cc`, `.cpp`, `.h`, `.hpp`, `.ino` |

Skipped directories: `node_modules`, `vendor`, `__pycache__`, `.git`, `.venv`, `venv`, `dist`, `build`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `.tox`, `site-packages`

**Smells**, in every language with code to measure (METHODOLOGY 2.14): a file
or a function too long or too complex, by the limits in `jpcheck/smells.py`.
Code outside every function is the file's main, measured as one more function,
`(top level)`. A smell the change introduces fails; one that was already there
warns. Complexity is each language's standard tool's count, listed in
`jpcheck/measure.py`.

## Output Format

```json
{
  "target": "/path/to/project",
  "mode": "multi-language",
  "languages": [
    {"language": "python", "file_count": 3, "tools": ["ruff", "mypy"]},
    {"language": "js", "file_count": 2, "tools": ["eslint", "prettier"]}
  ],
  "checks": [
    {
      "tool": "ruff",
      "status": "fail",
      "issues": [
        {"file": "app.py", "line": 42, "col": 5, "severity": "error",
         "rule": "F401", "message": "'os' imported but unused", "fixable": true}
      ]
    }
  ],
  "summary": {"total": 1, "errors": 1, "warnings": 0, "fixable": 1}
}
```

**Exit codes:** `0` = every check ran and found no errors, `1` = errors found,
`2` = usage/path error, or a check that could not run.

A tool that is not found is reported as `"status": "unavailable"` and the rest
still run, but the run then exits `2` and names the tool on stderr: a check that
did not run is not a pass. The same holds for a tool that exits
abnormally, such as ruff on a config that fails to load (`"status": "error"`).
`tests/test_check_exit_codes.py` pins this behaviour.

`fix.py` follows the same rule for its fixers: `0` = every fixer ran (or none
applies to the target), `2` = a fixer was not installed or stopped short
(`"unavailable"` or `"error"`), named on stderr. What a fixer leaves unfixed
does not change its exit code; run `check.py` for that. `tests/test_fix.py`
pins it.

## Configs

Shared configuration files in `configs/`:

| File | Tool |
|---|---|
| `ruff.toml` | ruff |
| `mypy.ini` | mypy |
| `eslint.config.js` | ESLint 9+ (flat config) |
| `phpstan.neon` | PHPStan |
| `phpcs.xml` | PHP_CodeSniffer |
| `rector.php` | Rector |

## Git Hooks

```bash
# Install into any repo (installs whichever hook is missing)
python install-hooks.py /path/to/repo

# Remove both
python install-hooks.py /path/to/repo --remove

# Which repos' hooks are current
python install-hooks.py --status ~/projects
```

- **pre-commit** runs `check.py` on each staged file before every commit, and
  fails the commit if any has errors.
- **commit-msg** refuses a message that links a Claude Code session
  (`claude.ai/code/session_...`), naming the line: such a link points at a
  private session and would be published with the repository's history.

Use `git commit --no-verify` to bypass when needed.

## CI Integration

```bash
# Copy GitHub Actions workflow into a repo
python init-ci.py /path/to/repo
```

Creates `.github/workflows/check.yml` -- runs on push to main and on PRs.

## FileScanner Library

**FileScanner does not live in this repo.** It ships in `iteration8/utilities`
under `src/FileScanner/`, and `find-dupes.php` here (with `lib/FindDupes.php`)
is the composition root that wires it up. Install it as
[Private libraries: find-dupes.php](#private-libraries-find-dupesphp) says.

It is a PHP library with hexagonal architecture: interfaces for everything, so
adapters swap without touching business logic.

```
Iteration8\Utilities\FileScanner\   (src/FileScanner/ in iteration8/utilities)
├── Contract/           Interfaces (ports)
│   ├── HasherInterface
│   ├── FilesystemInterface
│   ├── HashCacheInterface
│   ├── OutputInterface
│   ├── ScannerInterface
│   ├── SchedulerInterface
│   └── DuplicateFinderInterface
├── Hasher/             Hash adapters
│   ├── NativeHasher            PHP hash_file(), cross-platform
│   └── ShellHasher             md5sum/sha256sum, Linux, parallel-friendly
├── Cache/              Storage adapters
│   ├── MemoryCache             In-memory, single-run
│   ├── FilesystemCache         File-per-hash on disk
│   └── SqliteCache             Persistent DB, staleness checks, dupe queries
├── Filesystem/LocalFilesystem  Native filesystem adapter
├── Output/
│   ├── ConsoleOutput           Human-readable terminal output
│   └── JsonOutput              Machine-readable JSON
├── Scheduler/
│   ├── SequentialScheduler     One-at-a-time (default)
│   ├── WorkerPoolScheduler     Parallel via proc_open worker pool
│   └── hash-worker.php         Worker process for parallel hashing
├── Scanner                     Directory walker + hasher
├── DirectoryWalker
├── FileHasher
├── IgnoreFilter
├── DuplicateFinder             Find dupes within or across directories
├── FileEntry                   Value object
├── DuplicateGroup              Value object
└── ComparisonResult            Value object
```

Its tests live with it, in that package's `tests/Unit/FileScanner/` and
`tests/Integration/FileScanner/`.

### find-dupes.php Usage

```bash
# Find duplicates within a directory
php find-dupes.php ~/Pictures

# Compare two directories
php find-dupes.php ~/backup1 ~/backup2

# JSON output
php find-dupes.php ~/data --json

# Persistent SQLite cache (hash once, instant re-scans)
php find-dupes.php ~/data --db ~/.hash-cache.db

# SHA-256 instead of MD5
php find-dupes.php ~/data --algo sha256

# Parallel hashing with 4 worker processes
php find-dupes.php ~/data --workers 4

# Ignore specific directories
php find-dupes.php ~/data --ignore vendor,node_modules
```

## Recovery Tools

### Profiles (recover.py)

| Profile | File Types |
|---|---|
| `code` | JS, HTML, PHP, Python, JSON, XML, CSS, SQL, shell, etc. |
| `art` | PNG, JPG, TIFF, PSD, BMP, GIF, SVG, WebP, etc. |
| `writing` | TXT, PDF, DOC, ODT, RTF, MD, EPUB, etc. |
| `roms` | NES, SNES, GB, GBA, N64, ISO, BIN, CHD, ZIP, 7z, etc. |
| `all` | All of the above |
| `full` | Everything photorec can find |

```bash
# Recover code files from a drive
python recover.py /dev/sdb1 ./recovered --profile code

# Image a failing drive first, then recover from the image
python image-disk.py /dev/sdb ~/images/drive.img
python recover.py ~/images/drive.img ./recovered --profile all

# On Windows, a drive letter is read as its physical drive (as administrator)
python recover.py F: ./recovered --profile all
```

### CHD Archive Management

```bash
# Create CHD from disc image
python chd.py create game.iso game.chd

# Import any format (auto-converts CSO, PBP, NRG, MDF, ECM)
python chd.py import game.cso game.chd

# Batch convert a whole directory
python chd.py batch-create ~/roms/psx --format iso

# Verify integrity
python chd.py verify game.chd
python chd.py batch-verify ~/roms/chd

# Delta (incremental) CHD, and back to a standalone one
python chd.py delta base.chd updated.img updated.chd
python chd.py merge base.chd updated.chd standalone.chd

# Analyze block-level deduplication
python chd-hunkmap.py archive.chd --self-refs --trace archive.tar
```

### ROM Signature Scanner

```bash
# Scan a disk image for ROM magic bytes
python scan-image.py disk.img --signatures roms

# Scan with binwalk + ROM signatures
python scan-image.py disk.img --signatures all --output ./results
```

Detects: NES, SNES, Game Boy, GBA, N64, Genesis, Master System, Game Gear,
Atari 7800, Atari Lynx, Neo Geo, FDS, Sega 32X, Sega CD, CHD, CSO, CUE,
PBP, WBFS, NKit, GameCube, 3DS, NDS, WordPerfect, ISO 9660.

## Testing

The PHP tools declared in `composer.json` (phpstan, phpcs, phpunit, rector) are
here to be run *against other projects* by `check.py`. FileScanner's tests live
with FileScanner, in `iteration8/utilities`.

```bash
# Check this repo the way CI does
python check.py . --pretty

# Python linting directly
ruff check recover.py image-disk.py undelete.py scan-image.py chd.py chd-hunkmap.py

# FileScanner's own suite, from a clone of iteration8/utilities
php vendor/bin/phpunit
php vendor/bin/phpstan analyse src/ --level=8
php vendor/bin/phpcs src/ --standard=PSR12
```

### Known gap: this repo does not yet meet its own bar

`METHODOLOGY.md` §2.5 says quality gates are not optional and puts the coverage
threshold at 95%. JP_TOOLS does not currently meet that standard on itself:

- **No test suite.** None of the Python tools here have unit tests.
- **`python check.py .` reports 89 issues on master**, 86 of them errors, 54
  auto-fixable.

CI gates the two workflows it runs, so this is not invisible, but a toolbox that
distributes a discipline should hold itself to it first. Recorded here rather
than left for someone to discover by running the tool on its own repo.
