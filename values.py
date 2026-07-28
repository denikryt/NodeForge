"""Small data containers used during expression compilation."""

from types import MappingProxyType

from .compile_time import CompileTimeObject
from .errors import CompileError
from .constants import TYPE_OBJECT


class Value:
    """Typed reference to a Blender node socket produced by the compiler."""
    def __init__(self, socket, typ):
        """Store the Blender socket and NodeForge semantic type for this value."""
        self.socket = socket
        self.typ = typ


_UNSET = object()


class ObjectValue(Value):
    """Object socket value with lazily configured Object Info access."""

    def __init__(self, socket):
        super().__init__(socket, TYPE_OBJECT)
        self._info_transform_space = "ORIGINAL"
        self._info_as_instance = True
        self._info_resolved = False
        self._object_info_outputs = None

    def configure_info(self, *, transform_space=_UNSET, as_instance=_UNSET):
        """Update unresolved Object Info settings and return this value for chaining."""
        if self._info_resolved:
            raise CompileError("Object.info() cannot be changed after Object Info has been resolved")
        if transform_space is not _UNSET:
            self._info_transform_space = transform_space
        if as_instance is not _UNSET:
            self._info_as_instance = as_instance
        return self

    def resolve_property(self, name, comp, x=0, y=0):
        """Resolve one Object Info output, creating and caching its node on first use."""
        from .builtins.object_info import resolve_object_property
        return resolve_object_property(comp, self, name, x=x, y=y)


def make_value(socket, typ):
    """Wrap a Blender socket with the specialized runtime value for its semantic type."""
    if typ == TYPE_OBJECT:
        return ObjectValue(socket)
    return Value(socket, typ)


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


__all__ = ["Value", "ObjectValue", "NodeResult", "make_value"]
