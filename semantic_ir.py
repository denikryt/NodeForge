"""Blender-independent typed expression records for NodeForge semantic lowering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True)
class IRLiteral:
    """Represent one scalar runtime literal with its resolved NodeForge type."""

    value: object
    typ: str


@dataclass(frozen=True)
class IRBinding:
    """Represent a reference to one existing runtime binding by name and type."""

    name: str
    typ: str


@dataclass(frozen=True)
class IRUnary:
    """Represent one typed unary operation."""

    op: str
    operand: "IRExpr"
    typ: str


@dataclass(frozen=True)
class IRBinary:
    """Represent one typed binary operation."""

    op: str
    left: "IRExpr"
    right: "IRExpr"
    typ: str


@dataclass(frozen=True)
class IRBoolOp:
    """Represent one typed Boolean and/or expression over ordered operands."""

    op: str
    values: tuple["IRExpr", ...]
    typ: str


@dataclass(frozen=True)
class IRCompareChain:
    """Represent one ordered comparison chain and its resolved operations."""

    values: tuple["IRExpr", ...]
    ops: tuple[str, ...]
    typ: str


@dataclass(frozen=True)
class IRConditional:
    """Represent one typed conditional expression."""

    condition: "IRExpr"
    true_value: "IRExpr"
    false_value: "IRExpr"
    typ: str


@dataclass(frozen=True)
class IRVectorComponent:
    """Represent one Vector component access."""

    value: "IRExpr"
    component: str
    typ: str


IRExpr: TypeAlias = (
    IRLiteral
    | IRBinding
    | IRUnary
    | IRBinary
    | IRBoolOp
    | IRCompareChain
    | IRConditional
    | IRVectorComponent
)


__all__ = [
    "IRExpr",
    "IRLiteral",
    "IRBinding",
    "IRUnary",
    "IRBinary",
    "IRBoolOp",
    "IRCompareChain",
    "IRConditional",
    "IRVectorComponent",
]
