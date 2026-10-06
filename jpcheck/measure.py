"""What the smells arm measures: each function's length and complexity, and the file's.

Code outside every function is the file's MAIN: it runs, it branches, and it
is measured as one more function, named TOP_LEVEL, under the same limits.

Complexity is the standard tool's for each language, never a count of our own:
ruff's C901 (mccabe) for Python. None of those tools measures code outside a
function, so the top-level statements are copied into a synthetic function
appended to the source, and the tool measures that. Lengths are physical lines
from the language's own parser.

A measure is (physical lines, {name: (lines, complexity, first line)}). A
nested function's name is its parent's plus a dot, and the tools count a
nested function into its parent as well as on its own, so a file's total
(file_complexity) leaves nested functions out.
"""

import ast
import csv
import json
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from jpcheck.common import _php_bin, _php_cmd

Measure = tuple[int, dict[str, tuple[int, int, int]]]
Funcs = dict[str, tuple[int, int, int]]

TOP_LEVEL = "(top level)"
_SYNTHETIC = "__jp_top_level__"


class ToolMissingError(Exception):
    """The standard tool a measure needs is not installed; its name is the message."""


def measure(lang: str, src: str) -> Measure:
    """SRC's measure as a file of LANG. ValueError when it does not parse."""
    return _MEASURES[lang](src)


# Whether the language's tool counts a nested function into its parent too
# (mccabe and phpcs do; ESLint's complexity rule does not).
_NESTED_IN_PARENT = {"python": True, "php": True, "js": False, "shell": False, "cpp": False}


def file_complexity(funcs: dict[str, tuple[int, int, int]], lang: str = "python") -> int:
    """The file's complexity: its top level and its functions, each counted once.
    Where the tool already counts a nested function into its parent, the nested
    one is left out (a method's parent is its class, which is not measured)."""
    nested_counted = _NESTED_IN_PARENT.get(lang, False)
    return sum(cx for name, (_, cx, _) in funcs.items()
               if not (nested_counted and "." in name and name.rsplit(".", 1)[0] in funcs))


def _ruff_complexity(src: str) -> dict[int, int]:
    """{first line of each function: its mccabe complexity}, from ruff's C901."""
    if not shutil.which("ruff"):
        raise ToolMissingError("ruff (pip install -r requirements-dev.txt)")
    r = subprocess.run(["ruff", "check", "--isolated", "--select", "C901",
                        "--config", "lint.mccabe.max-complexity=0",
                        "--output-format", "json", "--stdin-filename", "measured.py", "-"],
                       input=src, capture_output=True, text=True, check=False)
    if r.returncode not in (0, 1):
        raise ValueError(f"ruff exit {r.returncode}: {r.stderr.strip()[:200]}")
    out: dict[int, int] = {}
    for f in json.loads(r.stdout or "[]"):
        # "`name` is too complex (N > 0)"
        n = int(f["message"].rsplit("(", 1)[1].split(" ", 1)[0])
        out[f["location"]["row"]] = n
    return out


def _python_top_level(tree: ast.Module) -> list[ast.stmt]:
    """The statements that run at import: everything but definitions, imports
    and the module docstring."""
    skip = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return [s for s in body if not isinstance(s, skip)]


def measure_python(src: str) -> Measure:
    tree = ast.parse(src)
    lines = src.splitlines()
    top = _python_top_level(tree)
    synthetic_line = 0
    measured = src
    if top:
        body = "\n".join("\n".join(lines[s.lineno - 1:s.end_lineno]) for s in top)
        head = src.rstrip("\n")
        synthetic_line = len(head.splitlines()) + 3
        measured = head + f"\n\n\ndef {_SYNTHETIC}():\n" + textwrap.indent(body, "    ") + "\n"
    cx = _ruff_complexity(measured)
    funcs: dict[str, tuple[int, int, int]] = {}
    stack: list[tuple[ast.AST, str]] = [(tree, "")]
    while stack:
        node, prefix = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                size = (child.end_lineno or child.lineno) - child.lineno + 1
                funcs[name] = (size, cx.get(child.lineno, 1), child.lineno)
                stack.append((child, name + "."))
            elif isinstance(child, ast.ClassDef):
                stack.append((child, prefix + child.name + "."))
            else:
                stack.append((child, prefix))
    _add_top_level(funcs, [(s.lineno, s.end_lineno or s.lineno) for s in top],
                   cx.get(synthetic_line, 1))
    return len(lines), funcs


