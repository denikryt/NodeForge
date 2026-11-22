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


from dataclasses import dataclass

from ..errors import CompileError
from ..semantic.callable_contracts import canonicalize_group_input_default


@dataclass(frozen=True)
class ConstantPayload:
    """Canonical detached scalar/composite data suitable for a physical socket default."""

    typ: NFType
    value: bool | int | float | str | tuple

    def __post_init__(self):
        """Normalize a valid typed constant without retaining Blender RNA objects."""
        if not isinstance(self.typ, NFType):
            raise TypeError("ConstantPayload.typ must be NFType")
        canonical = canonicalize_group_input_default(self.typ, self.value)
        object.__setattr__(self, "value", canonical)


class DeferredConstant:
    """Attempt-local typed constant, materialized only at a physical socket boundary.

    The identity of this carrier is significant: aliases keep this object and
    share its single producer once the value is physically required.
    """

    __slots__ = ("payload", "_owner_group", "_materialized", "_shared_runtime")

    def __init__(self, typ: NFType, value):
        """Create one canonical value without mutating any Blender node tree."""
        self.payload = ConstantPayload(typ, value)
        self._owner_group = None
        self._materialized = None
        self._shared_runtime = False

    def share_runtime_consumers(self) -> None:
        """Use one physical producer for non-literal consumers of a shared inline value.

        Literal-only sockets may still consume the original constant directly.
        """
        self._shared_runtime = True

    @property
    def typ(self) -> NFType:
        """Return the original semantic type even after physical materialization."""
        return self.payload.typ

    @property
    def value(self):
        """Return immutable canonical constant data."""
        return self.payload.value

    def bind_group(self, group) -> None:
        """Pin this invocation-scoped carrier to its first physical Blender owner."""
        if self._owner_group is None:
            self._owner_group = group
        elif self._owner_group is not group:
            raise CompileError("Deferred constant cannot cross physical node-tree boundaries")


BackendOperand = Value | DeferredConstant

__all__ += ["ConstantPayload", "DeferredConstant", "BackendOperand"]
