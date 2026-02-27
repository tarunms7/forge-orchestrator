"""Tests for fibonacci module: recursive (memoized), iterative, and generator variants."""

import itertools

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


def test_recursive_raises_on_multiple_negative_inputs():
    for bad in (-2, -5, -100):
        with pytest.raises(ValueError):
            fibonacci_recursive(bad)


def test_recursive_error_message_contains_value():
    with pytest.raises(ValueError, match=r"-7"):
        fibonacci_recursive(-7)


def test_recursive_returns_int():
    assert isinstance(fibonacci_recursive(0), int)
    assert isinstance(fibonacci_recursive(10), int)


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


def test_iterative_very_large_value():
    # Verify O(n) iterative handles large n without issue
    assert fibonacci_iterative(100) == 354224848179261915075
    assert fibonacci_iterative(200) == 280571172992510140037611932413038677189525


def test_iterative_raises_on_negative_input():
    with pytest.raises(ValueError, match="negative"):
        fibonacci_iterative(-1)


def test_iterative_raises_on_multiple_negative_inputs():
    for bad in (-2, -5, -100):
        with pytest.raises(ValueError):
            fibonacci_iterative(bad)


def test_iterative_error_message_contains_value():
    with pytest.raises(ValueError, match=r"-7"):
        fibonacci_iterative(-7)


def test_iterative_returns_int():
    assert isinstance(fibonacci_iterative(0), int)
    assert isinstance(fibonacci_iterative(10), int)


def test_iterative_matches_recursive():
    for n in range(30):
        assert fibonacci_iterative(n) == fibonacci_recursive(n)


def test_iterative_sequence_property_each_term_is_sum_of_two_previous():
    """F(n) == F(n-1) + F(n-2) for all n >= 2."""
    for n in range(2, 25):
        assert fibonacci_iterative(n) == fibonacci_iterative(n - 1) + fibonacci_iterative(n - 2)


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


def test_generator_sequence_property_each_term_is_sum_of_two_previous():
    """Each yielded term equals the sum of the two preceding terms."""
    gen = fibonacci_generator()
    terms = [next(gen) for _ in range(20)]
    for i in range(2, len(terms)):
        assert terms[i] == terms[i - 1] + terms[i - 2], (
            f"F({i}) should be {terms[i-1]} + {terms[i-2]}, got {terms[i]}"
        )


def test_generator_instances_are_independent():
    """Two separate generators each produce the full sequence independently."""
    gen_a = fibonacci_generator()
    gen_b = fibonacci_generator()

    # Advance gen_a by 5 steps
    for _ in range(5):
        next(gen_a)

    # gen_b should still start from the beginning
    assert next(gen_b) == 0
    assert next(gen_b) == 1

    # And gen_a continues from where it left off (index 5 → F(5) = 5)
    assert next(gen_a) == 5


def test_generator_matches_iterative_for_first_30_values():
    """Generator values must agree with the O(1)-space iterative implementation."""
    gen = fibonacci_generator()
    for n in range(30):
        assert next(gen) == fibonacci_iterative(n), f"Mismatch at index {n}"


def test_generator_with_islice():
    """itertools.islice works naturally with the infinite generator."""
    result = list(itertools.islice(fibonacci_generator(), 10))
    assert result == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
