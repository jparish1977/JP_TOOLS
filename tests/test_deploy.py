#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_deploy.py
deploy-all.py and deploy-s3.py, on real git repositories with a bare
remote, against a fake AWS CLI: the refusals, the gh-pages sync, the S3
command, and that every outcome ends back on the starting branch.

    python tests/test_deploy.py
"""

import subprocess
import sys
from pathlib import Path

from _harness import check, check_true, load_script, run
from fake_tools import calls, env, fake_tools, run_main

DEPLOY_ALL = load_script("deploy-all.py")
DEPLOY_S3 = load_script("deploy-s3.py")

FAKE_AWS = """\
if sys.argv[1:3] == ["sts", "get-caller-identity"]:
    print('{"Arn": "arn:aws:iam::1:user/test"}')
    sys.exit(0)
sys.exit(int(os.environ.get("FAKE_RC", "0")))
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _project(work: Path, pages: bool = True) -> Path:
    """A repo on main, pushed to a bare remote, with a gh-pages branch when PAGES."""
    remote, repo = work / "remote.git", work / "site"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "index.html").write_text("v1\n")
    _git(repo, "add", "index.html")
    _git(repo, "commit", "-q", "-m", "v1")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "main")
    if pages:
        _git(repo, "branch", "gh-pages")
        _git(repo, "push", "-q", "-u", "origin", "gh-pages")
    (repo / "index.html").write_text("v2\n")
    _git(repo, "commit", "-q", "-am", "v2")
    return repo


def test_refusals() -> None:
    with fake_tools(keep_path=True, aws=FAKE_AWS) as (w, log):
        repo = _project(w)
        (repo / "index.html").write_text("dirty\n")
        check("dirty tree", run_main(DEPLOY_ALL.main, str(repo), "b")[0], 1)
        _git(repo, "checkout", "-q", "--", "index.html")
        _git(repo, "checkout", "-q", "gh-pages")
        check("wrong branch", run_main(DEPLOY_ALL.main, str(repo), "b")[0], 1)
        check("nothing ran", calls(log), [])


def test_pages_and_s3() -> None:
    with fake_tools(keep_path=True, aws=FAKE_AWS) as (w, log):
        repo = _project(w)
        code, _ = run_main(DEPLOY_ALL.main, str(repo), "example.com")
        check("exit", code, 0)
        check("gh-pages pushed at main", _git(w / "remote.git", "rev-parse", "gh-pages"),
              _git(repo, "rev-parse", "main"))
        check("back on main", _git(repo, "branch", "--show-current"), "main")
        sync = [c for c in calls(log) if c[1:3] == ["s3", "sync"]]
        check_true("one sync to the bucket", len(sync) == 1 and "s3://example.com/" in sync[0])


def test_a_failed_push_ends_on_main() -> None:
    """The push fails (the remote is gone); the project must not be left on gh-pages."""
    with fake_tools(keep_path=True, aws=FAKE_AWS) as (w, _log):
        repo = _project(w)
        _git(repo, "remote", "set-url", "origin", str(w / "missing.git"))
        code, out = run_main(DEPLOY_ALL.main, str(repo), "b", "--skip-s3")
        check("exit", code, 1)
        check("back on main", _git(repo, "branch", "--show-current"), "main")
        check_true("says git push", "git push" in out)


def test_no_pages_branch_and_failed_s3() -> None:
    with fake_tools(keep_path=True, aws=FAKE_AWS) as (w, _log), env(FAKE_RC="1"):
        repo = _project(w, pages=False)
        code, out = run_main(DEPLOY_ALL.main, str(repo), "b")
        check("exit", code, 1)
        check_true("skipped pages, then S3 failed",
                   "No gh-pages branch" in out and "deploy-s3.py exited 1" in out)


def test_s3_arguments_and_missing_cli() -> None:
    args = DEPLOY_S3.sync_args(Path("/site"), "example.com", ["*.md"], True, True)
    check("sync", args, ["s3", "sync", "/site", "s3://example.com/", "--exclude", "*.md",
                         "--delete", "--dryrun"])
    with fake_tools() as (w, _log):
        check("no aws", run_main(DEPLOY_S3.main, str(w), "b")[0], 2)


if __name__ == "__main__":
    sys.exit(run(globals(), "deploy"))
