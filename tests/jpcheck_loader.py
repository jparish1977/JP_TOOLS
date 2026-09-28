"""Import a jpcheck module for a test, from this checkout whatever the working directory."""

import importlib
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent


def load(name: str) -> ModuleType:
    """jpcheck.<name> from this checkout.

    Bytecode is off, so a mutation run that rewrites the module in place is
    never answered by a cached copy of the original.
    """
    sys.dont_write_bytecode = True
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module(f"jpcheck.{name}")
