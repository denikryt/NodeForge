"""Small data containers used during expression compilation."""

from types import MappingProxyType

from .compile_time import CompileTimeObject
from .errors import CompileError


class Value:
    """Typed reference to a Blender node socket produced by the compiler."""
    def __init__(self, socket, typ):
        """Store the Blender socket and NodeForge semantic type for this value."""
        self.socket = socket
        self.typ = typ


class NodeResult(CompileTimeObject):
    """Compile-time-only container for declared raw node outputs."""

    def __init__(self, outputs):
        object.__setattr__(self, "_outputs", MappingProxyType(dict(outputs)))

    @property
    def output_names(self):
        """Return declared output names in deterministic order."""
        return tuple(self._outputs.keys())

    def get_output(self, name):
        """Return one declared output Value or raise a controlled error."""
        if name not in self._outputs:
            known = ", ".join(repr(n) for n in self.output_names) or "<none>"
            raise CompileError(f"Unknown raw node output {name!r}; declared outputs are: {known}")
        return self._outputs[name]


__all__ = ["Value", "NodeResult"]
