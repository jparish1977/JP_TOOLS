"""
JP_TOOLS/tests/_lab_edge_cases.py
The boundary cases tests/test_lab_not_executed.py feeds to jpcheck.lab_use,
each with its answer written down: (label, runs a lab file?, source). Each
sits where a near miss of the rule would answer differently: a name bound
one way and not another, an option that takes a value or does not, a quote
or a comment that starts or ends one character early.
"""

PY_EDGES = [
    ("py: Path parts that only start like a lab path", False,
     'import subprocess\nfrom pathlib import Path\nsubprocess.run([Path("tools") / "x.sh"])\n'),
    ("py: an attribute of a call's result is no name", False,
     'import subprocess\nf().tool = "tools/lab/x.sh"\nsubprocess.run([g().tool])\n'),
    ("py: import a.b binds a", True, 'import os.path\nos.system("tools/lab/x.sh")\n'),
    ("py: import a.b as x binds a.b", True,
     'import importlib.util as iu\niu.spec_from_file_location("x", "tools/lab/x.py")\n'),
    ("py: from a import b, then b.c", True,
     'from importlib import util\nutil.spec_from_file_location("x", "tools/lab/x.py")\n'),
    ("py: a name assigned before the one it reads", True,
     'import subprocess\ns = LAB + "/x.sh"\nLAB = "tools/lab"\nsubprocess.run([s])\n'),
    ("py: a held list naming a lab path as an argument", False,
     'import subprocess\nCMD = ["ls", "tools/lab"]\nsubprocess.run(CMD)\n'),
    ("py: a held shell string that only cats a lab file", False,
     'import subprocess\nCMD = "cat tools/lab/x.sh"\nsubprocess.run(CMD, shell=True)\n'),
    ("py: an f-string part that is a constant", True,
     'import subprocess\nsubprocess.run([f"tools/{\'lab\'}/x.sh"])\n'),
    ("py: *ARGS spread into argv, running one", True,
     'import subprocess\nARGS = ["bash", "tools/lab/x.sh"]\nsubprocess.run([*ARGS])\n'),
    ("py: *ARGS spread into argv, naming one", False,
     'import subprocess\nARGS = ["ls", "tools/lab"]\nsubprocess.run([*ARGS])\n'),
    ("py: os.execv(path, *args)", True, 'import os\nos.execv("tools/lab/x.sh", *A)\n'),
    ("py: os.execv with no argv", False, 'import os\nos.execv("/bin/bash")\n'),
    ("py: subprocess.run(args=...)", True, 'import subprocess\nsubprocess.run(args=["tools/lab/x.sh"])\n'),
    ("py: os.popen(cmd=...)", True, 'import os\nos.popen(cmd="tools/lab/x.sh")\n'),
    ("py: os.system(command=...)", True, 'import os\nos.system(command="tools/lab/x.sh")\n'),
    ("py: os.system() with nothing", False, 'import os\nos.system()\n'),
    ("py: import_module(name=...)", True, 'import importlib\nimportlib.import_module(name="tools.lab.x")\n'),
    ("py: load_script from a package", True,
     'from tests._harness import load_script\nload_script("tools/lab/x.py")\n'),
    ("py: a classmethod helper", True,
     ('import subprocess\nclass T:\n    @classmethod\n    def go(cls, p):\n'
      '        subprocess.run([p])\nT.go("tools/lab/x.sh")\n')),
    ("py: a helper of a helper, defined first", True,
     ('import subprocess\ndef b(q):\n    a(q)\ndef a(p):\n    subprocess.run([p])\n'
      'b("tools/lab/x.sh")\n')),
    ("py: a helper given more arguments than it names", True,
     ('import subprocess\ndef go(p, *rest):\n    subprocess.run([p])\n'
      'go("tools/lab/x.sh", 1, 2)\n')),
    ("py: a runner reached through a subscript (not seen)", False,
     'import subprocess\nRUN = [subprocess.run]\nRUN[0](["tools/lab/x.sh"])\n'),
    ("py: from tools import lab", True, "from tools import lab\n"),
    ("py: from tools import something else", False, "from tools import other\n"),
    ("py: lab from another package", False, "from pkg import lab\n"),
]

SH_EDGES = [
    ("sh: timeout 5m", True, "timeout 5m tools/lab/x.sh"),
    ("sh: VAR=a=b", True, "OPTS=a=b tools/lab/x.sh"),
    ("sh: two shell options, one taking a value", True, "bash -e -o pipefail tools/lab/x.sh"),
    ("sh: bash +o", True, "bash +o posix tools/lab/x.sh"),
    ("sh: python3 -c with no code", False, "python3 -c  # tools/lab/x.py"),
    ("sh: sudo -E and nothing else", False, "sudo -E  # tools/lab/x.sh"),
    ("sh: find without -exec", False, "find tools/lab -name '*.sh'"),
    ("sh: a substitution after a closed single quote", True, "echo 'a' $(tools/lab/x.sh)"),
    ("sh: a comment after a closed single quote", False, "echo 'a' # $(tools/lab/x.sh)"),
    ("sh: a comment after a closed double quote", False, 'echo "a" # $(tools/lab/x.sh)'),
    ("sh: '#' inside a word", True, "echo a#b $(tools/lab/x.sh)"),
    ("sh: '#' first on the line", False, "#$(tools/lab/x.sh)"),
    ("sh: '#' after a space, none after it", False, "echo x #$(tools/lab/x.sh)"),
    ("sh: a capital X before a comment", False, "echo X # $(tools/lab/x.sh)"),
    ("sh: an escape just before backticks", True, "echo \\x`tools/lab/x.sh`"),
    ("sh: two substitutions, the lab one second", True, "a=$(true) b=$(tools/lab/x.sh)"),
    ("sh: a quoted path inside backticks", True, "out=`bash 'tools/lab/x.sh'`"),
    ("sh: a quoted path inside $()", True, "out=$(bash 'tools/lab/x.sh')"),
    ("sh: backticks then a stray quote", True, "echo `bash tools/lab/x.sh`'"),
]
