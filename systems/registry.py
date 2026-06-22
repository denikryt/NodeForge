"""Registry for embedded system constructor calls."""

from ..errors import CompileError
from .lsystem import constructors as lsystem_constructors

_HANDLERS = dict(lsystem_constructors.HANDLERS)
NAMES = frozenset(_HANDLERS)


def has_system_constructor(name: str) -> bool:
    """Return True when *name* is reserved for a system constructor."""
    return name in _HANDLERS


def compile_call(comp, expr, depth=0):
    """Compile one registered system constructor call."""
    name = expr.func.id
    try:
        handler = _HANDLERS[name]
    except KeyError as exc:
        raise CompileError(f"Unsupported system constructor: {name}") from exc
    return handler(comp, expr, depth)


def validate_no_reserved_collision(name: str, owner: str) -> None:
    """Reject user-owned callables that collide with reserved system names."""
    if name in NAMES:
        raise CompileError(f"{owner} {name!r} collides with reserved system constructor name")


__all__ = ["NAMES", "has_system_constructor", "compile_call", "validate_no_reserved_collision"]
