"""Pure Python semantic resolution and type checking for migrated expressions."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from types import MappingProxyType
from typing import AbstractSet, Mapping

from .constants import (
    TYPE_BOOL,
    TYPE_BUNDLE,
    TYPE_FLOAT,
    TYPE_GEOMETRY,
    TYPE_INT,
    TYPE_OBJECT,
    TYPE_STRING,
    TYPE_TOKEN_NAMES,
    TYPE_VECTOR,
    _ALLOWED_CONSTS,
    _BIN_OPS,
    _BOOLEAN_OPS,
    _COMPARE_OPS,
)
from .errors import CompileError
from .compiler_identities import BindingId


_RESERVED_VALUE_LABELS = {
    "DSL builtin",
    "imported function",
    "local function",
    "backend helper",
    "embedded-system constructor",
    "reserved helper",
}
_SWITCH_TYPES = {
    TYPE_FLOAT,
    TYPE_INT,
    TYPE_VECTOR,
    TYPE_BOOL,
    TYPE_GEOMETRY,
    TYPE_STRING,
    TYPE_BUNDLE,
}


@dataclass(frozen=True)
class RuntimeBindingSymbol:
    """Frontend metadata for one resolved runtime binding slot."""

    binding_id: BindingId
    typ: str


@dataclass(frozen=True)
class SemanticEnvironment:
    """Immutable semantic metadata snapshot used by one expression analysis."""

    runtime_bindings: Mapping[str, RuntimeBindingSymbol]
    legacy_binding_names: AbstractSet[str]
    scalar_constants: Mapping[str, tuple[str, object]]
    unsupported_constant_names: AbstractSet[str]
    reserved_name_labels: Mapping[str, str]


@dataclass(frozen=True)
class ResolvedName:
    """Describe how one reached source name resolved in the semantic frontend."""

    kind: str
    typ: str
    name: str | None = None
    value: object | None = None
    binding_id: BindingId | None = None


@dataclass(frozen=True)
class ExpressionFact:
    """Store resolved type and normalized semantic facts for one AST expression."""

    typ: str
    operation: str | None = None
    compare_operations: tuple[str, ...] = ()
    resolved_name: ResolvedName | None = None
    literal_value: object | None = None


@dataclass(frozen=True)
class ExpressionAnalysis:
    """Ephemeral AST-associated semantic facts for one fully owned expression."""

    root: ast.expr
    facts: Mapping[ast.AST, ExpressionFact]


@dataclass(frozen=True)
class _Unsupported:
    """Internal result indicating that this migration stage does not own a path."""


UNSUPPORTED = _Unsupported()


def _is_number_type(typ):
    """Return whether *typ* follows the existing scalar Math-node contract."""
    return typ in {TYPE_FLOAT, TYPE_INT}


def _validate_binary(op_type, left_typ, right_typ):
    """Return the current result type for one semantically valid binary operation."""
    if op_type not in _BIN_OPS:
        raise CompileError(f"Unsupported binary operator: {op_type.__name__}")
    if _is_number_type(left_typ) and _is_number_type(right_typ):
        return TYPE_FLOAT
    if op_type in {ast.Add, ast.Sub} and left_typ == TYPE_VECTOR and right_typ == TYPE_VECTOR:
        return TYPE_VECTOR
    if op_type is ast.Mult:
        if left_typ == TYPE_VECTOR and right_typ == TYPE_FLOAT:
            return TYPE_VECTOR
        if left_typ == TYPE_FLOAT and right_typ == TYPE_VECTOR:
            return TYPE_VECTOR
        if left_typ == TYPE_VECTOR and right_typ == TYPE_VECTOR:
            return TYPE_VECTOR
    if op_type is ast.Div and left_typ == TYPE_VECTOR and right_typ == TYPE_FLOAT:
        return TYPE_VECTOR
    raise CompileError(f"Unsupported operation between {left_typ} and {right_typ}")


def _validate_compare(left_typ, right_typ):
    """Apply the current comparison type contract without backend materialization."""
    if _is_number_type(left_typ) and _is_number_type(right_typ):
        return
    if left_typ == right_typ and left_typ in {TYPE_BOOL, TYPE_VECTOR}:
        return
    raise CompileError("Comparison inputs must both be numeric, both Bool, or both Vector")


def analyze_expression(expr, environment):
    """Resolve and type-check one current Semantic IR expression slice.

    Return an :class:`ExpressionAnalysis` when the whole expression belongs to the
    migrated slice, or ``None`` when traversal first reaches an explicitly legacy
    expression family/value shape. No IR values or Blender resources are created.
    """
    facts = {}

    def record(node, fact):
        facts[node] = fact
        return fact

    def analyze(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return record(node, ExpressionFact(TYPE_BOOL, literal_value=node.value))
            if isinstance(node.value, (int, float)):
                return record(node, ExpressionFact(TYPE_FLOAT, literal_value=node.value))
            if isinstance(node.value, str):
                return record(node, ExpressionFact(TYPE_STRING, literal_value=node.value))
            raise CompileError("Only numeric, boolean and string constants are supported")

        if isinstance(node, ast.Name):
            if node.id in TYPE_TOKEN_NAMES:
                raise CompileError(f"Type token {node.id} may only be used in node(...) type declarations")
            if node.id in environment.runtime_bindings:
                symbol = environment.runtime_bindings[node.id]
                resolved = ResolvedName(
                    "runtime_binding",
                    symbol.typ,
                    name=node.id,
                    binding_id=symbol.binding_id,
                )
                return record(node, ExpressionFact(symbol.typ, resolved_name=resolved))
            if node.id in environment.legacy_binding_names:
                return UNSUPPORTED
            if node.id in environment.scalar_constants:
                typ, value = environment.scalar_constants[node.id]
                resolved = ResolvedName("scalar_constant", typ, name=node.id, value=value)
                return record(node, ExpressionFact(typ, resolved_name=resolved, literal_value=value))
            if node.id in environment.unsupported_constant_names:
                return UNSUPPORTED
            if node.id in _ALLOWED_CONSTS:
                value = _ALLOWED_CONSTS[node.id]
                resolved = ResolvedName("allowed_constant", TYPE_FLOAT, name=node.id, value=value)
                return record(node, ExpressionFact(TYPE_FLOAT, resolved_name=resolved, literal_value=value))
            label = environment.reserved_name_labels.get(node.id)
            if label in _RESERVED_VALUE_LABELS:
                raise CompileError(f"Name {node.id} is registered as {label} and cannot be used as a value")
            raise CompileError(f"Unknown name: {node.id}")

        if isinstance(node, ast.Attribute):
            base = analyze(node.value)
            if base is UNSUPPORTED:
                return UNSUPPORTED
            # SEMANTIC_ANALYSIS_MIGRATION: TYPE_OBJECT attribute semantics still belong to
            # the legacy ObjectValue.resolve_property() path. Keep Object property access
            # outside semantic analysis until its property resolution and result typing are
            # represented frontend-side. Remove this fallback when TYPE_OBJECT attribute
            # access is migrated end-to-end and ObjectValue is no longer the semantic owner.
            if base.typ == TYPE_OBJECT:
                return UNSUPPORTED
            if node.attr in {"x", "y", "z"}:
                if base.typ != TYPE_VECTOR:
                    raise CompileError(".x/.y/.z can only be used on Vector values")
                return record(node, ExpressionFact(TYPE_FLOAT, operation=node.attr))
            return UNSUPPORTED

        if isinstance(node, ast.BinOp):
            left = analyze(node.left)
            if left is UNSUPPORTED:
                return UNSUPPORTED
            right = analyze(node.right)
            if right is UNSUPPORTED:
                return UNSUPPORTED
            op_type = type(node.op)
            typ = _validate_binary(op_type, left.typ, right.typ)
            return record(node, ExpressionFact(typ, operation=_BIN_OPS[op_type]))

        if isinstance(node, ast.UnaryOp):
            operand = analyze(node.operand)
            if operand is UNSUPPORTED:
                return UNSUPPORTED
            if isinstance(node.op, ast.UAdd):
                return record(node, ExpressionFact(operand.typ, operation="+"))
            if isinstance(node.op, ast.USub):
                if _is_number_type(operand.typ):
                    return record(node, ExpressionFact(TYPE_FLOAT, operation="-"))
                if operand.typ == TYPE_VECTOR:
                    return record(node, ExpressionFact(TYPE_VECTOR, operation="-"))
                raise CompileError(f"Unsupported unary operator: {type(node.op).__name__}")
            if isinstance(node.op, ast.Not):
                if operand.typ != TYPE_BOOL:
                    raise CompileError("not expects Bool")
                return record(node, ExpressionFact(TYPE_BOOL, operation="not"))
            raise CompileError(f"Unsupported unary operator: {type(node.op).__name__}")

        if isinstance(node, ast.BoolOp):
            if not node.values:
                return UNSUPPORTED
            if len(node.values) < 2:
                first = analyze(node.values[0])
                if first is UNSUPPORTED:
                    return UNSUPPORTED
                return record(node, ExpressionFact(first.typ))
            op = _BOOLEAN_OPS.get(type(node.op))
            if not op:
                raise CompileError("Unsupported boolean operator")
            first = analyze(node.values[0])
            if first is UNSUPPORTED:
                return UNSUPPORTED
            current_typ = first.typ
            for child in node.values[1:]:
                nxt = analyze(child)
                if nxt is UNSUPPORTED:
                    return UNSUPPORTED
                if current_typ != TYPE_BOOL or nxt.typ != TYPE_BOOL:
                    raise CompileError("Boolean operations expect Bool values")
                current_typ = TYPE_BOOL
            return record(node, ExpressionFact(TYPE_BOOL, operation=op))

        if isinstance(node, ast.Compare):
            if len(node.ops) < 1 or len(node.comparators) < 1:
                raise CompileError("Invalid comparison")
            normalized_ops = []
            left_expr = node.left
            for op_node, right_expr in zip(node.ops, node.comparators):
                left = analyze(left_expr)
                if left is UNSUPPORTED:
                    return UNSUPPORTED
                right = analyze(right_expr)
                if right is UNSUPPORTED:
                    return UNSUPPORTED
                op = _COMPARE_OPS.get(type(op_node))
                if not op:
                    raise CompileError("Unsupported comparison operator")
                _validate_compare(left.typ, right.typ)
                normalized_ops.append(op)
                left_expr = right_expr
            return record(
                node,
                ExpressionFact(TYPE_BOOL, compare_operations=tuple(normalized_ops)),
            )

        if isinstance(node, ast.IfExp):
            condition = analyze(node.test)
            if condition is UNSUPPORTED:
                return UNSUPPORTED
            true_value = analyze(node.body)
            if true_value is UNSUPPORTED:
                return UNSUPPORTED
            false_value = analyze(node.orelse)
            if false_value is UNSUPPORTED:
                return UNSUPPORTED
            if condition.typ != TYPE_BOOL:
                raise CompileError("select(cond, true, false): cond must be Bool")
            if false_value.typ != true_value.typ:
                raise CompileError("select() true/false values must have same type")
            if false_value.typ not in _SWITCH_TYPES:
                return UNSUPPORTED
            return record(node, ExpressionFact(true_value.typ, operation="select"))

        return UNSUPPORTED

    result = analyze(expr)
    if result is UNSUPPORTED:
        return None
    return ExpressionAnalysis(expr, MappingProxyType(dict(facts)))


__all__ = [
    "RuntimeBindingSymbol",
    "SemanticEnvironment",
    "ResolvedName",
    "ExpressionFact",
    "ExpressionAnalysis",
    "analyze_expression",
]
