"""Which tracked files outside tools/lab/ RUN a tools/lab/ file.

METHODOLOGY 2.9: a lab tool that grows a dependency is promoted out of the
lab. A dependency is code that executes the tool, not text that names it: a
comment or a doc citing a lab tool as how something was measured is a
mention and passes. So the question is asked where code runs things:

- Python: a call that runs a program or loads a file (subprocess, os.system,
  os.exec*, runpy.run_path, importlib's spec_from_file_location, the suites'
  load_script, or a function in the same file that hands a parameter to one
  of those) whose arguments name a lab path, directly or through a variable
  assigned from one in the same file.
- Shell, and each line of a YAML file (a workflow's `run:`): a command whose
  first word, after `VAR=value` and wrappers such as env and nice, is a lab
  path, or an interpreter (bash, sh, python3, source, ., exec, ...) whose
  script argument is one. `bash -c '...'` is read as the command it runs.

A lab path is "tools/lab/" in one string, or "tools" then "lab" as path parts
(Path(...) / "tools" / "lab" / "x.py"). Not seen: a path built at run time
from other variables, and JS or PHP launching a process.
"""

import ast
import shlex
import subprocess
from itertools import pairwise
from pathlib import Path

LAB = "tools/lab/"
_PY_RUNNERS = {
    "run", "call", "check_call", "check_output", "Popen", "system",
    "execv", "execve", "execvp", "execl", "execlp", "spawnv", "spawnl",
    "run_path", "spec_from_file_location", "load_script",
}
_INTERPRETERS = {"bash", "sh", "zsh", "dash", "python", "python3", "pwsh", "node", "php",
                 "source", ".", "exec"}
_WRAPPERS = {"env", "nice", "ionice", "nohup", "stdbuf", "time", "sudo", "command", "timeout"}
_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")"}
_SHELL_SUFFIXES = (".sh", ".bash", ".yml", ".yaml")


def _strings(node: ast.AST) -> list[str]:
    """The string constants under NODE in source order. ast.walk is breadth
    first, which gives Path / "tools" / "lab" / "x" as x, lab, tools."""
    found = [(n.lineno, n.col_offset, n.value) for n in ast.walk(node)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return [value for _, _, value in sorted(found)]


def _names_lab_path(node: ast.AST) -> bool:
    parts = _strings(node)
    if any(LAB in p or p.rstrip("/") == "tools/lab" for p in parts):
        return True
    return any(a == "tools" and b == "lab" for a, b in pairwise(parts))


def _callee(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def _mentions(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))


def _local_runners(tree: ast.AST) -> set[str]:
    """_PY_RUNNERS plus each function defined here that hands one of its own
    parameters to a runner, to a fixed point, so a helper that wraps
    subprocess.run (or wraps such a helper) counts as running what it is
    given."""
    runners = set(_PY_RUNNERS)
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    changed = True
    while changed:
        changed = False
        for f in funcs:
            if f.name in runners:
                continue
            params = {a.arg for a in [*f.args.posonlyargs, *f.args.args, *f.args.kwonlyargs]}
            for call in (n for n in ast.walk(f) if isinstance(n, ast.Call)):
                if _callee(call) in runners and any(
                        _mentions(a, params) for a in [*call.args, *(k.value for k in call.keywords)]):
                    runners.add(f.name)
                    changed = True
                    break
    return runners


def python_runs(text: str) -> list[int]:
    """Line numbers where Python code runs a lab path."""
    try:
        tree = ast.parse(text)
    except SyntaxError:  # reason: a file that does not parse runs nothing this can see; check.py reports it
        return []
    runners = _local_runners(tree)
    tainted: set[str] = set()
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else \
            [node.target] if isinstance(node, ast.AnnAssign) and node.value else []
        value = getattr(node, "value", None)
        if targets and value is not None and _names_lab_path(value):
            tainted.update(t.id for t in targets if isinstance(t, ast.Name))
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _callee(node) not in runners:
            continue
        args = [*node.args, *(k.value for k in node.keywords)]
        if any(_names_lab_path(a) or _mentions(a, tainted) for a in args):
            lines.append(node.lineno)
    return sorted(set(lines))


def _tokens(line: str) -> list[str] | None:
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:  # reason: an unbalanced quote (a heredoc body, a multi-line string) is not a command this can read
        return None


def _skip_prefix(words: list[str]) -> list[str]:
    """WORDS from the command itself: no VAR=value, no wrapper and its options."""
    i = 0
    while i < len(words):
        w = words[i]
        if "=" in w and not w.startswith("-") and w.split("=", 1)[0].isidentifier():
            i += 1
        elif w in _WRAPPERS:
            # Its options, and the numbers they take (nice -n 5, ionice -c 2,
            # timeout 30s): a wrapper's command never starts with a digit.
            i += 1
            while i < len(words) and (words[i].startswith("-") or words[i][:1].isdigit()):
                i += 1
        else:
            break
    return words[i:]


def _command_runs_lab(words: list[str]) -> bool:
    words = _skip_prefix(words)
    if not words:
        return False
    cmd = words[0]
    if LAB in cmd:
        return True
    if Path(cmd).name not in _INTERPRETERS:
        return False
    rest = words[1:]
    for i, w in enumerate(rest):
        if w == "-c" and i + 1 < len(rest):
            return shell_runs(rest[i + 1]) != []
        if not w.startswith("-"):
            return LAB in w
    return False


def shell_runs(text: str) -> list[int]:
    """Line numbers where a shell line (or a YAML line) runs a lab path."""
    lines = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        for prefix in ("- run:", "run:"):
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
        if LAB not in line:
            continue
        words = _tokens(line)
        if not words:
            continue
        cmd: list[str] = []
        for w in [*words, ";"]:
            if w in _SEPARATORS:
                if _command_runs_lab(cmd):
                    lines.append(n)
                    break
                cmd = []
            else:
                cmd.append(w)
    return lines


def _is_shell_script(path: Path) -> bool:
    if path.suffix in _SHELL_SUFFIXES:
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as fh:
            first = fh.readline(200)
    except OSError:  # reason: an unreadable file runs nothing this can see
        return False
    return first.startswith(b"#!") and any(s in first for s in (b"sh", b"bash"))


def lab_runs(root: Path) -> list[tuple[str, int]]:
    """(path, line) for each place a tracked file outside tools/lab/ runs a
    lab path, in path order."""
    tracked = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True,
                             text=True, check=True).stdout.splitlines()
    found = []
    for rel in tracked:
        if rel.startswith(LAB):
            continue
        path = root / rel
        if not path.is_file():
            continue
        if path.suffix == ".py":
            hits = python_runs(path.read_text(encoding="utf-8", errors="replace"))
        elif _is_shell_script(path):
            hits = shell_runs(path.read_text(encoding="utf-8", errors="replace"))
        else:
            continue
        found += [(rel, n) for n in hits]
    return found
