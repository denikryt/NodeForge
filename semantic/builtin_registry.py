"""Declarative namespace inventory for NodeForge DSL builtins."""

from .builtin_calls import (
    INPUT_DECLARATION_BUILTIN_NAMES,
    IR_CAPABLE_BUILTIN_NAMES,
)

CALLABLE_BUILTIN_NAMES = frozenset(
    IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES
)
RUNTIME_BUILTIN_NAMES = frozenset({"range", "repeat_range"})
BUILTIN_NAMES = frozenset(CALLABLE_BUILTIN_NAMES | RUNTIME_BUILTIN_NAMES)


def has_builtin(name: str) -> bool:
    """Return whether *name* is reserved by the core DSL builtin namespace."""
    return name in BUILTIN_NAMES


def has_callable_builtin(name: str) -> bool:
    """Return whether *name* is a callable core DSL builtin."""
    return name in CALLABLE_BUILTIN_NAMES


__all__ = [
    "BUILTIN_NAMES",
    "CALLABLE_BUILTIN_NAMES",
    "RUNTIME_BUILTIN_NAMES",
    "has_builtin",
    "has_callable_builtin",
]
