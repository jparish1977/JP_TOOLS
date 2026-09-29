"""A docstring is not top-level code."""
import os

x = os.environ.get("X")
if x and x > "a":
    print(1)
for _ in range(2):
    pass


def f(a: int) -> int:
    if a:
        return 1
    return 2


class K:
    def m(self, b: int) -> int:
        def inner(c: int) -> int:
            if c:
                return 1
            return 0
        if b:
            return inner(b)
        return 0
