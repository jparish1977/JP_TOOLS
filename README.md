# JP_TOOLS

[![Code Quality](https://github.com/jparish1977/JP_TOOLS/actions/workflows/check.yml/badge.svg)](https://github.com/jparish1977/JP_TOOLS/actions/workflows/check.yml)

Developer toolbox for code quality, data recovery, file analysis, and archive management.
Built with pluggable interfaces — every tool is coded to contracts with swappable adapters.

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

## Tools

### Code Quality

| Script | Purpose |
|---|---|
| `check.py` | Run quality checks, output JSON report |
| `fix.py` | Auto-fix what's fixable, report what isn't |
| `install-hooks.py` | Install pre-commit hook into any git repo |
| `init-ci.py` | Copy GitHub Actions CI template into a repo |

### File Analysis (PHP — hex architecture)

| Script | Purpose |
|---|---|
| `find-dupes.php` | Find duplicate files or compare directories |
| | Pluggable hashers, caches (memory, filesystem, SQLite), output formats |
| | Worker pool for parallel hashing, persistent hash DB for instant re-scans |

### Data Recovery (Python)

| Script | Purpose |
|---|---|
| `recover.py` | File recovery via photorec with profile-based filtering |
| `undelete.py` | Recover deleted files from NTFS/ext filesystems |
| `image-disk.py` | Forensic disk imaging via ddrescue |
| `scan-image.py` | Scan raw images for ROM signatures and embedded files |

### Archive Management (Python)

| Script | Purpose |
|---|---|
| `chd.py` | CHD archive management — create, verify, extract, import, delta, batch |
| `chd-hunkmap.py` | CHD hunk map analysis — block-level dedup intelligence |

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
and runs the appropriate tools for each:

| Language | Extensions | Tools | Audit |
|---|---|---|---|
| Python | `.py` | ruff, mypy | pip-audit |
| JavaScript | `.js`, `.jsx` | eslint, prettier | npm audit |
| TypeScript | `.ts`, `.tsx` | eslint (typescript-eslint), prettier | npm audit |
| CSS | `.css`, `.scss`, `.less` | stylelint, prettier | — |
| HTML | `.html` | eslint, stylelint, prettier | — |
| PHP | `.php` | phpstan, phpcs, rector | composer audit |

Skipped directories: `node_modules`, `vendor`, `__pycache__`, `.git`, `.venv`, `dist`, `build`

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

**Exit codes:** `0` = clean, `1` = errors found, `2` = usage/path error.

Tools not found on PATH are reported as `"status": "unavailable"` — the rest still run.

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

## Pre-Commit Hook

```bash
# Install into any repo
python install-hooks.py /path/to/repo

# Remove
python install-hooks.py /path/to/repo --remove
```

Runs `check.py .` before every commit. Fails the commit if errors are found.
Use `git commit --no-verify` to bypass when needed.

## CI Integration

```bash
# Copy GitHub Actions workflow into a repo
python init-ci.py /path/to/repo
```

Creates `.github/workflows/check.yml` — runs on push to main and on PRs.

## FileScanner Library

PHP library with hexagonal architecture — interfaces for everything, swap adapters without touching business logic.

```
lib/FileScanner/
├── Contract/           Interfaces (ports)
│   ├── HasherInterface.php
│   ├── FilesystemInterface.php
│   ├── CacheInterface.php
│   ├── OutputInterface.php
│   ├── ScannerInterface.php
│   ├── SchedulerInterface.php
│   └── DuplicateFinderInterface.php
├── Hasher/             Hash adapters
│   ├── NativeHasher.php        PHP hash_file() — cross-platform
│   └── ShellHasher.php         md5sum/sha256sum — Linux, parallel-friendly
├── Cache/              Storage adapters
│   ├── MemoryCache.php          In-memory, single-run
│   ├── FilesystemCache.php      File-per-hash on disk
│   └── SqliteCache.php          Persistent DB, staleness checks, dupe queries
├── Filesystem/
│   └── LocalFilesystem.php      Native filesystem adapter
├── Output/
│   ├── ConsoleOutput.php        Human-readable terminal output
│   └── JsonOutput.php           Machine-readable JSON
├── Scheduler/
│   ├── SequentialScheduler.php  One-at-a-time (default)
│   ├── WorkerPoolScheduler.php  Parallel via proc_open worker pool
│   └── hash-worker.php          Worker process for parallel hashing
├── Scanner.php          Directory walker + hasher
├── DuplicateFinder.php  Find dupes within or across directories
├── FileEntry.php        Value object
├── DuplicateGroup.php   Value object
├── ComparisonResult.php Value object
└── tests/
    ├── HasherTest.php
    ├── CacheTest.php
    ├── ValueObjectTest.php
    ├── ScannerTest.php
    ├── DuplicateFinderTest.php
    └── phpunit.xml
```

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

# Recover everything
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

# Delta (incremental) CHD
python chd.py delta base.chd updated.img updated.chd

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

```bash
# PHP unit tests (46 tests, 107 assertions)
php vendor/bin/phpunit --configuration lib/FileScanner/tests/phpunit.xml

# PHP with coverage (requires PCOV)
php vendor/bin/phpunit --configuration lib/FileScanner/tests/phpunit.xml --coverage-text

# PHP static analysis (level 8)
php vendor/bin/phpstan analyse lib/FileScanner/ --level=8

# PHP code style (PSR-12)
php vendor/bin/phpcs lib/FileScanner/ --standard=PSR12

# Python linting
ruff check recover.py image-disk.py undelete.py scan-image.py chd.py chd-hunkmap.py
```
