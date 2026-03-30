"""Tests for calculator.py — the test_divide_by_zero test exposes the known bug."""

from calculator import add, divide, multiply, subtract


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(5, 3) == 2


def test_multiply():
    assert multiply(3, 4) == 12


def test_divide():
    assert divide(10, 2) == 5.0


def test_divide_by_zero():
    # Should return None, not raise ZeroDivisionError
    assert divide(10, 0) is None
