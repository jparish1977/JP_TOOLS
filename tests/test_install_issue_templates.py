#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_install_issue_templates.py
install-issue-templates.py: installs every shipped template, refuses to
overwrite without --force, and refuses a directory that is not a repo.

    python tests/test_install_issue_templates.py
"""

import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, load_script, run
from fake_tools import run_main

INSTALL = load_script("install-issue-templates.py")
SHIPPED = sorted(p.name for p in (ROOT / "templates" / "issue-templates").glob("*.yml"))


def test_install_refuse_force() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        check("not a repo", run_main(INSTALL.main, str(repo))[0], 1)
        (repo / ".git").mkdir()
        check("installs", run_main(INSTALL.main, str(repo))[0], 0)
        dest = repo / ".github" / "ISSUE_TEMPLATE"
        check("every shipped template", sorted(p.name for p in dest.glob("*.yml")), SHIPPED)
        (dest / SHIPPED[0]).write_text("mine\n")
        check("refuses to overwrite", run_main(INSTALL.main, str(repo))[0], 1)
        check("left alone", (dest / SHIPPED[0]).read_text(), "mine\n")
        check("--force overwrites", run_main(INSTALL.main, str(repo), "--force")[0], 0)
        check("overwritten", (dest / SHIPPED[0]).read_bytes(),
              (ROOT / "templates" / "issue-templates" / SHIPPED[0]).read_bytes())


if __name__ == "__main__":
    sys.exit(run(globals(), "install-issue-templates"))
