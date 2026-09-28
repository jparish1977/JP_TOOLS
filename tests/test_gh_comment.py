#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_gh_comment.py
gh-comment.py against the shipped templates and a fake gh: filling
placeholders, refusing to post an unfilled one, and the gh calls it makes.

    python tests/test_gh_comment.py
"""

import sys

from _harness import check, check_true, load_script, run
from fake_tools import calls, env, fake_tools, run_main

GH = load_script("gh-comment.py")

FAKE_GH = """\
print("https://example.com/comment/1")
sys.exit(int(os.environ.get("FAKE_RC", "0")))
"""


def test_fill_is_one_pass() -> None:
    body, unfilled = GH.fill("{a} and {b} and {c}", {"a": "{b}", "b": "B"})
    check("a value with braces is inserted as written", body, "{b} and B and {c}")
    check("unfilled", unfilled, ["c"])


def test_template_lookup() -> None:
    check("exact", GH.find_template("spec").stem, "spec")
    check("partial", GH.find_template("closing").stem, "issue-closing")
    try:
        GH.find_template("nope")
    except GH.CommentError:
        # reason: the refusal is the result under test
        return
    check_true("an unknown template was accepted", False)


def test_list_names_every_template() -> None:
    code, out = run_main(GH.main, "--list")
    check("exit", code, 0)
    check_true("spec listed", "spec" in out and "superseded" in out)


def test_unfilled_is_previewed_but_never_posted() -> None:
    with fake_tools(gh=FAKE_GH) as (_w, log):
        code, out = run_main(GH.main, "--template", "superseded", "--dry-run")
        check("preview", code, 0)
        check_true("warns", "unfilled placeholders" in out)
        code, out = run_main(GH.main, "--repo", "o/r", "--issue", "4", "--template", "superseded")
        check("refused", code, 1)
        check("nothing posted", calls(log), [])


FILLED = ("--template", "superseded", "--var", "issue=15", "--var", "reason=R")


def test_the_usage_examples_fill_their_templates() -> None:
    """Every `--var` in the docstring's examples names a placeholder its template has."""
    for name, given in (("closing", {"pr", "summary", "files", "verify"}),
                        ("superseded", {"issue", "reason"})):
        text = GH.find_template(name).read_text(encoding="utf-8")
        check(f"{name} placeholders", set(GH.PLACEHOLDER.findall(text)), given)
        for var in given:
            check_true(f"{name}: --var {var}= in the docstring", f"--var {var}=" in (GH.__doc__ or ""))


def test_comment_then_close() -> None:
    with fake_tools(gh=FAKE_GH) as (_w, log):
        code, _ = run_main(GH.main, "--repo", "o/r", "--issue", "4", *FILLED, "--close")
        check("exit", code, 0)
        check("calls", [c[1:4] for c in calls(log)],
              [["issue", "comment", "4"], ["issue", "close", "4"]])
        with env(FAKE_RC="1"):
            code, out = run_main(GH.main, "--repo", "o/r", "--create", "T", *FILLED)
        check("gh failing is exit 1", code, 1)
        check_true("because gh failed", "gh issue create failed" in out)


if __name__ == "__main__":
    sys.exit(run(globals(), "gh-comment"))
