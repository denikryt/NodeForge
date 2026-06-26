"""Deprecated layout built-in module after layout helpers moved to functions."""

from ..errors import CompileError

NAMES = set()


def compile_call(comp, expr, depth=0):
    """Reject layout built-in dispatch after Stage 4 helper migration."""
    name = getattr(getattr(expr, "func", None), "id", "<unknown>")
    raise CompileError(f"Unsupported layout builtin: {name}")
