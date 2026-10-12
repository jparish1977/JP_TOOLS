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


def is_lab(text: str) -> bool:
    """TEXT is a lab path: holds "tools/lab/", or is the lab dir itself."""
    return LAB in text or text.rstrip("/").endswith("tools/lab")


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
_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", ";;", "|&"}


def _tokens(line: str) -> list[str] | None:
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:  # reason: an unbalanced quote (a heredoc body, a multi-line string) is not a command this can read
        return None


def _substitution_end(line: str, i: int) -> int:
    """Index just past the substitution starting at I: a backtick's partner,
    or the ")" closing "$(" (counting the parentheses inside it)."""
    if line[i] == "`":
        end = line.find("`", i + 1)
        return len(line) if end < 0 else end + 1
    depth = 0
    for j in range(i + 1, len(line)):
        depth += {"(": 1, ")": -1}.get(line[j], 0)
        if depth == 0:
            return j + 1
    return len(line)


def _substitutions(line: str) -> tuple[str, list[str]]:
    """LINE without its comment, each $(...) and backtick substitution
    outside single quotes replaced by UNKNOWN; and the commands those
    substitutions run, each to be read as a line of its own."""
    out, inner = [], []
    i, quote = 0, ""
    while i < len(line):
        c = line[i]
        step = 1
        if quote == "'":
            quote = "" if c == "'" else quote
        elif c == "\\":
            step = 2
        elif c in "'\"":
            quote = "" if quote == c else quote or c
        elif c == "#" and not quote and (i == 0 or line[i - 1].isspace()):
            break
        elif c == "`" or line.startswith("$(", i):
            end = _substitution_end(line, i)
            body = line[i + 1:end - 1] if c == "`" else line[i + 2:end - 1]
            inner.append(body)
            out.append(UNKNOWN)
            i = end
            continue
        out.append(line[i:i + step])
        i += step
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
        if w in _CONTROL or ("=" in w and not w.startswith("-") and w.split("=", 1)[0].isidentifier()):
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
        value = rest[i + 1] if i + 1 < len(rest) else ""
        if w in spec.code:
            return _code_runs_lab(value, spec.code[w])
        if spec.modules and w == "-m":
            return is_lab_module(value)
        if w in spec.script:
            return is_lab(value)
        if not w.startswith(("-", "+")):
            return is_lab(w)
        i += 2 if w in spec.value else 1
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
