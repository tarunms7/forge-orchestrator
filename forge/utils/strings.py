"""String utility functions for Forge."""

import re


def snake_to_camel(s: str) -> str:
    """Convert a snake_case string to camelCase.

    Examples:
        >>> snake_to_camel("hello_world")
        'helloWorld'
        >>> snake_to_camel("foo_bar_baz")
        'fooBarBaz'
        >>> snake_to_camel("")
        ''
        >>> snake_to_camel("hello")
        'hello'
    """
    if not s:
        return s

    # Split on one or more underscores, filtering out empty segments
    parts = [p for p in re.split(r"_+", s) if p]
    if not parts:
        return s

    # If there were no underscores in the original, the string is already in
    # camelCase (or a single plain word) — return it unchanged.
    if "_" not in s:
        return s

    # First word stays lowercase; subsequent words are title-cased
    return parts[0].lower() + "".join(word.capitalize() for word in parts[1:])


def camel_to_snake(s: str) -> str:
    """Convert a camelCase (or PascalCase) string to snake_case.

    Handles consecutive uppercase letters such as "XMLParser" → "xml_parser"
    and "getHTTPResponse" → "get_http_response".

    Examples:
        >>> camel_to_snake("helloWorld")
        'hello_world'
        >>> camel_to_snake("XMLParser")
        'xml_parser'
        >>> camel_to_snake("")
        ''
    """
    if not s:
        return s

    # Insert underscore between a run of uppercase letters and a following
    # uppercase+lowercase pair, e.g. "XMLParser" → "XML_Parser"
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)

    # Insert underscore between a lowercase/digit and an uppercase letter,
    # e.g. "helloWorld" → "hello_World"
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)

    return s.lower()


def truncate(s: str, max_len: int, suffix: str = "...") -> str:
    """Truncate *s* to at most *max_len* characters, appending *suffix*.

    If the string is already within *max_len* characters it is returned
    unchanged.  When truncation is required the result is
    ``s[:max_len - len(suffix)] + suffix`` provided ``max_len >= len(suffix)``;
    otherwise the string is simply truncated to ``max_len`` characters with no
    suffix.

    Examples:
        >>> truncate("hello world", 8)
        'hello...'
        >>> truncate("hello", 10)
        'hello'
        >>> truncate("hello world", 5, suffix="")
        'hello'
    """
    if len(s) <= max_len:
        return s

    suffix_len = len(suffix)
    if max_len >= suffix_len:
        return s[: max_len - suffix_len] + suffix

    # max_len is smaller than the suffix itself — plain truncation
    return s[:max_len]
