"""Which Python code runs or loads a lab path (jpcheck/lab_use.py).

The question is the shell side's: is the PROGRAM a lab path, or the script
an interpreter is given, not is a lab path anywhere in the call. So a call
counts when it is:

- a runner, matched on its module through the file's imports
  (subprocess.*, os.system/popen/exec*/spawn*/posix_spawn*,
  asyncio.create_subprocess_*), whose argv[0], interpreter script or
  executable= is a lab path; a string given to a shell (os.system,
  shell=True, getoutput, ...) is read as a shell line;
- a loader: runpy, importlib's import_module and spec_from_file_location,
  exec or compile of a lab file, the suites' load_script, `import
  tools.lab...`, or a lab directory put on sys.path;
- a function defined in the same file, with a lab path in a parameter that
  reaches one of those as the program (to a fixed point, so a helper of a
  helper counts).

A lab path is a string holding one, "tools" then "lab" as path parts
(Path(...) / "tools" / "lab" / "x.py"), or a name or attribute assigned
from one through any number of assignments in the file. Not seen: a path
from data the file does not hold (a file, an argument, the environment), a
PYTHONPATH naming the lab, and a runner reached through a variable holding
the function.
"""

import ast
import functools
from collections.abc import Callable
from itertools import pairwise, takewhile

from jpcheck.lab_shell import MARK, UNKNOWN, command_runs_lab, is_lab, is_lab_module, shell_runs

_BUILTINS = {"exec", "compile", "__import__"}


def _strings(node: ast.AST) -> list[str]:
    """The string constants under NODE in source order. ast.walk is breadth
    first, which gives Path / "tools" / "lab" / "x" as x, lab, tools."""
    found = [(n.lineno, n.col_offset, n.value) for n in ast.walk(node)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return [value for _, _, value in sorted(found)]


def _names_lab_path(node: ast.AST) -> bool:
    parts = _strings(node)
    if any(is_lab(p) for p in parts):
        return True
    return any(a == "tools" and b == "lab" for a, b in pairwise(parts))


def _dotted(node: ast.AST) -> str | None:
    """x, x.y.z for a Name or a chain of attributes on one; else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else None
    return None


def _assignments(tree: ast.AST) -> list[tuple[ast.expr, ast.expr]]:
    pairs: list[tuple[ast.expr, ast.expr]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            pairs += [(t, n.value) for t in n.targets]
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            pairs.append((n.target, n.value))
    return pairs


class _Scope:
    """What one file binds: imported modules and names, and the names and
    attributes that hold a lab path (as text, or as an argv list)."""

    def __init__(self, tree: ast.AST) -> None:
        self.modules: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.held: dict[str, str | list[str]] = {}
        self._imports(tree)
        self._holders(_assignments(tree))

    def _imports(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    head = a.name.split(".")[0]
                    self.modules[a.asname or head] = a.name if a.asname else head
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for a in node.names:
                    self.names[a.asname or a.name] = f"{node.module}.{a.name}"

    def _holders(self, assigns: list[tuple[ast.expr, ast.expr]]) -> None:
        # To a fixed point of WHICH names hold one: LAB = "tools/lab"; s = LAB +
        # "/x.py" holds one too, wherever s is assigned. The names only grow,
        # from a finite set, so this ends; the texts are re-read each round
        # and do not decide it, because names that feed each other (a = b +
        # "/x"; ...; b = a) grow their text every round and never settle.
        while True:
            before = set(self.held)
            for target, value in assigns:
                key = _dotted(target)
                if key and (_names_lab_path(value) or self.mentions(value)):
                    self.held[key] = (self.words(value) if isinstance(value, (ast.List, ast.Tuple))
                                      else self.text(value))
            if set(self.held) == before:
                return

    def with_held(self, name: str) -> "_Scope":
        """This scope with NAME holding a lab path, for asking what a
        function does with a parameter."""
        trial = _Scope.__new__(_Scope)
        trial.modules, trial.names = self.modules, self.names
        trial.held = {**self.held, name: MARK}
        return trial

    def qualified(self, func: ast.AST) -> str:
        """module.name for a callee, through this file's imports; a bare
        builtin as itself; anything else (a method, mock.call) as ""."""
        if isinstance(func, ast.Name):
            if func.id in self.names:
                return self.names[func.id]
            return func.id if func.id in _BUILTINS else ""
        dotted = _dotted(func)
        if dotted is None:
            return ""
        head, _, rest = dotted.partition(".")  # an attribute: rest is never empty
        bound = self.modules.get(head) or self.names.get(head)
        return f"{bound}.{rest}" if bound else ""

    def mentions(self, node: ast.AST) -> bool:
        return any(_dotted(n) in self.held for n in ast.walk(node)
                   if isinstance(n, (ast.Name, ast.Attribute)))

    def holding(self, node: ast.AST) -> str | list[str] | None:
        """What the name or attribute NODE holds, if it holds a lab path."""
        key = _dotted(node)
        return None if key is None else self.held.get(key)

    def text(self, node: ast.AST) -> str:
        """What NODE would hold as a string, as far as this file says:
        literals, f-strings and + joined; a held name as its text; MARK for
        anything else built from a lab path; UNKNOWN otherwise."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(self.text(v.value if isinstance(v, ast.FormattedValue) else v)
                           for v in node.values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self.text(node.left) + self.text(node.right)
        held = self.holding(node)
        if isinstance(held, str):
            return held
        return MARK if held is not None or _names_lab_path(node) or self.mentions(node) else UNKNOWN

    def words(self, node: ast.AST) -> list[str]:
        """An argv: a list or tuple word by word (a *NAME holding a list
        spread into it), a held list as held, any other value as one word."""
        if isinstance(node, (ast.List, ast.Tuple)):
            out: list[str] = []
            for e in node.elts:
                spread = self.holding(e.value) if isinstance(e, ast.Starred) else None
                if isinstance(spread, list):
                    out += spread
                else:
                    out.append(UNKNOWN if isinstance(e, ast.Starred) else self.text(e))
            return out
        held = self.holding(node)
        if isinstance(held, list):
            return held
        return [self.text(node)]


def _kw(call: ast.Call, keyword: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == keyword), None)


