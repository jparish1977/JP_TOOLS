"""
JP_TOOLS/tests/mutmut_gate_support.py
What tests/test_mutmut_gate.py and tests/test_mutmut_gate_state.py build on:
mutmut-gate.py loaded once, throwaway repositories and mutants/ trees, and a
mutmut stood in for.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from _harness import load_script
from jpcheck import mutmut_log

gate = load_script("mutmut-gate.py")
# How the gate runs mutmut: through mutmut-clean.py beside it, its clean run across the children.
MUTMUT = [sys.executable, str(Path(gate.__file__ or "").resolve().parent / "mutmut-clean.py"), "run",
          "--max-children", "2"]

ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")


def repo(tmp: Path) -> Path:
    root = tmp / "repo"
    root.mkdir()
    for args in (["init", "-q"], ["config", "commit.gpgsign", "false"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, env=ENV)
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "vendor").mkdir()
    subprocess.run(["git", "-C", str(root), "add", "a.py"], check=True, env=ENV)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--no-verify", "-m", "one"],
                   check=True, env=ENV)
    return root


def ls(path: Path) -> str:
    return subprocess.run(["git", "-C", str(path), "ls-files"], capture_output=True,
                          text=True, check=True).stdout


def head(path: Path) -> str:
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


def commit(root: Path, files: dict[str, str], message: str) -> str:
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(body, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=ENV)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--no-verify", "-m", message],
                   check=True, env=ENV)
    return head(root)


def run_mutmut(root: Path, full: bool, rc: int = 0,
               env: dict[str, str] | None = None) -> list[dict[str, object]]:
    """Each mutmut invocation gate.run makes, mutmut itself stood in for and
    exiting RC; git runs for real. ENV replaces os.environ for the run.
    "cache_is_dir": whether its MYPY_CACHE_DIR was a directory while it ran."""
    asked: list[dict[str, object]] = []

    def fake(cmd: list[str], cwd: Path, env: dict[str, str], relay: object) -> int:
        cache = env.get("MYPY_CACHE_DIR")
        asked.append({"cmd": cmd, "cwd": cwd, "env": env,
                      "cache_is_dir": cache is not None and Path(cache).is_dir()})
        return rc

    with mock.patch.object(mutmut_log, "run_relayed", side_effect=fake), \
            mock.patch.dict(gate.os.environ, env or {}, clear=env is not None):
        gate.run(root, full, 2)
    return asked


def plant(mutants: Path, rel: str, hashes: dict[str, str], codes: dict[str, int | None]) -> Path:
    """A .meta as mutmut 3.8.0 writes it, for the source file REL."""
    meta = mutants / (rel + ".meta")
    meta.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(zip(META_KEYS, (codes, hashes, {}, {}, {}), strict=True))
    meta.write_text(json.dumps(doc, indent=4), encoding="utf-8")
    return meta


# A .meta's keys, in the order mutmut 3.8.0 writes them.
META_KEYS = ("exit_code_by_key", "hash_by_function_name", "type_check_error_by_key", "durations_by_key",
             "estimated_durations_by_key")


def codes(meta: Path) -> dict[str, int | None]:
    return dict(json.loads(meta.read_text(encoding="utf-8"))["exit_code_by_key"])


def stats(mutants: Path, reach: dict[str, list[str]]) -> None:
    (mutants / gate.STATS).write_text(json.dumps({"tests_by_mangled_function_name": reach}),
                                      encoding="utf-8")