def _add_top_level(funcs: Funcs, spans: list[tuple[int, int]], complexity: int) -> None:
    """Record the top level, when there is any: its statements' lines, the
    synthetic function's complexity, and where its first statement is."""
    if spans:
        funcs[TOP_LEVEL] = (sum(end - start + 1 for start, end in spans), complexity, spans[0][0])


def _lines_of(src: str, spans: list[tuple[int, int]]) -> str:
    lines = src.splitlines()
    return "\n".join("\n".join(lines[start - 1:end]) for start, end in spans)


# ── PHP: php-parser for the structure, phpcs's Generic.Metrics for complexity ──

# Top-level statements that declare rather than run.
_PHP_DECLARING = {"Stmt_Declare", "Stmt_Use", "Stmt_GroupUse", "Stmt_Function", "Stmt_Class",
                  "Stmt_Interface", "Stmt_Trait", "Stmt_Enum", "Stmt_Const", "Stmt_Nop",
                  "Stmt_InlineHTML", "Stmt_HaltCompiler"}
_PHP_TYPES = ("Stmt_Class", "Stmt_Interface", "Stmt_Trait", "Stmt_Enum")

# Every function reported: complexity above 0 warns, and nothing reaches error.
_PHPCS_RULESET = """<?xml version="1.0"?>
<ruleset name="jp-measure">
  <rule ref="Generic.Metrics.CyclomaticComplexity">
    <properties>
      <property name="complexity" value="0"/>
      <property name="absoluteComplexity" value="1000000"/>
    </properties>
  </rule>
</ruleset>
"""


def _php_tool(name: str) -> list[str]:
    """The command that runs Composer bin NAME: php, then the bin's proxy, as
    the lint runners call it. The proxy is a PHP script with no extension,
    which Windows cannot execute on its own (WinError 193)."""
    php, found = _php_cmd(), _php_bin(name)
    if not php:
        raise ToolMissingError("php")
    if not found:
        raise ToolMissingError(f"{name} (composer install in JP_TOOLS)")
    return [php, found]


