"""Legacy placeholder for moved standard-package math callables.

Named math callables are owned by the installed nodeforge.standard package.
Core runtime must resolve them through NodeForge.systems.registry.
"""

NAMES: set[str] = set()


def compile_call(*_args, **_kwargs):
    """Reject direct use of the old core math-callable path."""
    from ..errors import CompileError

    raise CompileError("Named math callables are provided by the nodeforge.standard package")


__all__ = ["NAMES", "compile_call"]
