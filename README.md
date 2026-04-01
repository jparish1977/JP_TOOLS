# JP_TOOLS

[![Code Quality](https://github.com/jparish1977/JP_TOOLS/actions/workflows/check.yml/badge.svg)](https://github.com/jparish1977/JP_TOOLS/actions/workflows/check.yml)

Code quality toolbox for use with agentic AI in professional software development.
Runs linters, type checkers, formatters, and security auditors against arbitrary files
or directories and returns structured JSON designed for AI agent consumption.

## Quick Start

```bash
git clone https://github.com/jparish1977/JP_TOOLS.git
cd JP_TOOLS
pip install ruff mypy pip-audit         # Python tools
npm install                             # JS/TS/CSS tools (eslint, typescript-eslint, stylelint, prettier)
composer install                        # PHP tools (phpstan, phpcs, rector)
```

## Scripts

| Script | Purpose |
|---|---|
| `check.py` | Run quality checks, output JSON report |
| `fix.py` | Auto-fix what's fixable, report what isn't |
| `install-hooks.py` | Install pre-commit hook into any git repo |
| `init-ci.py` | Copy GitHub Actions CI template into a repo |

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
