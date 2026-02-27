"""Tests for fibonacci module: recursive (memoized), iterative, and generator variants."""

import pytest

from forge.core.fibonacci import fibonacci_recursive, fibonacci_iterative, fibonacci_generator


# ---------------------------------------------------------------------------
# fibonacci_recursive (with memoization)
# ---------------------------------------------------------------------------

def test_recursive_base_case_zero():
    assert fibonacci_recursive(0) == 0


def test_recursive_base_case_one():
    assert fibonacci_recursive(1) == 1


def test_recursive_small_values():
    assert fibonacci_recursive(2) == 1
    assert fibonacci_recursive(3) == 2
    assert fibonacci_recursive(4) == 3
    assert fibonacci_recursive(5) == 5
    assert fibonacci_recursive(6) == 8


def test_recursive_known_values():
    assert fibonacci_recursive(10) == 55
    assert fibonacci_recursive(20) == 6765


def test_recursive_large_value_runs_fast_due_to_memoization():
    # This would be extremely slow without memoization (O(2^n))
    assert fibonacci_recursive(50) == 12586269025


def test_recursive_raises_on_negative_input():
    with pytest.raises(ValueError, match="negative"):
        fibonacci_recursive(-1)


# ---------------------------------------------------------------------------
# fibonacci_iterative
# ---------------------------------------------------------------------------

def test_iterative_base_case_zero():
    assert fibonacci_iterative(0) == 0


def test_iterative_base_case_one():
    assert fibonacci_iterative(1) == 1


def test_iterative_small_values():
    assert fibonacci_iterative(2) == 1
    assert fibonacci_iterative(3) == 2
    assert fibonacci_iterative(4) == 3
    assert fibonacci_iterative(5) == 5
    assert fibonacci_iterative(6) == 8


def test_iterative_known_values():
    assert fibonacci_iterative(10) == 55
    assert fibonacci_iterative(20) == 6765


def test_iterative_large_value():
    assert fibonacci_iterative(50) == 12586269025


def test_iterative_raises_on_negative_input():
    with pytest.raises(ValueError, match="negative"):
        fibonacci_iterative(-1)


def test_iterative_matches_recursive():
    for n in range(30):
        assert fibonacci_iterative(n) == fibonacci_recursive(n)


# ---------------------------------------------------------------------------
# fibonacci_generator
# ---------------------------------------------------------------------------

def test_generator_first_term():
    gen = fibonacci_generator()
    assert next(gen) == 0


def test_generator_first_ten_terms():
    expected = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
    gen = fibonacci_generator()
    result = [next(gen) for _ in range(10)]
    assert result == expected


def test_generator_is_lazy_and_infinite():
    gen = fibonacci_generator()
    # Consume 100 terms without exhausting the generator
    values = [next(gen) for _ in range(100)]
    assert len(values) == 100
    assert values[0] == 0
    assert values[1] == 1


def test_generator_produces_correct_large_value():
    gen = fibonacci_generator()
    # Advance to the 50th term (index 50, 0-based)
    for _ in range(50):
        next(gen)
    assert next(gen) == 12586269025


def test_generator_with_limit_returns_sequence():
    gen = fibonacci_generator()
    result = list(next(gen) for _ in range(8))
    assert result == [0, 1, 1, 2, 3, 5, 8, 13]
