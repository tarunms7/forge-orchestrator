"""Tests for string utility functions."""

import pytest
from forge.utils.strings import snake_to_camel, camel_to_snake, truncate


# ─── snake_to_camel ────────────────────────────────────────────────────────────

def test_snake_to_camel_empty_string():
    assert snake_to_camel("") == ""


def test_snake_to_camel_single_word():
    assert snake_to_camel("hello") == "hello"


def test_snake_to_camel_basic_two_words():
    assert snake_to_camel("hello_world") == "helloWorld"


def test_snake_to_camel_multiple_words():
    assert snake_to_camel("foo_bar_baz") == "fooBarBaz"


def test_snake_to_camel_already_camel_case():
    assert snake_to_camel("helloWorld") == "helloWorld"


def test_snake_to_camel_preserves_first_word_lowercase():
    assert snake_to_camel("my_variable_name") == "myVariableName"


def test_snake_to_camel_consecutive_underscores():
    # Consecutive underscores: treat as single separator
    assert snake_to_camel("hello__world") == "helloWorld"


def test_snake_to_camel_leading_underscore():
    # Leading underscores are ignored/stripped
    assert snake_to_camel("_hello") == "hello"


def test_snake_to_camel_all_uppercase_segments():
    assert snake_to_camel("get_http_response") == "getHttpResponse"


# ─── camel_to_snake ────────────────────────────────────────────────────────────

def test_camel_to_snake_empty_string():
    assert camel_to_snake("") == ""


def test_camel_to_snake_single_word_lowercase():
    assert camel_to_snake("hello") == "hello"


def test_camel_to_snake_basic_camel_case():
    assert camel_to_snake("helloWorld") == "hello_world"


def test_camel_to_snake_multiple_words():
    assert camel_to_snake("fooBarBaz") == "foo_bar_baz"


def test_camel_to_snake_already_snake_case():
    assert camel_to_snake("hello_world") == "hello_world"


def test_camel_to_snake_consecutive_uppercase_letters():
    # e.g., XMLParser → xml_parser
    assert camel_to_snake("XMLParser") == "xml_parser"


def test_camel_to_snake_all_uppercase_acronym():
    # e.g., HTTP → http
    assert camel_to_snake("HTTP") == "http"


def test_camel_to_snake_leading_uppercase():
    # PascalCase → snake_case
    assert camel_to_snake("HelloWorld") == "hello_world"


def test_camel_to_snake_acronym_in_middle():
    # e.g., getHTTPResponse → get_http_response
    assert camel_to_snake("getHTTPResponse") == "get_http_response"


def test_camel_to_snake_single_uppercase_letter():
    assert camel_to_snake("A") == "a"


# ─── truncate ─────────────────────────────────────────────────────────────────

def test_truncate_empty_string():
    assert truncate("", 10) == ""


def test_truncate_string_shorter_than_max_len():
    assert truncate("hello", 10) == "hello"


def test_truncate_string_exactly_max_len():
    assert truncate("hello", 5) == "hello"


def test_truncate_string_longer_than_max_len():
    assert truncate("hello world", 8) == "hello..."


def test_truncate_custom_suffix():
    assert truncate("hello world", 7, suffix="--") == "hello--"


def test_truncate_empty_suffix():
    assert truncate("hello world", 5, suffix="") == "hello"


def test_truncate_max_len_equals_suffix_length():
    # max_len == len(suffix) → return only the suffix
    assert truncate("hello world", 3) == "..."


def test_truncate_max_len_less_than_suffix_length():
    # When max_len < len(suffix), fall back to simple truncation
    assert truncate("hello world", 2) == "he"


def test_truncate_default_suffix_is_ellipsis():
    result = truncate("abcdefgh", 5)
    assert result == "ab..."


def test_truncate_long_string_with_default_suffix():
    result = truncate("the quick brown fox", 10)
    assert result == "the qui..."
    assert len(result) == 10
