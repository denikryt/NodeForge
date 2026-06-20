"""Compiler-specific exceptions for NodeForge."""

class CompileError(Exception):
    """Raised when a GN script cannot be parsed or compiled into Geometry Nodes."""
    pass

__all__ = ["CompileError"]
