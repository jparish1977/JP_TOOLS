"""What a pull request's own mutmut runs judged, and what its gate holds.

A PR's kept state carries, for every function its runs never judged, the
verdicts of the state it began from, for code it has not changed; the
baseline is always the newest state's, which may not describe those. So
`mutmut-gate.py run --pr` lists in its record (mutmut-gate.json, "own") each
function it judges, and `gate --pr` holds only those, and any function the
baseline lacks, to the baseline.
"""

import json
from collections.abc import Iterable
from pathlib import Path


class RecordError(Exception):
    """The record cannot say what the pull request judged: could not look."""


def functions(keys: Iterable[str]) -> set[str]:
    """The functions mutant KEYS belong to, module.function as mutmut keys them."""
    return {k.partition("__mutmut_")[0] for k in keys}


def read(record: Path) -> set[str] | None:
    """RECORD's "own"; None when it has none (a state's record, whose
    verdicts all agree with its own baseline) or does not exist. RecordError,
    naming the record, when it cannot be read as one."""
    try:
        raw = record.read_bytes()
    except FileNotFoundError:  # reason: no record is no PR's run yet, said by returning None
        return None
    try:
        doc = json.loads(raw)
    except ValueError as e:
        raise RecordError(f"cannot read {record.name}: {e}") from e
    own = doc.get("own") if isinstance(doc, dict) else None
    if own is None:
        return None
    if not isinstance(own, list) or not all(isinstance(f, str) for f in own):
        raise RecordError(f'{record.name}\'s "own" is not a list of function names')
    return set(own)


def so_far(record: Path) -> set[str]:
    """What the runs before this one judged: RECORD's "own", or none."""
    return read(record) or set()


def required(record: Path) -> set[str]:
    """RECORD's "own" for `gate --pr`, which holds nothing without it."""
    own = read(record)
    if own is None:
        raise RecordError(f"--pr: {record.name} holds no record of what this pull request judged "
                          "(run --pr writes it on a tree with nothing uncommitted)")
    return own


def held(baseline: dict[str, dict[str, int]], current: dict[str, dict[str, int]],
         own: set[str]) -> dict[str, dict[str, int]]:
    """CURRENT's functions a pull request's run is held to BASELINE for: the
    ones its own runs judged (OWN), and any the baseline lacks."""
    return {f: c for f, c in current.items() if f in own or f not in baseline}


def hold(baseline: dict[str, dict[str, int]], current: dict[str, dict[str, int]],
         own: set[str] | None) -> dict[str, dict[str, int]]:
    """What the gate compares: CURRENT whole (OWN None, not a PR's run), or
    held, saying how many it leaves uncompared."""
    if own is None:
        return current
    kept = held(baseline, current, own)
    print(f"mutmut-gate: {len(kept)} function(s) this pull request judged, held to the baseline; "
          f"{len(current) - len(kept)} carry its state's verdicts for code it did not change, not compared")
    return kept
