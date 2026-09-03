"""Blender-independent value-based records for NodeForge semantic lowering."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from .compiler_identities import BindingId, CallSiteId, FunctionId


class IRFunctionMaterializationMode(str, Enum):
    """Select physical materialization semantics for one resolved reusable call."""

    SHARED = "SHARED"
    UNIQUE = "UNIQUE"


@dataclass(frozen=True)
class IRFunctionMaterialization:
    """Describe how one resolved reusable function call is materialized."""

    callee: FunctionId
    mode: IRFunctionMaterializationMode
    call_site: CallSiteId | None = None

    def __post_init__(self):
        """Validate the semantic materialization invariants."""
        if not isinstance(self.callee, FunctionId):
            raise TypeError("callee must be a FunctionId")
        if self.mode is IRFunctionMaterializationMode.SHARED:
            if self.call_site is not None:
                raise ValueError("shared function materialization cannot carry a CallSiteId")
            return
        if self.mode is IRFunctionMaterializationMode.UNIQUE:
            if not isinstance(self.call_site, CallSiteId):
                raise ValueError("unique function materialization requires a CallSiteId")
            if self.call_site.callee != self.callee:
                raise ValueError("function materialization CallSiteId must target the same callee")
            return
        raise ValueError("unsupported function materialization mode")


@dataclass(frozen=True)
class IRValue:
    """Identify one typed semantic value inside a single :class:`IRProgram`."""

    id: int
    typ: str


@dataclass(frozen=True)
class IRArray:
    """Store one immutable compiler-structural expression result."""

    items: tuple["IRResult", ...]


IRResult: TypeAlias = IRValue | IRArray


@dataclass(frozen=True)
class IRLiteral:
    """Produce one scalar runtime literal."""

    result: IRValue
    depth: int
    value: object


@dataclass(frozen=True)
class IRBinding:
    """Produce one value from an existing canonical runtime binding slot."""

    result: IRValue
    depth: int
    binding_id: BindingId


@dataclass(frozen=True)
class IRUnary:
    """Produce one value from a typed unary operation."""

    result: IRValue
    depth: int
    op: str
    operand: IRValue


@dataclass(frozen=True)
class IRBinary:
    """Produce one value from a typed binary operation."""

    result: IRValue
    depth: int
    op: str
    left: IRValue
    right: IRValue


@dataclass(frozen=True)
class IRBoolBinary:
    """Produce one Bool value from two ordered Bool operands."""

    result: IRValue
    depth: int
    op: str
    left: IRValue
    right: IRValue


@dataclass(frozen=True)
class IRCompare:
    """Produce one Bool value from a typed comparison."""

    result: IRValue
    depth: int
    op: str
    left: IRValue
    right: IRValue


@dataclass(frozen=True)
class IRConditional:
    """Produce one value by selecting between two same-typed values."""

    result: IRValue
    depth: int
    condition: IRValue
    true_value: IRValue
    false_value: IRValue


@dataclass(frozen=True)
class IRVectorLiteral:
    """Produce one Vector value from normalized compile-time components."""

    result: IRValue
    depth: int
    components: tuple[float, float, float]


@dataclass(frozen=True)
class IRObjectProperty:
    """Produce one typed Object Info property from an Object runtime value."""

    result: IRValue
    depth: int
    value: IRValue
    property_name: str


@dataclass(frozen=True)
class IRVectorComponent:
    """Produce one Float value from a Vector component."""

    result: IRValue
    depth: int
    value: IRValue
    component: str


IROperation: TypeAlias = (
    IRLiteral
    | IRBinding
    | IRUnary
    | IRBinary
    | IRBoolBinary
    | IRCompare
    | IRConditional
    | IRVectorLiteral
    | IRObjectProperty
    | IRVectorComponent
)


@dataclass(frozen=True)
class IRProgram:
    """Store one ordered expression program and its final program-local result."""

    operations: tuple[IROperation, ...]
    result: IRResult


__all__ = [
    "IRFunctionMaterializationMode",
    "IRFunctionMaterialization",
    "IRValue",
    "IRArray",
    "IRResult",
    "IROperation",
    "IRProgram",
    "IRLiteral",
    "IRBinding",
    "IRUnary",
    "IRBinary",
    "IRBoolBinary",
    "IRCompare",
    "IRConditional",
    "IRVectorLiteral",
    "IRObjectProperty",
    "IRVectorComponent",
]
