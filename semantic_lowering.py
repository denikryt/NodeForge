"""Pure Python AST to typed Semantic IR lowering for the initial expression slice."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .constants import (
    TYPE_BOOL,
    TYPE_BUNDLE,
    TYPE_FLOAT,
    TYPE_GEOMETRY,
    TYPE_INT,
    TYPE_MATERIAL,
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
from .semantic_ir import (
    IRBinary,
    IRBinding,
    IRBoolOp,
    IRCompareChain,
    IRConditional,
    IRLiteral,
    IRUnary,
    IRVectorComponent,
)


@dataclass(frozen=True)
class _Unsupported:
    """Internal result indicating that the current IR slice does not own an expression."""


UNSUPPORTED = _Unsupported()

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


def _is_number_type(typ):
    """Return whether *typ* follows the existing scalar Math-node contract."""
    return typ in {TYPE_FLOAT, TYPE_INT}


def _literal_ir(value):
    """Return the first-slice IR literal for one supported scalar Python value."""
    if isinstance(value, bool):
        return IRLiteral(value, TYPE_BOOL)
    if isinstance(value, (int, float)):
        return IRLiteral(value, TYPE_FLOAT)
    if isinstance(value, str):
        return IRLiteral(value, TYPE_STRING)
    return UNSUPPORTED


def _validate_binary(op_type, left, right):
    """Return the existing runtime result type for one supported binary operation."""
    if op_type not in _BIN_OPS:
        raise CompileError(f"Unsupported binary operator: {op_type.__name__}")
    if _is_number_type(left.typ) and _is_number_type(right.typ):
        return TYPE_FLOAT
    if op_type in {ast.Add, ast.Sub} and left.typ == TYPE_VECTOR and right.typ == TYPE_VECTOR:
        return TYPE_VECTOR
    if op_type is ast.Mult:
        if left.typ == TYPE_VECTOR and right.typ == TYPE_FLOAT:
            return TYPE_VECTOR
        if left.typ == TYPE_FLOAT and right.typ == TYPE_VECTOR:
            return TYPE_VECTOR
        if left.typ == TYPE_VECTOR and right.typ == TYPE_VECTOR:
            return TYPE_VECTOR
    if op_type is ast.Div and left.typ == TYPE_VECTOR and right.typ == TYPE_FLOAT:
        return TYPE_VECTOR
    raise CompileError(f"Unsupported operation between {left.typ} and {right.typ}")


def _validate_compare(left, right):
    """Apply the current comparison type contract without creating Blender nodes."""
    if _is_number_type(left.typ) and _is_number_type(right.typ):
        return
    if left.typ == right.typ and left.typ in {TYPE_BOOL, TYPE_VECTOR}:
        return
    raise CompileError("Comparison inputs must both be numeric, both Bool, or both Vector")


def try_lower_expression(
    expr,
    *,
    runtime_binding_types,
    consts,
    reserved_name_labels,
):
    """Return typed Semantic IR, or ``None`` when the initial IR slice does not own *expr*.

    The lowering pass is pure: it consumes only Python AST and semantic type/constant
    views.
    """

    def lower(node):
        if isinstance(node, ast.Constant):
            result = _literal_ir(node.value)
            if result is UNSUPPORTED:
                raise CompileError("Only numeric, boolean and string constants are supported")
            return result

        if isinstance(node, ast.Name):
            if node.id in TYPE_TOKEN_NAMES:
                raise CompileError(f"Type token {node.id} may only be used in node(...) type declarations")
            if node.id in runtime_binding_types:
                return IRBinding(node.id, runtime_binding_types[node.id])
            if node.id in consts:
                literal = _literal_ir(consts[node.id])
                if literal is not UNSUPPORTED:
                    return literal
                return UNSUPPORTED
            if node.id in _ALLOWED_CONSTS:
                return IRLiteral(_ALLOWED_CONSTS[node.id], TYPE_FLOAT)
            label = reserved_name_labels.get(node.id)
            if label in _RESERVED_VALUE_LABELS:
                raise CompileError(f"Name {node.id} is registered as {label} and cannot be used as a value")
            return UNSUPPORTED

        if isinstance(node, ast.Attribute):
            base = lower(node.value)
            if base is UNSUPPORTED:
                return UNSUPPORTED
            # SEMANTIC_IR_MIGRATION: TYPE_OBJECT attribute semantics still belong to the
            # existing ObjectValue.resolve_property() path. The target architecture is for
            # Object property access to be represented and validated in Semantic IR. Remove
            # this fallback when TYPE_OBJECT ast.Attribute lowering is migrated end-to-end
            # and ObjectValue.resolve_property() is no longer the semantic owner.
            if base.typ == TYPE_OBJECT:
                return UNSUPPORTED
            if node.attr in {"x", "y", "z"}:
                if base.typ != TYPE_VECTOR:
                    raise CompileError(".x/.y/.z can only be used on Vector values")
                return IRVectorComponent(base, node.attr, TYPE_FLOAT)
            return UNSUPPORTED

        if isinstance(node, ast.BinOp):
            left = lower(node.left)
            if left is UNSUPPORTED:
                return UNSUPPORTED
            right = lower(node.right)
            if right is UNSUPPORTED:
                return UNSUPPORTED
            op_type = type(node.op)
            typ = _validate_binary(op_type, left, right)
            return IRBinary(_BIN_OPS[op_type], left, right, typ)

        if isinstance(node, ast.UnaryOp):
            operand = lower(node.operand)
            if operand is UNSUPPORTED:
                return UNSUPPORTED
            if isinstance(node.op, ast.UAdd):
                return IRUnary("+", operand, operand.typ)
            if isinstance(node.op, ast.USub):
                if _is_number_type(operand.typ):
                    return IRUnary("-", operand, TYPE_FLOAT)
                if operand.typ == TYPE_VECTOR:
                    return IRUnary("-", operand, TYPE_VECTOR)
                raise CompileError(f"Unsupported unary operator: {type(node.op).__name__}")
            if isinstance(node.op, ast.Not):
                if operand.typ != TYPE_BOOL:
                    raise CompileError("not expects Bool")
                return IRUnary("not", operand, TYPE_BOOL)
            raise CompileError(f"Unsupported unary operator: {type(node.op).__name__}")

        if isinstance(node, ast.BoolOp):
            if not node.values:
                return UNSUPPORTED
            if len(node.values) < 2:
                return lower(node.values[0])
            op = _BOOLEAN_OPS.get(type(node.op))
            if not op:
                raise CompileError("Unsupported boolean operator")
            values = []
            current = lower(node.values[0])
            if current is UNSUPPORTED:
                return UNSUPPORTED
            values.append(current)
            for child in node.values[1:]:
                nxt = lower(child)
                if nxt is UNSUPPORTED:
                    return UNSUPPORTED
                if current.typ != TYPE_BOOL or nxt.typ != TYPE_BOOL:
                    raise CompileError("Boolean operations expect Bool values")
                values.append(nxt)
                current = IRBoolOp(op, tuple(values), TYPE_BOOL)
            return IRBoolOp(op, tuple(values), TYPE_BOOL)

        if isinstance(node, ast.Compare):
            if len(node.ops) < 1 or len(node.comparators) < 1:
                raise CompileError("Invalid comparison")
            values = []
            left_expr = node.left
            for index, (op_node, right_expr) in enumerate(zip(node.ops, node.comparators)):
                left = lower(left_expr)
                if left is UNSUPPORTED:
                    return UNSUPPORTED
                right = lower(right_expr)
                if right is UNSUPPORTED:
                    return UNSUPPORTED
                op = _COMPARE_OPS.get(type(op_node))
                if not op:
                    raise CompileError("Unsupported comparison operator")
                _validate_compare(left, right)
                if index == 0:
                    values.append(left)
                values.append(right)
                left_expr = right_expr
            return IRCompareChain(tuple(values), tuple(_COMPARE_OPS[type(op)] for op in node.ops), TYPE_BOOL)

        if isinstance(node, ast.IfExp):
            condition = lower(node.test)
            if condition is UNSUPPORTED:
                return UNSUPPORTED
            true_value = lower(node.body)
            if true_value is UNSUPPORTED:
                return UNSUPPORTED
            false_value = lower(node.orelse)
            if false_value is UNSUPPORTED:
                return UNSUPPORTED
            if condition.typ != TYPE_BOOL:
                raise CompileError("select(cond, true, false): cond must be Bool")
            if false_value.typ != true_value.typ:
                raise CompileError("select() true/false values must have same type")
            if false_value.typ not in _SWITCH_TYPES:
                return UNSUPPORTED
            return IRConditional(condition, true_value, false_value, true_value.typ)

        return UNSUPPORTED

    result = lower(expr)
    return None if result is UNSUPPORTED else result


__all__ = ["try_lower_expression"]
