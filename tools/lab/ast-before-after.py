#!/usr/bin/env python3
"""What did a cleanup change in a repository's code: its drawings and its findings.

ANSWERS: between two refs of one git repository, which Python functions
  changed structure (by AST, so whitespace and comments do not count), what
  each looked like before and after as a code figure (projectbook's
  vm/code-figure.py, the same renderer the covers use), each one's node count,
  depth and McCabe complexity on both sides, and how many check.py findings
  (ruff and smells) each changed file carried before and after, by rule.
  Joe, 2026-09-15: "it hink it will be really interesting to see the
  difference in the ast drawing of the code before and after this cleanup
  effort", then "befor/after on both would be best" (drawings and
  patterns/antipatterns).

TESTED AGAINST: dynatext-tools b2de031^..d4a4ee3 and projectbook
  77e90b4..main, 2026-09-15. Positive control: dtlibidx.ibase, changed by a
  known one-line fix in that range, is reported changed; a function the range
  never touched is not reported at all.

NOT TESTED AGAINST: functions renamed or moved between classes (they read as
  one removed and one added); files renamed (the diff is by path). Findings
  are counted on copies of each side in a scratch directory, so rules that
  depend on the file's mode or location are left out: EXE001/EXE002 read the
  exec bit the copy does not keep. mypy is not run, since a lone copy has no
  project to type-check against. Complexity is ruff's C901 at threshold 0,
  not a second implementation.

usage: ast-before-after.py REPO BEFORE AFTER --out DATA.json
         [--skip-dir tests --skip-dir checks] [--figure-tool PATH]
"""

import argparse
import ast
import collections
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
from typing import Any

CHECK = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "check.py")
MODE_RULES = {"EXE001", "EXE002"}


def git(repo: str, *args: str) -> str | None:
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False)
    return p.stdout if p.returncode == 0 else None


def load_figure(path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location("code_figure", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"ast-before-after: cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def functions(src: str | None) -> dict[str, ast.AST]:
    """qualified name -> function node; a later duplicate gets #2, #3."""
    if src is None:
        return {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    out: dict[str, ast.AST] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    key, n = name, 2
                    while key in out:
                        key, n = f"{name}#{n}", n + 1
                    out[key] = child
                visit(child, name + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return out


def complexity(src: str | None, name: str) -> dict[str, int]:
    """function name -> McCabe complexity, from ruff's C901 at threshold 0."""
    ruff = shutil.which("ruff")
    if src is None or ruff is None:
        return {}
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        p = subprocess.run([ruff, "check", "--isolated", "--select", "C901", "--config",
                            "lint.mccabe.max-complexity = 0", "--output-format", "json", path],
                           capture_output=True, text=True, check=False)
    out: dict[str, int] = {}
    try:
        rows = json.loads(p.stdout or "[]")
    except ValueError:
        return out
    for row in rows:
        m = re.match(r"`([^`]+)` is too complex \((\d+) > 0\)", row.get("message", ""))
        if m:
            out[m.group(1)] = max(out.get(m.group(1), 0), int(m.group(2)))
    return out


def findings(src: str | None, name: str) -> dict[str, int]:
    """check.py rule -> count, over a copy of one side, mode-dependent rules left out."""
    if src is None:
        return {}
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        p = subprocess.run([sys.executable, CHECK, path, "--tools", "ruff,smells"],
                           capture_output=True, text=True, check=False, cwd=td)
    count: collections.Counter[str] = collections.Counter()
    for line in p.stdout.splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        for arm in doc.get("checks", []):
            for issue in arm.get("issues", []):
                rule = str(issue.get("rule", ""))
                if rule not in MODE_RULES:
                    count[f"{arm.get('tool')}:{rule}"] += 1
    return dict(count)


def figure(cf: ModuleType, node: ast.AST | None, title: str, sub: str) -> dict[str, Any] | None:
    if node is None:
        return None
    tree = cf._py_conv(node)
    segs: list[Any] = []
    cf.walk(tree, 0.0, 0.0, -90.0, 0, segs)
    body, n, depth = cf.svg(segs, title, sub)
    return {"svg": body, "nodes": n, "depth": depth}


def changed_functions(old: str | None, new: str | None, base: str,
                      cf: ModuleType) -> list[dict[str, Any]]:
    """Every function whose AST differs between the two sides, drawn on each."""
    fo, fn = functions(old), functions(new)
    co, cn = complexity(old, base), complexity(new, base)
    out = []
    for q in sorted(set(fo) | set(fn)):
        a, b = fo.get(q), fn.get(q)
        if a is not None and b is not None and ast.dump(a) == ast.dump(b):
            continue
        short = q.split(".")[-1].split("#")[0]
        out.append({
            "name": q,
            "status": "added" if a is None else "removed" if b is None else "changed",
            "before": figure(cf, a, f"{short}()", f"{base} before"),
            "after": figure(cf, b, f"{short}()", f"{base} after"),
            # A side the function is absent from has no complexity, even
            # when another function there shares its short name.
            "complexity": [co.get(short) if a is not None else None,
                           cn.get(short) if b is not None else None],
        })
    return out


def compare(repo: str, before: str, after: str, skip: list[str], cf: ModuleType) -> dict[str, Any]:
    names = (git(repo, "diff", "--name-only", before, after, "--", "*.py") or "").split()
    names = [n for n in names if not any(n == d or n.startswith(d.rstrip("/") + "/") for d in skip)]
    files = []
    for rel in names:
        old, new = git(repo, "show", f"{before}:{rel}"), git(repo, "show", f"{after}:{rel}")
        base = os.path.basename(rel)
        files.append({"path": rel, "added": old is None, "removed": new is None,
                      "findings": [findings(old, base), findings(new, base)],
                      "functions": changed_functions(old, new, base, cf)})
    return {"repo": os.path.basename(os.path.abspath(repo)), "before": before, "after": after,
            "before_sha": (git(repo, "rev-parse", "--short", before) or "").strip(),
            "after_sha": (git(repo, "rev-parse", "--short", after) or "").strip(),
            "files": files}


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-dir", action="append", default=[],
                    help="a top-level directory to leave out, e.g. tests; repeatable")
    ap.add_argument("--figure-tool", default=os.path.expanduser(
        "~/projects/projectbook/vm/code-figure.py"))
    a = ap.parse_args()
    if git(a.repo, "rev-parse", "--verify", a.before) is None or \
            git(a.repo, "rev-parse", "--verify", a.after) is None:
        sys.exit(f"ast-before-after: {a.before} or {a.after} is not a ref in {a.repo}")
    data = compare(a.repo, a.before, a.after, a.skip_dir, load_figure(a.figure_tool))
    # A .js --out is the same data as a script a page can load beside itself,
    # keyed by repository, since a sandboxed page may run scripts but not fetch.
    with open(a.out, "w", encoding="utf-8") as fh:
        if a.out.endswith(".js"):
            fh.write("window.AST_BEFORE_AFTER = window.AST_BEFORE_AFTER || {};\n"
                     f"window.AST_BEFORE_AFTER[{json.dumps(data['repo'])}] = ")
            json.dump(data, fh)
            fh.write(";\n")
        else:
            json.dump(data, fh)
    nfun = sum(len(f["functions"]) for f in data["files"])
    print(f"ast-before-after: {data['repo']} {data['before_sha']}..{data['after_sha']}: "
          f"{len(data['files'])} file(s), {nfun} function(s) changed -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
