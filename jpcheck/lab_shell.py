"""What a lab path is, and which shell commands run one (jpcheck/lab_use.py).

A shell line, or a line of a YAML file (a workflow's `run:`), runs a lab
path when a command in it does: one whose first word, after `VAR=value`,
control words (if, while, !, then, ...) and wrappers (env, nice, sudo,
xargs, ...) with their options and those options' values, is a lab path; an
interpreter (bash, sh, python3, source, ., ...) whose script argument is
one, or `python3 -m tools.lab.x`; `bash -c '...'` read as shell and
`python3 -c '...'` as Python; `find -exec`. Each `$(...)` and backtick
substitution outside single quotes is read as a command of its own, so one
inside double quotes counts too. A comment is not read.

Not seen: `env -S`, a heredoc body, and a command split over lines with `\\`.
"""

import shlex
from dataclasses import dataclass, field
from pathlib import Path

LAB = "tools/lab/"
LAB_MODULE = "tools.lab"
# What a lab path stands as once it has been built from parts that cannot
# be spelled out (os.path.join, Path / ..., a call): still a lab path.
MARK = "tools/lab/?"
UNKNOWN = "?"
LAB_DIR = "tools/lab"


def is_lab(text: str) -> bool:
    """TEXT is a lab path: holds "tools/lab/", or is the lab dir itself.
    (A trailing slash is the first case, so "tools/lab/" needs no strip.)"""
    return LAB in text or text.endswith(LAB_DIR)


def is_lab_module(name: str) -> bool:
    return name == LAB_MODULE or name.startswith(LAB_MODULE + ".")


# Words a command may start with that are not the command.
_CONTROL = {"if", "then", "elif", "else", "while", "until", "do", "!", "{"}
# Each wrapper and the options of it that take a value; its command follows.
_WRAPPERS: dict[str, set[str]] = {
    "env": {"-u", "--unset", "-C", "--chdir"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "--class", "-n", "--classdata", "-p", "-P", "-u"},
    "nohup": set(),
    "stdbuf": {"-i", "-o", "-e"},
    "time": {"-f", "--format", "-o", "--output"},
    "sudo": {"-u", "--user", "-g", "--group", "-C", "--close-from", "-D", "--chdir",
             "-h", "--host", "-p", "--prompt", "-r", "--role", "-t", "--type",
             "-T", "--command-timeout", "-U", "--other-user"},
    "doas": {"-u", "-C"},
    "command": set(),
    "exec": {"-a"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "xargs": {"-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "--replace", "-L",
              "--max-lines", "-n", "--max-args", "-P", "--max-procs", "-s", "--max-chars"},
}


@dataclass(frozen=True)
class _Interpreter:
    """Options whose value is the script; options whose value is code, and
    its language ("shell", "python", or "" for one not read); options that
    take some other value. The first word that is not an option is the
    script."""
    script: frozenset[str] = frozenset()
    code: dict[str, str] = field(default_factory=dict)
    value: frozenset[str] = frozenset()
    modules: bool = False  # -m names a module to run


_SHELL = _Interpreter(code={"-c": "shell"},
                      value=frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}))
_PYTHON = _Interpreter(code={"-c": "python"}, value=frozenset({"-W", "-X"}), modules=True)
_INTERPRETERS = {
    **dict.fromkeys(("bash", "sh", "zsh", "dash", "ksh"), _SHELL),
    "source": _Interpreter(),
    ".": _Interpreter(),
    "python": _PYTHON,
    "node": _Interpreter(script=frozenset({"-r", "--require", "--import"}),
                         code=dict.fromkeys(("-e", "--eval", "-p", "--print"), "")),
    "php": _Interpreter(script=frozenset({"-f"}), code={"-r": ""},
                        value=frozenset({"-c", "-d", "-z"})),
    "pwsh": _Interpreter(script=frozenset({"-File", "-f"}),
                         code=dict.fromkeys(("-Command", "-c"), "")),
}
_FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
_OPTION_MARKS = ("-", "+")  # bash's +o and +O are options too
_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", ";;", "|&"}


def _tokens(line: str) -> list[str] | None:
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:  # reason: an unbalanced quote (a heredoc body, a multi-line string) is not a command this can read
        return None


_BACKTICK = "`"
_DOLLAR_PAREN = "$("
_DEPTH = {"(": 1, ")": -1}
_QUOTES = ("'", '"')
_ESCAPED = 2  # a backslash and the character it escapes


def _substitution(line: str, i: int) -> tuple[str, int]:
    """(the command, the index just past it) of the substitution opening at
    I: up to a backtick's partner, or to the ")" closing "$(" (counting the
    parentheses inside it). An unclosed one runs to the end of the line."""
    if line.startswith(_BACKTICK, i):
        start = i + len(_BACKTICK)
        end = line.find(_BACKTICK, start)
        if end == -1:
            return line[start:], len(line)
        return line[start:end], end + len(_BACKTICK)
    start = i + len(_DOLLAR_PAREN)
    depth = 1
    for j in range(start, len(line)):
        depth += _DEPTH.get(line[j], 0)
        if depth == 0:
            return line[start:j], j + 1
    return line[start:], len(line)


