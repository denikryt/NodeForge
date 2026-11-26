"""Validation for exact programmatic names of callable result leaves."""

import ast


def validate_named_result_key(key):
    """Require a nonempty exact UTF-8 key that fits a physical socket label."""
    if not isinstance(key, str) or not key:
        raise ValueError("named result key must be a non-empty string")
    if "\0" in key:
        raise ValueError("named result key must not contain NUL")
    try:
        encoded = key.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("named result key must be valid UTF-8") from exc
    if len(encoded) > 63:
        raise ValueError("named result key exceeds the 63-byte UTF-8 limit")


def is_exact_attribute_key(key):
    """Return whether Python dot syntax preserves the exact spelling of a key."""
    if not isinstance(key, str) or not key:
        return False
    try:
        node = ast.parse("r." + key, mode="eval").body
    except (SyntaxError, UnicodeError, ValueError):
        return False
    return (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "r" and node.attr == key)
