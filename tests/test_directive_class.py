#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_directive_class.py
jpcheck.directives.Directive, built inside a test.

WHY
    Every Directive the checks use is built when jpcheck.directives is
    imported, which is not inside any test, so mutation testing saw no test
    reach the constructor at all. This builds one and reads it back.

WHAT IS CHECKED
    - name is kept as given; pattern and reason are compiled from the given
      strings (their .pattern is the string).
    - reasoned() finds a reason holding a letter, and refuses one with none.

    python3 tests/test_directive_class.py
"""

import re
import sys

from _harness import check, run
from jpcheck.directives import Directive


def test_a_directive_keeps_what_it_is_given() -> None:
    d = Directive("thing disable", r"thing:\s*disable", r"--\s*(?P<why>.*)")
    check("its name, as given", d.name, "thing disable")
    check("its pattern, compiled from the string", (type(d.pattern), d.pattern.pattern),
          (re.Pattern, r"thing:\s*disable"))
    check("its reason form, compiled from the string", (type(d.reason), d.reason.pattern),
          (re.Pattern, r"--\s*(?P<why>.*)"))
    check("the pattern finds the directive", bool(d.pattern.search("x  # thing: disable=E1")), True)
    check("a reason with a letter is a reason", d.reasoned(" -- the parser needs it"), True)
    check("one with no letter is not", d.reasoned(" -- 42"), False)
    check("no reason form at all is not", d.reasoned(" nothing here"), False)


if __name__ == "__main__":
    sys.exit(run(globals(), "Directive"))
