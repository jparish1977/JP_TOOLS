#!/bin/bash
# JP_TOOLS install — sets up code quality tools
set -e

echo "==> Python tools"
pip install --upgrade ruff mypy pytest pytest-cov

echo ""
echo "==> Node tools (requires npm)"
if command -v npm &>/dev/null; then
    npm install -g eslint prettier
else
    echo "    npm not found — skipping eslint/prettier"
    echo "    Install Node.js from https://nodejs.org then re-run this script"
fi

echo ""
echo "Done. Usage:"
echo "  python JP_TOOLS/check.py <path> [--pretty]"
echo "  python JP_TOOLS/fix.py   <path> [--dry-run]"
