"""Pure Python AST to value-based typed Semantic IR lowering."""

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
    IRBoolBinary,
    IRCompare,
    IRConditional,
    IRLiteral,
    IRProgram,
    IRUnary,
    IRValue,
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


class _IRBuilder:
    """Allocate program-local values and append operations in semantic order."""

    def __init__(self):
        self._next_value_id = 0
        self._operations = []

    def new_value(self, typ):
        """Allocate the next deterministic value identity for *typ*."""
        value = IRValue(self._next_value_id, typ)
        self._next_value_id += 1
        return value

    def emit(self, operation):
        """Append one operation after all of its dependencies have been emitted."""
        self._operations.append(operation)
        return operation.result

    def finish(self, result):
        """Freeze the emitted operations and final value into one IR program."""
        return IRProgram(tuple(self._operations), result)


def _is_number_type(typ):
    """Return whether *typ* follows the existing scalar Math-node contract."""
    return typ in {TYPE_FLOAT, TYPE_INT}


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
    """Return an :class:`IRProgram`, or ``None`` when this IR slice does not own *expr*.

    The lowering pass is pure: it consumes only Python AST and semantic type/constant
    views. Operation placement depths are relative to the returned program root.
    """
    builder = _IRBuilder()

    def emit_literal(value, typ, depth):
        result = builder.new_value(typ)
        return builder.emit(IRLiteral(result, depth, value))

    def lower_literal(value, depth):
        if isinstance(value, bool):
            return emit_literal(value, TYPE_BOOL, depth)
        if isinstance(value, (int, float)):
            return emit_literal(value, TYPE_FLOAT, depth)
        if isinstance(value, str):
            return emit_literal(value, TYPE_STRING, depth)
        return UNSUPPORTED

    def lower(node, depth):
        if isinstance(node, ast.Constant):
            result = lower_literal(node.value, depth)
            if result is UNSUPPORTED:
                raise CompileError("Only numeric, boolean and string constants are supported")
            return result

        if isinstance(node, ast.Name):
            if node.id in TYPE_TOKEN_NAMES:
                raise CompileError(f"Type token {node.id} may only be used in node(...) type declarations")
            if node.id in runtime_binding_types:
                result = builder.new_value(runtime_binding_types[node.id])
                return builder.emit(IRBinding(result, depth, node.id))
            if node.id in consts:
                literal = lower_literal(consts[node.id], depth)
                if literal is not UNSUPPORTED:
                    return literal
                return UNSUPPORTED
            if node.id in _ALLOWED_CONSTS:
                return emit_literal(_ALLOWED_CONSTS[node.id], TYPE_FLOAT, depth)
            label = reserved_name_labels.get(node.id)
            if label in _RESERVED_VALUE_LABELS:
                raise CompileError(f"Name {node.id} is registered as {label} and cannot be used as a value")
            return UNSUPPORTED

        if isinstance(node, ast.Attribute):
            base = lower(node.value, depth + 1)
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
                result = builder.new_value(TYPE_FLOAT)
                return builder.emit(IRVectorComponent(result, depth, base, node.attr))
            return UNSUPPORTED

        if isinstance(node, ast.BinOp):
            left = lower(node.left, depth + 1)
            if left is UNSUPPORTED:
                return UNSUPPORTED
            right = lower(node.right, depth + 1)
            if right is UNSUPPORTED:
                return UNSUPPORTED
            op_type = type(node.op)
            typ = _validate_binary(op_type, left, right)
            result = builder.new_value(typ)
            return builder.emit(IRBinary(result, depth, _BIN_OPS[op_type], left, right))

        if isinstance(node, ast.UnaryOp):
            operand = lower(node.operand, depth + 1)
            if operand is UNSUPPORTED:
                return UNSUPPORTED
            if isinstance(node.op, ast.UAdd):
                result = builder.new_value(operand.typ)
                return builder.emit(IRUnary(result, depth, "+", operand))
            if isinstance(node.op, ast.USub):
                if _is_number_type(operand.typ):
                    result = builder.new_value(TYPE_FLOAT)
                    return builder.emit(IRUnary(result, depth, "-", operand))
                if operand.typ == TYPE_VECTOR:
                    result = builder.new_value(TYPE_VECTOR)
                    return builder.emit(IRUnary(result, depth, "-", operand))
                raise CompileError(f"Unsupported unary operator: {type(node.op).__name__}")
            if isinstance(node.op, ast.Not):
                if operand.typ != TYPE_BOOL:
                    raise CompileError("not expects Bool")
                result = builder.new_value(TYPE_BOOL)
                return builder.emit(IRUnary(result, depth, "not", operand))
            raise CompileError(f"Unsupported unary operator: {type(node.op).__name__}")

        if isinstance(node, ast.BoolOp):
            if not node.values:
                return UNSUPPORTED
            if len(node.values) < 2:
                return lower(node.values[0], depth + 1)
            op = _BOOLEAN_OPS.get(type(node.op))
            if not op:
                raise CompileError("Unsupported boolean operator")
            current = lower(node.values[0], depth + 1)
            if current is UNSUPPORTED:
                return UNSUPPORTED
            for child in node.values[1:]:
                nxt = lower(child, depth + 1)
                if nxt is UNSUPPORTED:
                    return UNSUPPORTED
                if current.typ != TYPE_BOOL or nxt.typ != TYPE_BOOL:
                    raise CompileError("Boolean operations expect Bool values")
                result = builder.new_value(TYPE_BOOL)
                current = builder.emit(IRBoolBinary(result, depth, op, current, nxt))
            return current

        if isinstance(node, ast.Compare):
            if len(node.ops) < 1 or len(node.comparators) < 1:
                raise CompileError("Invalid comparison")
            comparisons = []
            left_expr = node.left
            # SEMANTIC_IR_VALUE_MIGRATION: Re-lower the shared middle source expression for
            # each comparison pair so this behavior-preserving stage emits distinct IR values
            # and preserves the current duplicated Geometry Nodes topology. Remove this rule
            # only in a dedicated topology-changing plan that defines IR value reuse and
            # updates the comparison-chain Blender regression contract.
            for op_node, right_expr in zip(node.ops, node.comparators):
                left = lower(left_expr, depth + 1)
                if left is UNSUPPORTED:
                    return UNSUPPORTED
                right = lower(right_expr, depth + 1)
                if right is UNSUPPORTED:
                    return UNSUPPORTED
                op = _COMPARE_OPS.get(type(op_node))
                if not op:
                    raise CompileError("Unsupported comparison operator")
                _validate_compare(left, right)
                result = builder.new_value(TYPE_BOOL)
                comparisons.append(builder.emit(IRCompare(result, depth, op, left, right)))
                left_expr = right_expr
            current = comparisons[0]
            for nxt in comparisons[1:]:
                result = builder.new_value(TYPE_BOOL)
                current = builder.emit(IRBoolBinary(result, depth, "AND", current, nxt))
            return current

        if isinstance(node, ast.IfExp):
            condition = lower(node.test, depth + 1)
            if condition is UNSUPPORTED:
                return UNSUPPORTED
            true_value = lower(node.body, depth + 1)
            if true_value is UNSUPPORTED:
                return UNSUPPORTED
            false_value = lower(node.orelse, depth + 1)
            if false_value is UNSUPPORTED:
                return UNSUPPORTED
            if condition.typ != TYPE_BOOL:
                raise CompileError("select(cond, true, false): cond must be Bool")
            if false_value.typ != true_value.typ:
                raise CompileError("select() true/false values must have same type")
            if false_value.typ not in _SWITCH_TYPES:
                return UNSUPPORTED
            result = builder.new_value(true_value.typ)
            return builder.emit(IRConditional(result, depth, condition, true_value, false_value))

        return UNSUPPORTED

    result = lower(expr, 0)
    return None if result is UNSUPPORTED else builder.finish(result)


__all__ = ["try_lower_expression"]
