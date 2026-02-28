"""Tests for the hello utility function."""

from forge.utils.hello import hello


def test_hello_returns_expected_greeting():
    assert hello() == "Hello from Forge!"
