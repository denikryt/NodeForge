"""Validation helpers for legacy L-system grammar strings."""

import re

from ...errors import CompileError
from .modules import BUILTIN_COMMANDS as COMMANDS, parse_rule_predecessor

_ALLOWED_RE = re.compile(r"^[A-Za-z0-9_]$")


def is_allowed_symbol(ch: str) -> bool:
    """Return True when *ch* is a turtle command or ignored grammar symbol."""
    return ch in COMMANDS or bool(_ALLOWED_RE.match(ch))


def validate_stream(value: str, context: str) -> str:
    """Validate a legacy axiom or replacement string before marker-aware parsing."""
    if not isinstance(value, str):
        raise CompileError(f"{context} must be a compile-time string")
    # Parenthesized module streams are fully validated after ls_system() collects
    # params and markers. Legacy streams keep the previous strict character contract.
    relaxed = "(" in value or ")" in value
    for ch in value:
        if relaxed and ch in "(),.":
            continue
        if ch.isspace() or not is_allowed_symbol(ch):
            raise CompileError(f"{context} contains invalid L-system symbol {ch!r}")
    return value


def validate_rule_symbol(value: str) -> str:
    """Validate a single-symbol rule predecessor."""
    return parse_rule_predecessor(value)


__all__ = ["COMMANDS", "is_allowed_symbol", "validate_stream", "validate_rule_symbol"]