def _php_ast(src: str) -> list[dict[str, Any]]:
    """php-parser's tree for SRC, as JSON. ValueError when it does not parse."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "measured.php"
        path.write_text(src, encoding="utf-8")
        r = subprocess.run([*_php_tool("php-parse"), "-j", str(path)],
                           capture_output=True, text=True, check=False)
    start = r.stdout.find("[")
    if r.returncode != 0 or "Syntax error" in r.stdout or start < 0:
        raise ValueError(f"php-parse: {(r.stdout + r.stderr).strip()[-200:]}")
    tree: list[dict[str, Any]] = json.loads(r.stdout[start:])
    return tree


def _phpcs_complexity(src: str) -> dict[int, int]:
    """{line of each `function` keyword: its complexity}, from phpcs."""
    with tempfile.TemporaryDirectory() as d:
        ruleset = Path(d) / "ruleset.xml"
        ruleset.write_text(_PHPCS_RULESET, encoding="utf-8")
        r = subprocess.run([*_php_tool("phpcs"), f"--standard={ruleset}", "--report=json", "-q",
                            "--stdin-path=measured.php", "-"],
                           input=src, capture_output=True, text=True, check=False)
    if r.returncode not in (0, 1, 2):
        raise ValueError(f"phpcs exit {r.returncode}: {r.stderr.strip()[:200]}")
    out: dict[int, int] = {}
    for data in json.loads(r.stdout or "{}").get("files", {}).values():
        for m in data.get("messages", []):
            found = re.search(r"\((\d+)\)", m["message"])
            if found:
                out[m["line"]] = int(found.group(1))
    return out


def _php_walk(nodes: list[dict[str, Any]], prefix: str, spans: dict[str, tuple[int, int]],
              top: list[tuple[int, int]] | None) -> None:
    """Functions and methods into SPANS; into TOP (when given) the statements that run."""
    for n in nodes:
        kind = n.get("nodeType", "")
        a = n.get("attributes", {})
        where = (a.get("startLine", 0), a.get("endLine", 0))
        name = (n.get("name") or {}).get("name", "") if isinstance(n.get("name"), dict) else ""
        if kind == "Stmt_Namespace":
            _php_walk(n.get("stmts") or [], prefix, spans, top)
        elif kind in ("Stmt_Function", "Stmt_ClassMethod"):
            spans[prefix + name] = where
            _php_walk(n.get("stmts") or [], prefix + name + ".", spans, None)
        elif kind in _PHP_TYPES:
            _php_walk(n.get("stmts") or [], prefix + name + ".", spans, None)
        elif top is not None and kind not in _PHP_DECLARING:
            top.append(where)


def measure_php(src: str) -> Measure:
    spans: dict[str, tuple[int, int]] = {}
    top: list[tuple[int, int]] = []
    tree = _php_ast(src)
    _php_walk(tree, "", spans, top)
    head = src.rstrip()
    measured, synthetic_line = src, 0
    if top:
        # Back into PHP mode first when the file ends outside it.
        reopen = "<?php\n" if head.endswith("?>") or \
            (tree and tree[-1].get("nodeType") == "Stmt_InlineHTML") else ""
        prefix = head + "\n\n" + reopen
        synthetic_line = len(prefix.splitlines()) + 1
        measured = prefix + f"function {_SYNTHETIC}()\n{{\n{_lines_of(src, top)}\n}}\n"
    cx = _phpcs_complexity(measured)
    funcs: Funcs = {}
    for name, (start, end) in spans.items():
        # phpcs reports on the `function` keyword, which can follow attributes
        # php-parser counts into the span: the first report inside the span.
        inside = [line for line in cx if start <= line <= end]
        funcs[name] = (end - start + 1, cx[min(inside)] if inside else 1, start)
    _add_top_level(funcs, top, cx.get(synthetic_line, 1))
    return len(src.splitlines()), funcs


# ── JS: ESLint's parser and its `complexity` rule, in jp_measure.mjs ──

def measure_js(src: str) -> Measure:
    from jpcheck.common import TOOLS_ROOT
    from jpcheck.js_tools import _resolve_args, _runner_env
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        raise ToolMissingError("node (on PATH, to run jp_measure.mjs)")
    r = subprocess.run([node, *_resolve_args(), str(TOOLS_ROOT / "jp_measure.mjs")], input=src,
                       capture_output=True, text=True, check=False, cwd=str(TOOLS_ROOT),
                       env=_runner_env())
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        # reason: no JSON means the runner itself failed (eslint not installed, a crash)
        raise ToolMissingError(f"eslint (jp_measure.mjs: {r.stderr.strip()[-200:]})") from None
    if "error" in doc:
        raise ValueError(doc["error"])
    return doc["lines"], {name: (size, cx, line) for name, (size, cx, line) in doc["funcs"].items()}


# ── Shell: shfmt's parser, and OUR count, because no shell tool measures complexity ──
# McCabe as the other tools count it: 1, plus each if and elif, loop, case
# branch, and && or || between commands. A nested function is its own.

def _sh_branches(node: Any, nested: list[dict[str, Any]]) -> int:
    """Decision points under NODE; a function defined inside goes to NESTED instead."""
    if isinstance(node, list):
        return sum(_sh_branches(n, nested) for n in node)
    if not isinstance(node, dict):
        return 0
    kind = node.get("Type")
    if kind == "FuncDecl":
        nested.append(node)
        return 0
    n = {"IfClause": 1, "WhileClause": 1, "ForClause": 1}.get(kind or "", 0)
    if kind == "IfClause":
        branch = node.get("Else")
        while branch:
            n += 1 if branch.get("Cond") else 0
            branch = branch.get("Else")
    elif kind == "CaseClause":
        n += len(node.get("Items") or [])
    elif kind == "BinaryCmd" and node.get("Op") in ("&&", "||"):
        n += 1
    return n + sum(_sh_branches(v, nested) for k, v in node.items() if k not in ("Pos", "End"))


def _sh_function(node: dict[str, Any], prefix: str, funcs: Funcs) -> None:
    name = prefix + node["Name"]["Value"]
    inner: list[dict[str, Any]] = []
    start, end = node["Pos"]["Line"], node["End"]["Line"]
    funcs[name] = (end - start + 1, 1 + _sh_branches(node.get("Body"), inner), start)
    for fn in inner:
        _sh_function(fn, name + ".", funcs)


def measure_shell(src: str) -> Measure:
    shfmt = shutil.which("shfmt")
    if not shfmt:
        raise ToolMissingError("shfmt (pip install -r requirements-dev.txt)")
    r = subprocess.run([shfmt, "--to-json"], input=src, capture_output=True, text=True,
                       check=False)
    if r.returncode != 0:
        raise ValueError(f"shfmt: {r.stderr.strip()[:200]}")
    funcs: Funcs = {}
    top: list[tuple[int, int]] = []
    top_cx = 1
    for stmt in json.loads(r.stdout).get("Stmts") or []:
        cmd = stmt.get("Cmd") or {}
        if cmd.get("Type") == "FuncDecl":
            _sh_function(cmd, "", funcs)
            continue
        inner: list[dict[str, Any]] = []
        top_cx += _sh_branches(stmt, inner)
        for fn in inner:
            _sh_function(fn, "", funcs)
        top.append((stmt["Pos"]["Line"], stmt["End"]["Line"]))
    _add_top_level(funcs, top, top_cx)
    return len(src.splitlines()), funcs


# ── C and C++: lizard ──

def measure_cpp(src: str) -> Measure:
    lizard = shutil.which("lizard")
    if not lizard:
        raise ToolMissingError("lizard (pip install -r requirements-dev.txt)")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "measured.cpp"
        path.write_text(src, encoding="utf-8")
        r = subprocess.run([lizard, "--csv", str(path)], capture_output=True, text=True,
                           check=False)
    if r.returncode != 0:
        raise ValueError(f"lizard exit {r.returncode}: {r.stderr.strip()[:200]}")
    funcs: Funcs = {}
    # NLOC,CCN,token,PARAM,length,location,file,function,long_name,start,end
    for row in csv.reader(r.stdout.splitlines()):
        if len(row) >= 11 and row[1].isdigit():
            start, end = int(row[9]), int(row[10])
            funcs[row[7]] = (end - start + 1, int(row[1]), start)
    return len(src.splitlines()), funcs


_MEASURES = {"python": measure_python, "php": measure_php, "js": measure_js,
             "shell": measure_shell, "cpp": measure_cpp}

# How a function of each language is defined, for finding one that moved to
# another file at the base: (a git grep -E pattern for the name, file globs).
DEFINITION = {"python": (r"def {}\(", ("*.py",)),
              "php": (r"function {}\(", ("*.php",)),
              "js": (r"(function {0}\b|{0} *[=:] *(async *)?(function|\())",
                     ("*.js", "*.mjs", "*.cjs", "*.jsx")),
              "shell": (r"(function +)?{}\b *(\( *\))? *\{{", ("*.sh", "*.bash")),
              "cpp": (r"\b{}\b *\(", ("*.c", "*.cc", "*.cpp", "*.h", "*.hpp", "*.ino"))}
