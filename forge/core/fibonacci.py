"""Fibonacci implementations: recursive (memoized), iterative, and generator.

Public API
----------
fibonacci_recursive(n)  -- O(n) time / O(n) space via memoization cache
fibonacci_iterative(n)  -- O(n) time / O(1) space
fibonacci_generator()   -- lazy, infinite generator of the Fibonacci sequence
"""

from functools import lru_cache
from collections.abc import Generator


def fibonacci_recursive(n: int) -> int:
    """Return the nth Fibonacci number using recursion with memoization.

    Uses ``functools.lru_cache`` so each sub-problem is solved only once,
    giving O(n) time complexity instead of the naive O(2^n).

    Args:
        n: Non-negative index in the Fibonacci sequence (0-based).

    Returns:
        The nth Fibonacci number.

    Raises:
        ValueError: If *n* is negative.
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    return _fib_memo(n)


@lru_cache(maxsize=None)
def _fib_memo(n: int) -> int:
    """Memoized helper — never call directly with unvalidated input."""
    if n <= 1:
        return n
    return _fib_memo(n - 1) + _fib_memo(n - 2)


def fibonacci_iterative(n: int) -> int:
    """Return the nth Fibonacci number using an iterative approach.

    Runs in O(n) time and O(1) space — no recursion, no extra memory.

    Args:
        n: Non-negative index in the Fibonacci sequence (0-based).

    Returns:
        The nth Fibonacci number.

    Raises:
        ValueError: If *n* is negative.
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    if n <= 1:
        return n

    prev, curr = 0, 1
    for _ in range(2, n + 1):
        prev, curr = curr, prev + curr
    return curr


def fibonacci_generator() -> Generator[int, None, None]:
    """Yield Fibonacci numbers indefinitely, starting from F(0) = 0.

    This is a lazy, infinite generator.  Callers control how many values
    they consume — use ``itertools.islice`` or a manual loop.

    Yields:
        The next Fibonacci number in the sequence: 0, 1, 1, 2, 3, 5, 8, …

    Example::

        gen = fibonacci_generator()
        first_ten = [next(gen) for _ in range(10)]
        # [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
    """
    prev, curr = 0, 1
    while True:
        yield prev
        prev, curr = curr, prev + curr
