"""Typed Blender backend value carriers used during IR lowering."""

from ..semantic.constants import TYPE_OBJECT
from ..nf_types import NFType


class Value:
    """Typed reference to a Blender node socket produced by backend lowering."""

    def __init__(self, socket, typ: NFType):
        """Store the Blender socket and canonical NodeForge semantic type."""
        if not isinstance(typ, NFType):
            raise TypeError("typ must be an NFType")
        self.socket = socket
        self.typ = typ


class ObjectValue(Value):
    """Object backend value with a cache for one explicit Object Info realization."""

    def __init__(self, socket):
        """Initialize one Object socket and empty physical Object Info cache."""
        super().__init__(socket, TYPE_OBJECT)
        self._object_info_outputs = None
        self._object_info_cache_config = None


def make_value(socket, typ):
    """Wrap a Blender socket with the specialized backend carrier for *typ*."""
    if typ == TYPE_OBJECT:
        return ObjectValue(socket)
    return Value(socket, typ)


__all__ = ["Value", "ObjectValue", "make_value"]
