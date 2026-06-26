"""Removed compare-wrapper built-ins for NodeForge DSL.

Normal comparison operators and raw ``node(...)`` compare construction remain
the supported public APIs. This module stays importable so the central registry
can keep a stable module list while contributing no callable names.
"""

from ..errors import CompileError

NAMES = set()


def compile_call(comp, expr, depth=0):
    """Reject calls if this defensive module is invoked directly."""
    name = getattr(getattr(expr, "func", None), "id", "compare wrapper")
    raise CompileError(f"Unsupported compare wrapper builtin: {name}")
