"""Blender-independent value-based records for NodeForge semantic lowering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from .compiler_identities import BindingId


@dataclass(frozen=True)
class IRValue:
    """Identify one typed semantic value inside a single :class:`IRProgram`."""

    id: int
    typ: str


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
    | IRVectorComponent
)


@dataclass(frozen=True)
class IRProgram:
    """Store one ordered expression program and its final program-local result."""

    operations: tuple[IROperation, ...]
    result: IRValue


__all__ = [
    "IRValue",
    "IROperation",
    "IRProgram",
    "IRLiteral",
    "IRBinding",
    "IRUnary",
    "IRBinary",
    "IRBoolBinary",
    "IRCompare",
    "IRConditional",
    "IRVectorComponent",
]
