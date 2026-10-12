#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_lab_use_parts.py
The helpers under jpcheck/lab_use.py, each held to its own contract: what
it returns, not only whether a whole line ends up flagged. A whole-line
case cannot tell a helper's two answers apart when either one leads to the
same verdict; the next caller of the helper can.

    - lab_shell: a substitution's command and end; tokens; how many words
      an option takes; the words a wrapper and VAR= leave; an interpreter
      with an option missing its value.
    - lab_python: a callee's module.name, and "" for anything else; dotted
      names; the argument at a position; a method's parameters.
    - lab_use: which reader a file gets.

    python3 tests/test_lab_use_parts.py
"""

import ast
import sys
import tempfile
from pathlib import Path

from _harness import check, run
from jpcheck import lab_python, lab_shell, lab_use


def test_a_substitution_is_its_command_and_its_end() -> None:
    sub = lab_shell._substitution
    check("backticks", sub("`a`", 0), ("a", 3))
    check("  the first partner, not the last", sub("`a` `b`", 0), ("a", 3))
    check("  empty", sub("``x", 0), ("", 2))
    check("  unclosed: to the end", sub("`ab", 0), ("ab", 3))
    check("$(...) with text after it", sub("$(a) b", 0), ("a", 4))
    check("  nested parentheses", sub("$(a (b) c) d", 0), ("a (b) c", 10))
    check("  unclosed: to the end", sub("$(a", 0), ("a", 3))
    check("  opening part-way along the line", sub("x $(y)", 2), ("y", 6))


def test_tokens_split_only_on_whitespace_and_operators() -> None:
    check("a word with $ in it stays one word", lab_shell._tokens("a$b"), ["a$b"])
    check("an operator is its own word", lab_shell._tokens("a&&b"), ["a", "&&", "b"])


def test_how_many_words_an_option_takes() -> None:
    width = lab_shell._option_width
    check("one taking a value", width("-u", {"-u"}), 2)
    check("bundled, the last letter taking one", width("-Eu", {"-u"}), 2)
    check("bundled, none taking one", width("-E", {"-u"}), 1)
    check("a long option is never a bundle", width("--xu", {"-u"}), 1)
    check("not an option at all", width("x", {"-x"}), 1)


def test_what_a_wrapper_and_assignments_leave() -> None:
    skip = lab_shell._skip_prefix
    check("a wrapper with nothing after its option", skip(["sudo", "-E"]), [])
    check("VAR=value, its value holding '='", skip(["X=1", "OPTS=a=b", "cmd"]), ["cmd"])
    check("timeout's duration with a unit", skip(["timeout", "5m", "cmd"]), ["cmd"])
    check("a word that is not VAR=value", skip(["-o=x", "cmd"]), ["-o=x", "cmd"])


def test_an_interpreter_option_missing_its_value() -> None:
    runs = lab_shell.command_runs_lab
    for words in (["python3", "-c"], ["python3", "-m"], ["php", "-f"], ["bash", "-o"]):
        check(f"{' '.join(words)} and nothing after it runs nothing", runs(words), False)


def test_an_option_value_is_the_word_after_it() -> None:
    runs = lab_shell.command_runs_lab
    code = 'import runpy; runpy.run_path("tools/lab/x.py")'
    check("python3 -c CODE, then an argument", runs(["python3", "-c", code, "arg"]), True)
    check("python3 -m MODULE, then an argument", runs(["python3", "-m", "tools.lab.x", "arg"]), True)
    check("php -f SCRIPT, then an argument", runs(["php", "-f", "tools/lab/x.php", "arg"]), True)
    check("find with no -exec runs nothing", runs(["find", "tools/lab/", "-name", "x"]), False)


def test_substitutions_keep_the_rest_of_the_line() -> None:
    subs = lab_shell._substitutions
    check("an escape is kept, both characters", subs("ab\\cdef"), ("ab\\cdef", []))
    check("a single-quoted span is kept whole", subs("a 'b `c`' d"), ("a 'b `c`' d", []))
    check("a substitution leaves its place marked", subs("a $(b) c"), ("a ? c", ["b"]))


def _call(source: str) -> ast.Call:
    node = ast.parse(source).body[-1]
    assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    return node.value


def _scope(source: str) -> lab_python._Scope:
    return lab_python._Scope(ast.parse(source))


def test_a_callee_is_named_through_the_imports() -> None:
    scope = _scope("import os\nfrom importlib import util\n")
    q = scope.qualified
    check("an imported module's function", q(_call("os.system()").func), "os.system")
    check("a name imported from a module", q(_call("util.x()").func), "importlib.util.x")
    check("a builtin is itself", q(_call("exec()").func), "exec")
    check("a bare name nothing imported is ''", q(_call("foo()").func), "")
    check("an attribute of nothing imported is ''", q(_call("thing.run()").func), "")
    check("an attribute of a call's result is ''", q(_call("f().run()").func), "")


def test_dotted_names() -> None:
    expr = ast.parse("a.b.c; f().x; a").body
    nodes = [e.value for e in expr if isinstance(e, ast.Expr)]
    check("a chain of attributes", lab_python._dotted(nodes[0]), "a.b.c")
    check("an attribute of a call's result has none", lab_python._dotted(nodes[1]), None)
    check("a name", lab_python._dotted(nodes[2]), "a")


def test_the_argument_at_a_position() -> None:
    at = lab_python._positional
    second = at(_call("f(a, b)"), 1)
    check("the second of two", isinstance(second, ast.Name) and second.id, "b")
    check("past the last", at(_call("f(a)"), 1), None)
    check("a *args first: unknown", at(_call("f(*a)"), 0), None)
    first = at(_call("f(a, *b)"), 0)
    check("before a *args: known", isinstance(first, ast.Name) and first.id, "a")
    check("at a *args: unknown", at(_call("f(a, *b)"), 1), None)
    kw = lab_python._arg(_call("f(x=1)"), 0, "x")
    check("by keyword when not by position", isinstance(kw, ast.Constant) and kw.value, 1)


def test_a_methods_parameters() -> None:
    funcs = [n for n in ast.walk(ast.parse(
        "def f(a, b): pass\nclass T:\n    def m(self, p): pass\n    def c(cls, q, *, k): pass\n"))
        if isinstance(n, ast.FunctionDef)]
    check("each function's parameters a caller passes",
          [lab_python._params(f) for f in funcs], [["a", "b"], ["p"], ["q", "k"]])


def test_which_reader_a_file_gets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # "i": a #! line longer than the 200 bytes read names python past them.
        files = {"a.py": "", "b.sh": "", "c.yml": "", "d.md": "",
                 "e": "#!/usr/bin/env python3\n", "f": "#!/bin/sh\n",
                 "g": "#!/usr/bin/perl\n", "h": "no shebang\n",
                 "i": "#!/opt/" + "x" * 300 + "/python\n"}
        for name, body in files.items():
            (d / name).write_text(body, encoding="utf-8")
        got = {name: lab_use._reader(d / name) for name in files}
    py, sh = lab_python.python_runs, lab_shell.shell_runs
    check("by suffix, then by #! line; None for the rest", got,
          {"a.py": py, "b.sh": sh, "c.yml": sh, "d.md": None,
           "e": py, "f": sh, "g": None, "h": None, "i": None})


if __name__ == "__main__":
    sys.exit(run(globals(), "lab_use parts"))
