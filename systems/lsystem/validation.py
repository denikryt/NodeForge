"""Validation helpers for L-system grammar strings."""

import re

from ...errors import CompileError

COMMANDS = frozenset("Ff+-[]")
_ALLOWED_RE = re.compile(r"^[A-Za-z0-9_]+$")


def is_allowed_symbol(ch: str) -> bool:
    """Return True when *ch* is a turtle command or ignored grammar symbol."""
    return ch in COMMANDS or bool(_ALLOWED_RE.match(ch))


def validate_stream(value: str, context: str) -> str:
    """Validate an axiom or rule replacement string."""
    if not isinstance(value, str):
        raise CompileError(f"{context} must be a compile-time string")
    for ch in value:
        if not is_allowed_symbol(ch):
            raise CompileError(f"{context} contains invalid L-system symbol {ch!r}")
    return value


def validate_rule_symbol(value: str) -> str:
    """Validate a single-symbol rule predecessor."""
    if not isinstance(value, str):
        raise CompileError("ls_rule() predecessor must be a compile-time string")
    if value == "":
        raise CompileError("ls_rule() predecessor cannot be empty")
    if len(value) != 1:
        raise CompileError("ls_rule() predecessor must be exactly one symbol")
    if not is_allowed_symbol(value):
        raise CompileError(f"ls_rule() predecessor contains invalid L-system symbol {value!r}")
    return value


__all__ = ["COMMANDS", "is_allowed_symbol", "validate_stream", "validate_rule_symbol"]