def _positional(call: ast.Call, index: int) -> ast.expr | None:
    """Argument INDEX of CALL; None when there is none there, or a *args
    comes at or before it, so its position cannot be known."""
    upto = call.args[:index + 1]
    if len(upto) <= index or any(isinstance(a, ast.Starred) for a in upto):
        return None
    return call.args[index]


def _arg(call: ast.Call, index: int, keyword: str) -> ast.expr | None:
    """Argument INDEX of CALL, or else its KEYWORD."""
    found = _positional(call, index)
    return found if found is not None else _kw(call, keyword)


def _argv_runner(call: ast.Call, scope: _Scope) -> bool:
    """subprocess.run and kin: argv, or a shell string under shell=True."""
    program = _arg(call, 0, "args")
    if program is None:
        return False
    executable = _kw(call, "executable")
    if executable is not None and is_lab(scope.text(executable)):
        return True
    shell = _kw(call, "shell")
    if isinstance(shell, ast.Constant) and shell.value:
        return bool(shell_runs(scope.words(program)[0]))
    return command_runs_lab(scope.words(program))


def _shell_runner(call: ast.Call, scope: _Scope) -> bool:
    """os.system(command), os.popen(cmd), subprocess.getoutput(cmd), ..."""
    command = _positional(call, 0) or _kw(call, "cmd") or _kw(call, "command")
    return command is not None and bool(shell_runs(scope.text(command)))


def _vararg_runner(call: ast.Call, scope: _Scope) -> bool:
    """asyncio.create_subprocess_exec(program, *args)."""
    return command_runs_lab([scope.text(a) for a in call.args])


def _exec_runs(call: ast.Call, scope: _Scope, at: int, argv_at: int | None) -> bool:
    """os.exec*/spawn*/posix_spawn*: the program is argument AT; argv is the
    list at ARGV_AT, or, when None, the arguments after AT (argv[0] first)."""
    path = _positional(call, at)
    if path is None:
        return False
    if argv_at is None:
        rest = [scope.text(a) for a in call.args[at + 2:]]  # past the path and argv[0]
    else:
        argv = _positional(call, argv_at)
        rest = scope.words(argv)[1:] if argv is not None else []
    return command_runs_lab([scope.text(path), *rest])


def _exec_runner(at: int, argv_at: int | None) -> Callable[[ast.Call, _Scope], bool]:
    # A partial over a module-level function, not a closure: the table below
    # is built at import, and a closure made then would be the only code a
    # mutation test could never switch.
    return functools.partial(_exec_runs, at=at, argv_at=argv_at)


def _file_loader(call: ast.Call, scope: _Scope) -> bool:
    return any(is_lab(scope.text(a)) for a in [*call.args, *(k.value for k in call.keywords)])


def _module_loader(call: ast.Call, scope: _Scope) -> bool:
    module = _arg(call, 0, "name")
    return module is not None and is_lab_module(scope.text(module))


def _sys_path(call: ast.Call, scope: _Scope) -> bool:
    return any(is_lab(w) for a in call.args for w in scope.words(a))


