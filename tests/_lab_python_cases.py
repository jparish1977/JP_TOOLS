"""
JP_TOOLS/tests/_lab_python_cases.py
The Python sources tests/test_lab_not_executed.py feeds to jpcheck.lab_use,
each with its answer written down: (label, runs a lab file?, source). A lab
path that is only an argument, data, a comment or a docstring is a mention.
The shell lines are in tests/_lab_shell_cases.py.
"""

PY_CASES = [
    ("py: subprocess.run argv", True, 'import subprocess\nsubprocess.run(["python3", "tools/lab/x.py"])\n'),
    ("py: aliased import", True, 'from subprocess import run as r\nr(["tools/lab/x.sh"])\n'),
    ("py: module alias", True, 'import subprocess as sp\nsp.check_call(["bash", "tools/lab/x.sh"])\n'),
    ("py: two assignments", True, ('import subprocess\nLAB = "tools/lab"\ns = LAB + "/x.py"\n'
                                   'subprocess.run(["python3", s])\n')),
    ("py: an attribute", True, ('import subprocess\nclass T:\n    def __init__(self):\n'
                                '        self.tool = "tools/lab/x.py"\n'
                                '    def go(self):\n        subprocess.run([self.tool])\n')),
    ("py: a held argv list", True, 'import subprocess\nCMD = ["bash", "tools/lab/x.sh"]\nsubprocess.run(CMD)\n'),
    ("py: Path parts", True, ('import subprocess\nfrom pathlib import Path\n'
                              'subprocess.run([Path(".") / "tools" / "lab" / "x.sh"])\n')),
    ("py: shell=True string", True, 'import subprocess\nsubprocess.run(f"cd {D} && bash tools/lab/x.sh", shell=True)\n'),
    ("py: executable=", True, 'import subprocess\nsubprocess.run(["x"], executable="tools/lab/x.sh")\n'),
    ("py: os.system", True, 'import os\nos.system("nice tools/lab/x.sh")\n'),
    ("py: os.popen", True, 'import os\nos.popen("tools/lab/x.sh").read()\n'),
    ("py: subprocess.getoutput", True, 'import subprocess\nsubprocess.getoutput("tools/lab/x.sh")\n'),
    ("py: asyncio exec", True, 'import asyncio\nasyncio.create_subprocess_exec("bash", "tools/lab/x.sh")\n'),
    ("py: os.execl", True, 'import os\nos.execl("/bin/bash", "bash", "tools/lab/x.sh")\n'),
    ("py: os.execv", True, 'import os\nos.execv("/bin/bash", ["bash", "tools/lab/x.sh"])\n'),
    ("py: os.spawnv", True, 'import os\nos.spawnv(os.P_WAIT, "tools/lab/x.sh", ["x"])\n'),
    ("py: os.posix_spawn", True, 'import os\nos.posix_spawn("tools/lab/x.sh", ["x"], {})\n'),
    ("py: a local helper", True, ('import subprocess\nfrom pathlib import Path\n'
                                  'def go(script):\n    return subprocess.run(["bash", str(script)])\n'
                                  'go(Path(".") / "tools" / "lab" / "x.sh")\n')),
    ("py: a helper of a helper, by keyword", True, (
        'import subprocess\ndef a(p):\n    subprocess.run([p])\n'
        'def b(q):\n    a(q)\nb(q="tools/lab/x.sh")\n')),
    # Read before written, in file order: each round a reads last round's b,
    # so a's text grows every round and only the bound ends the reading.
    ("py: names feeding each other", True, ('import subprocess\na = b + "/x"\nb = "tools/lab"\n'
                                            'b = a\nsubprocess.run([a])\n')),
    ("py: a helper with several program parameters", True, (
        'import subprocess\ndef go(a, b, c):\n    subprocess.run([a])\n    subprocess.run([b])\n'
        '    subprocess.run([c])\ngo("ls", "ls", "tools/lab/x.sh")\n')),
    ("py: a method helper", True, ('import subprocess\nclass T:\n    def go(self, p):\n'
                                   '        subprocess.run([p])\nT().go("tools/lab/x.sh")\n')),
    ("py: import tools.lab.x", True, "import tools.lab.x\n"),
    ("py: from tools.lab import x", True, "from tools.lab import x\n"),
    ("py: sys.path then import", True, 'import sys\nsys.path.insert(0, "tools/lab")\nimport x\n'),
    ("py: importlib.import_module", True, 'import importlib\nimportlib.import_module("tools.lab.x")\n'),
    ("py: runpy.run_module", True, 'import runpy\nrunpy.run_module("tools.lab.x")\n'),
    ("py: runpy.run_path", True, 'import runpy\nrunpy.run_path("tools/lab/x.py")\n'),
    ("py: exec(open(...).read())", True, 'exec(open("tools/lab/x.py").read())\n'),
    ("py: load_script", True, 'from _harness import load_script\nload_script("tools/lab/x.py")\n'),
    # Mentions.
    ("py: shell=True, cat of a lab file", False, 'import subprocess\nsubprocess.run("cat tools/lab/x.sh", shell=True)\n'),
    ("py: git log -- a lab path", False, 'import subprocess\nsubprocess.run(["git", "log", "--", "tools/lab/x.py"])\n'),
    ("py: grep -r a lab dir", False, 'import subprocess\nsubprocess.run(["grep", "-r", "p", "tools/lab/"])\n'),
    ("py: a lab path in env=", False, ('import subprocess\n'
                                       'subprocess.run(["make"], env={"HINT": "tools/lab/x.py"})\n')),
    ("py: os.execv of another program", False, 'import os\nos.execv("/bin/cat", ["cat", "tools/lab/x.sh"])\n'),
    ("py: os.execv(*args), not readable", False, 'import os\nARGS = ("tools/lab/x.sh",)\nos.execv(*ARGS)\n'),
    ("py: subprocess.run(**kw), not readable", False, 'import subprocess\nsubprocess.run(**{"args": "tools/lab/x.sh"})\n'),
    ("py: mock.call", False, 'from unittest import mock\nmock.call("tools/lab/x.py")\n'),
    ("py: a method named run", False, 'r = Runner()\nr.run("tools/lab/x.py")\n'),
    ("py: a helper whose parameter is not the program", False, (
        'import subprocess\ndef log(path):\n    subprocess.run(["git", "log", "--", path])\n'
        'log("tools/lab/x.py")\n')),
    ("py: a helper given a lab path in a non-program place", False, (
        'import subprocess\ndef go(prog, note):\n    subprocess.run([prog])\n'
        'go("ls", "tools/lab/x.py")\n')),
    ("py: a file opened for reading", False, 'text = open("tools/lab/README.md").read()\n'),
    ("py: a docstring and a comment", False, '"""Measured with tools/lab/x.py."""\n# see tools/lab/x.py\n'),
    ("py: sys.path gets something else", False, 'import sys\nsys.path.insert(0, "tools")\n'),
    ("py: import_module of another package", False, 'import importlib\nimportlib.import_module("tools.labels")\n'),
]