def _is_comment(line: str, i: int) -> bool:
    """A "#" at I starts a comment: at the start of the line or after
    whitespace, not inside a word."""
    return line[i] == "#" and (i == 0 or line[i - 1].isspace())


def _substitutions(line: str) -> tuple[str, list[str]]:
    """LINE without its comment, each $(...) and backtick substitution
    outside single quotes replaced by UNKNOWN; and the commands those
    substitutions run, each to be read as a line of its own."""
    out, inner = [], []
    i, quote = 0, ""
    while i < len(line):
        c = line[i]
        if quote == "'":
            if c == "'":
                quote = ""
        elif c == "\\":
            out.append(line[i:i + _ESCAPED])
            i += _ESCAPED
            continue
        elif c in _QUOTES:
            quote = "" if quote == c else quote or c
        elif not quote and _is_comment(line, i):
            break
        elif line.startswith((_BACKTICK, _DOLLAR_PAREN), i):
            body, i = _substitution(line, i)
            inner.append(body)
            out.append(UNKNOWN)
            continue
        out.append(c)
        i += 1
    return "".join(out), inner


def _option_width(word: str, takes: set[str]) -> int:
    """How many words an option of a wrapper uses: two when it takes a
    value (sudo -u joe), bundled or not (sudo -Eu joe)."""
    bundled = word[:1] == "-" and word[1:2] != "-" and f"-{word[-1]}" in takes
    return 2 if word in takes or bundled else 1


def _skip_prefix(words: list[str]) -> list[str]:
    """WORDS from the command itself: no VAR=value, no control word, no
    wrapper with its options and their values."""
    i = 0
    while i < len(words):
        w = words[i]
        # VAR=value: an option (-o=x) is never an identifier, so no check for "-".
        if w in _CONTROL or ("=" in w and w.partition("=")[0].isidentifier()):
            i += 1
        elif Path(w).name in _WRAPPERS:
            takes = _WRAPPERS[Path(w).name]
            i += 1
            # Its options; a word starting with a digit is timeout's duration.
            while i < len(words) and (words[i].startswith("-") or words[i][:1].isdigit()):
                i += _option_width(words[i], takes)
        else:
            break
    return words[i:]


def _code_runs_lab(code: str, lang: str) -> bool:
    if lang == "shell":
        return bool(shell_runs(code))
    if lang == "python":
        # Here, not at the top: lab_python imports this module.
        from jpcheck.lab_python import python_runs
        return bool(python_runs(code))
    return False


def _interpreter_runs_lab(spec: _Interpreter, rest: list[str]) -> bool:
    """Whether an interpreter given the arguments REST runs a lab path."""
    i = 0
    while i < len(rest):
        w = rest[i]
        if not w.startswith(_OPTION_MARKS):
            return is_lab(w)
        takes_value = w in spec.code or w in spec.script or w in spec.value or (
            spec.modules and w == "-m")
        if takes_value and i + 1 == len(rest):
            return False  # an option missing its value: nothing runs
        if w in spec.code:
            return _code_runs_lab(rest[i + 1], spec.code[w])
        if spec.modules and w == "-m":
            return is_lab_module(rest[i + 1])
        if w in spec.script:
            return is_lab(rest[i + 1])
        i += 2 if takes_value else 1
    return False


def command_runs_lab(words: list[str]) -> bool:
    """Whether the one command WORDS (no separators) runs a lab path."""
    words = _skip_prefix(words)
    if not words:
        return False
    cmd = words[0]
    if is_lab(cmd):
        return True
    name = Path(cmd).name or cmd  # Path(".").name is ""
    if name == "find":
        at = next((i for i, w in enumerate(words) if w in _FIND_EXEC), None)
        return at is not None and command_runs_lab(words[at + 1:])
    spec = _PYTHON if name.startswith("python") else _INTERPRETERS.get(name)
    return spec is not None and _interpreter_runs_lab(spec, words[1:])


def _line_runs(line: str) -> bool:
    words = _tokens(line)
    if not words:
        return False
    cmd: list[str] = []
    for w in [*words, ";"]:
        if w in _SEPARATORS:
            if command_runs_lab(cmd):
                return True
            cmd = []
        else:
            cmd.append(w)
    return False


def shell_runs(text: str) -> list[int]:
    """Line numbers where a shell line (or a YAML line) runs a lab path."""
    lines = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        for prefix in ("- run:", "run:"):
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
        if LAB not in line and LAB_MODULE not in line:
            continue
        outer, inner = _substitutions(line)
        if any(shell_runs(body) for body in inner) or _line_runs(outer):
            lines.append(n)
    return lines