# Library callables, by "module.name" once the file's imports resolve them.
_LIBRARY: dict[str, Callable[[ast.Call, _Scope], bool]] = {
    **dict.fromkeys((f"subprocess.{n}" for n in ("run", "call", "check_call", "check_output", "Popen")),
                    _argv_runner),
    **dict.fromkeys(("os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput",
                     "asyncio.create_subprocess_shell"), _shell_runner),
    "asyncio.create_subprocess_exec": _vararg_runner,
    **{f"os.{n}": _exec_runner(0, 1) for n in ("execv", "execve", "execvp", "execvpe",
                                               "posix_spawn", "posix_spawnp")},
    **{f"os.{n}": _exec_runner(0, None) for n in ("execl", "execle", "execlp", "execlpe")},
    **{f"os.{n}": _exec_runner(1, 2) for n in ("spawnv", "spawnve", "spawnvp", "spawnvpe")},
    **{f"os.{n}": _exec_runner(1, None) for n in ("spawnl", "spawnle", "spawnlp", "spawnlpe")},
    **dict.fromkeys(("runpy.run_path", "importlib.util.spec_from_file_location",
                     "importlib.machinery.SourceFileLoader", "exec", "compile",
                     "site.addsitedir"), _file_loader),
    **dict.fromkeys(("importlib.import_module", "runpy.run_module", "__import__"), _module_loader),
    **dict.fromkeys(("sys.path.insert", "sys.path.append", "sys.path.extend"), _sys_path),
}


def _by_library(call: ast.Call, scope: _Scope) -> bool:
    name = scope.qualified(call.func)
    judge = _LIBRARY.get(name)
    if judge is None and name.endswith(".load_script"):  # the suites' helper, imported from _harness
        judge = _file_loader
    return judge is not None and judge(call, scope)


def _params(f: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """F's parameters a caller passes positionally or by name, without the
    self or cls a method is called through."""
    names = [a.arg for a in [*f.args.posonlyargs, *f.args.args]]
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    return names + [a.arg for a in f.args.kwonlyargs]


# A function defined in the file: its parameters in call order, and those
# of them that reach a runner as the program (or the file or module loaded).
_Runners = dict[str, tuple[list[str], set[str]]]


def _local_runners(tree: ast.AST, scope: _Scope) -> _Runners:
    """Each function defined here whose parameter reaches a runner as the
    program, to a fixed point, so a helper that wraps subprocess.run, or
    wraps such a helper, runs what it is given in that place, and only
    there: a helper running `git log -- PATH` runs nothing it is given."""
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    runners: _Runners = {}
    # Until a round adds no parameter: they come from a finite set, so it ends.
    while True:
        found = sum(len(programs) for _, programs in runners.values())
        for f in funcs:
            params = _params(f)
            known = runners.get(f.name, ([], set()))[1]
            for p in (p for p in params if p not in known):
                trial = scope.with_held(p)
                if any(_call_runs(c, trial, runners) for c in ast.walk(f) if isinstance(c, ast.Call)):
                    runners.setdefault(f.name, (params, set()))[1].add(p)
        if sum(len(programs) for _, programs in runners.values()) == found:
            return runners


def _call_runs(call: ast.Call, scope: _Scope, runners: _Runners) -> bool:
    if _by_library(call, scope):
        return True
    f = call.func
    if isinstance(f, ast.Name):
        local = f.id
    elif isinstance(f, ast.Attribute):
        local = f.attr
    else:
        return False  # a call through a subscript or a call: no name to match
    if local not in runners:
        return False
    params, programs = runners[local]
    given = [k.value for k in call.keywords if k.arg in programs]
    # Positions are known only up to the first *args; arguments past the
    # named parameters go to a *args of the helper's, never a program.
    for i, v in enumerate(takewhile(lambda a: not isinstance(a, ast.Starred), call.args)):
        if i < len(params) and params[i] in programs:
            given.append(v)
    return any(is_lab(scope.text(v)) for v in given)


def _imports_lab(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(is_lab_module(a.name) for a in node.names)
    if isinstance(node, ast.ImportFrom) and node.module and not node.level:
        return is_lab_module(node.module) or (
            node.module == "tools" and any(a.name == "lab" for a in node.names))
    return False


def python_runs(text: str) -> list[int]:
    """Line numbers where Python code runs or loads a lab path."""
    try:
        tree = ast.parse(text)
    except SyntaxError:  # reason: a file that does not parse runs nothing this can see; check.py reports it
        return []
    scope = _Scope(tree)
    runners = _local_runners(tree, scope)
    lines = {node.lineno for node in ast.walk(tree)
             if isinstance(node, (ast.Import, ast.ImportFrom, ast.Call))
             and (_imports_lab(node) or (isinstance(node, ast.Call) and _call_runs(node, scope, runners)))}
    return sorted(lines)
