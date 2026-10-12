"""
JP_TOOLS/tests/_lab_shell_cases.py
The shell lines tests/test_lab_not_executed.py feeds to jpcheck.lab_use, each
with its answer written down: (label, runs a lab file?, line). A lab path
that is only an argument, a comment, or quoted or escaped text is a mention.
"""

SH_CASES = [
    ("sh: direct", True, "tools/lab/x.sh --flag"),
    ("sh: ./ direct", True, "./tools/lab/x.sh"),
    ("sh: interpreter", True, "python3 tools/lab/x.py arg"),
    ("sh: env and nice", True, "env FOO=1 nice -n 5 tools/lab/x.sh"),
    ("sh: env -u NAME", True, "env -u HOME tools/lab/x.sh"),
    ("sh: sudo -u", True, "sudo -u joe tools/lab/x.sh"),
    ("sh: sudo -Eu bundled", True, "sudo -Eu joe tools/lab/x.sh"),
    ("sh: timeout -s KILL 30", True, "timeout -s KILL 30 tools/lab/x.sh"),
    ("sh: xargs -n 1", True, "ls | xargs -n 1 sh tools/lab/x.sh"),
    ("sh: exec", True, "exec tools/lab/x.sh"),
    ("sh: source", True, ". tools/lab/x.sh"),
    ("sh: bash -c", True, "bash -c 'tools/lab/x.sh'"),
    ("sh: python3 -c", True, "python3 -c 'import runpy; runpy.run_path(\"tools/lab/x.py\")'"),
    ("sh: python3 -W value", True, "python3 -W ignore tools/lab/x.py"),
    ("sh: python3 -m", True, "python3 -m tools.lab.x"),
    ("sh: bash -o value", True, "bash -o pipefail tools/lab/x.sh"),
    ("sh: php -f", True, "php -f tools/lab/x.php"),
    ("sh: after &&", True, "cd /tmp && bash tools/lab/x.sh"),
    ("sh: if", True, "if tools/lab/x.sh; then echo ok; fi"),
    ("sh: while", True, "while ./tools/lab/x.sh; do sleep 1; done"),
    ("sh: !", True, "! tools/lab/x.sh"),
    ("sh: then", True, "if true; then tools/lab/x.sh; fi"),
    ("sh: $(...)", True, "out=$(tools/lab/x.sh)"),
    ("sh: $(...) in double quotes", True, 'echo "got $(tools/lab/x.sh)"'),
    ("sh: backticks", True, "out=`tools/lab/x.sh`"),
    ("sh: $( left open at the end of the line", True, "out=$(tools/lab/x.sh"),
    ("sh: backticks in double quotes", True, 'echo "got `bash tools/lab/x.sh`"'),
    ("sh: find -exec", True, "find . -name '*.md' -exec tools/lab/x.sh {} \\;"),
    ("sh: workflow run:", True, "      - run: python3 tools/lab/x.py"),
    # Mentions.
    ("sh: cat", False, "cat tools/lab/x.sh"),
    ("sh: echo", False, 'echo "run tools/lab/x.sh to measure"'),
    ("sh: git log", False, "git log -- tools/lab/x.sh"),
    ("sh: a comment", False, "# measured with: bash tools/lab/x.sh"),
    ("sh: a comment with backticks", False, "true  # run `tools/lab/x.sh` to see"),
    ("sh: single-quoted $(...)", False, "echo 'run $(tools/lab/x.sh)'"),
    ("sh: single-quoted backticks", False, "echo 'run `tools/lab/x.sh`'"),
    ("sh: an escaped backtick", False, 'echo "run \\`tools/lab/x.sh\\` to see"'),
    ("sh: only a VAR= naming a lab path", False, "env TOOL=tools/lab/x.sh"),
    ("sh: an interpreter with options only", False, "bash -e  # see tools/lab/x.sh"),
    ("sh: YAML name:", False, "  - name: bash tools/lab/x.sh"),
    ("sh: python3 a script given a lab path", False, "python3 check.py tools/lab/x.py"),
    ("sh: find -exec of something else", False, "find tools/lab -exec cat {} \\;"),
]
