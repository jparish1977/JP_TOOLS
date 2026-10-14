"""mutmut-gate.py recheck: test every mutant not settled again, by name, in
chunks, resumably.

For timeouts that may have been the box, and for "did my new test kill them"
without a whole run. mutmut re-runs every mutant it is given by name, verdict
or not, so the names are fixed when a recheck starts and each chunk done is
recorded in mutants/ (RECHECK): a recheck stopped part way resumes with the
chunks it had not done, instead of every name again. The record goes when it
ends.
"""

import json
from collections.abc import Callable
from pathlib import Path

from jpcheck import mutmut_log

# The record, in mutants/, beside mutmut's files: {"names": [...], "done": [...]}.
RECHECK = "mutmut-gate-recheck.json"
# What a recheck leaves alone: a verdict that testing again cannot change.
SETTLED = {"killed", "skipped", "caught by type check"}


def statuses(text: str) -> dict[str, str]:
    """{mutant: status} from `mutmut results --all true` lines."""
    out = {}
    for line in text.splitlines():
        name, sep, status = line.strip().rpartition(": ")
        if sep and "__mutmut_" in name:
            out[name] = status
    return out


def recheck(mutants: Path, results: Callable[[], str], run: Callable[[list[str]], None], chunk: int) -> int:
    """Test every mutant RESULTS reports not settled again, RUN on CHUNK names
    at a time, recording each chunk done in MUTANTS; resume from the record
    when there is one. Returns how many it tested."""
    record = mutants / RECHECK
    if record.exists():
        doc = json.loads(record.read_bytes())
        mutmut_log.say(f"resuming a recheck: {len(doc['done'])} of {len(doc['names'])} mutant(s) done")
    else:
        doc = {"names": sorted(n for n, s in statuses(results()).items() if s not in SETTLED), "done": []}
        record.write_bytes(json.dumps(doc).encode())
    done = set(doc["done"])
    pending = [n for n in doc["names"] if n not in done]
    mutmut_log.say(f"recheck: {len(pending)} mutant(s) not killed, tested again by name, {chunk} at a time")
    for start in range(0, len(pending), chunk):
        part = pending[start:start + chunk]
        run(part)
        doc["done"].extend(part)
        record.write_bytes(json.dumps(doc).encode())
    record.unlink()
    return len(pending)
