# JP_TOOLS

Code quality toolbox for use with agentic AI in professional software development.
Runs linters, type checkers, and formatters against arbitrary files or directories
and returns structured JSON designed for AI agent consumption.

## Tools

| Script | Purpose |
|---|---|
| `check.py` | Run quality checks, output JSON report |
| `fix.py` | Auto-fix what's fixable, report what isn't |

## Install

**Windows (PowerShell):**
```powershell
.\install.ps1
```

**Linux / Mac:**
```bash
bash install.sh
```

## Usage

```bash
# Check a single file
python check.py src/mymodule.py --pretty

# Check a whole directory
python check.py src/ --pretty

# Force language
python check.py app.py --lang python

# Run specific tools only
python check.py app.py --tools ruff

# Auto-fix
python fix.py src/mymodule.py

# Dry run (show diff, don't write)
python fix.py src/ --dry-run --pretty
```

## Output format

```json
{
  "target": "/abs/path/to/file.py",
  "language": "python",
  "checks": [
    {
      "tool": "ruff",
      "status": "fail",
      "issues": [
        {
          "file": "file.py",
          "line": 42,
          "col": 5,
          "severity": "error",
          "rule": "F401",
          "message": "'os' imported but unused",
          "fixable": true
        }
      ]
    }
  ],
  "summary": {
    "total": 1,
    "errors": 1,
    "warnings": 0,
    "fixable": 1
  }
}
```

**Exit codes:** `0` = clean (no errors), `1` = errors found, `2` = usage/path error.

## Configs

Shared configuration files live in `configs/`:

| File | Tool | Usage |
|---|---|---|
| `configs/ruff.toml` | ruff | Copy/symlink to project root as `ruff.toml` |
| `configs/mypy.ini` | mypy | Copy/symlink to project root as `mypy.ini` |
| `configs/eslint.config.js` | ESLint 9+ | Copy/symlink to project root |

## Supported tools

| Tool | Language | Install |
|---|---|---|
| ruff | Python | `pip install ruff` |
| mypy | Python | `pip install mypy` |
| eslint | JS/TS | `npm install -g eslint` |
| prettier | JS/TS | `npm install -g prettier` |

Tools not found on PATH are reported as `"status": "unavailable"` — the rest still run.
