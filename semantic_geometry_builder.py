"""Pure frontend state and IR helpers for the GeometryBuilder DSL abstraction."""

from __future__ import annotations

from dataclasses import dataclass

from .compiler_identities import BindingId
from .errors import CompileError
from .nf_types import NFType
from .semantic_ir import IRBinding, IRCall, IRCallArgument, IRCallableKind, IRCallableTarget, IRProgram, IRValue


@dataclass(frozen=True)
class GeometryBuilderState:
    """Store one source builder's hidden Geometry slot and pending contributions."""

    state_binding_id: BindingId
    pending_binding_ids: tuple[BindingId, ...] = ()
    has_current: bool = False

    def __post_init__(self) -> None:
        """Enforce the batching invariant used by the historical builder topology."""
        object.__setattr__(self, "pending_binding_ids", tuple(self.pending_binding_ids))
        if not isinstance(self.state_binding_id, BindingId):
            raise TypeError("state_binding_id must be a BindingId")
        if not all(isinstance(item, BindingId) for item in self.pending_binding_ids):
            raise TypeError("pending_binding_ids must contain BindingId values")
        if self.has_current and self.pending_binding_ids:
            raise ValueError("current GeometryBuilder state cannot retain pending values")

    def with_pending(self, binding_id: BindingId) -> "GeometryBuilderState":
        """Return state with one pre-snapshot Geometry contribution appended in order."""
        if self.has_current:
            raise ValueError("pending Geometry may only be appended before the first snapshot")
        return GeometryBuilderState(
            self.state_binding_id,
            self.pending_binding_ids + (binding_id,),
            False,
        )

    def current(self) -> "GeometryBuilderState":
        """Return state after materializing the accumulated Geometry into the hidden slot."""
        return GeometryBuilderState(self.state_binding_id, (), True)


def validate_geometry_builder_constructor(call) -> None:
    """Apply the public no-argument ``geometry_builder()`` constructor contract."""
    if call.args:
        raise CompileError("geometry_builder() expects no arguments")
    if call.keywords:
        raise CompileError("geometry_builder() does not support keyword arguments")


def binding_program(binding_id: BindingId) -> IRProgram:
    """Read one hidden Geometry binding without introducing a backend-specific operation."""
    result = IRValue(0, NFType.GEOMETRY)
    return IRProgram((IRBinding(result, 0, binding_id),), result)


def empty_geometry_program() -> IRProgram:
    """Build the existing ``empty_geometry`` Call IR program for an empty snapshot."""
    result = IRValue(0, NFType.GEOMETRY)
    call = IRCall(
        (result,),
        0,
        IRCallableTarget(IRCallableKind.BUILTIN, "empty_geometry"),
        (),
    )
    return IRProgram((call,), result)


def join_binding_program(binding_ids: tuple[BindingId, ...]) -> IRProgram:
    """Join ordered hidden Geometry bindings through the existing variadic ``join`` Call IR."""
    binding_ids = tuple(binding_ids)
    if not binding_ids:
        return empty_geometry_program()
    if len(binding_ids) == 1:
        return binding_program(binding_ids[0])
    operations = []
    arguments = []
    for index, binding_id in enumerate(binding_ids):
        value = IRValue(index, NFType.GEOMETRY)
        operations.append(IRBinding(value, 0, binding_id))
        arguments.append(IRCallArgument(None, value))
    result = IRValue(len(binding_ids), NFType.GEOMETRY)
    operations.append(
        IRCall(
            (result,),
            0,
            IRCallableTarget(IRCallableKind.BUILTIN, "join"),
            tuple(arguments),
        )
    )
    return IRProgram(tuple(operations), result)



__all__ = [
    "GeometryBuilderState",
    "binding_program",
    "empty_geometry_program",
    "join_binding_program",
    "validate_geometry_builder_constructor",
]
