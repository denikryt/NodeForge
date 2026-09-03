"""Materialize value-based NodeForge Semantic IR into Blender Geometry Nodes."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .constants import TYPE_BOOL, TYPE_FLOAT, TYPE_INT, TYPE_VECTOR
from .errors import CompileError
from .compiler_identities import BindingId
from .nodes import _boolean_math, _combine_xyz_mixed, _compare, _math, _separate_xyz, _string_value, _switch, _value, _vector_math
from .semantic_ir import (
    IRBinary,
    IRBinding,
    IRBoolBinary,
    IRCompare,
    IRArray,
    IRConditional,
    IRLiteral,
    IRObjectProperty,
    IRUnary,
    IRVectorComponent,
    IRVectorLiteral,
)
from .values import ObjectValue, Value


@dataclass(frozen=True)
class BlenderIRLoweringContext:
    """Explicit Blender backend inputs for one Semantic IR lowering operation.

    Source semantic analysis must not receive this context. It contains only the
    target Geometry Nodes group and already-materialized runtime bindings needed
    to realize a validated Semantic IR program in Blender.
    """

    group: object
    runtime_bindings: Mapping[BindingId, Value]

    def __post_init__(self):
        """Freeze the binding container while retaining exact backend Value identity."""
        object.__setattr__(self, "runtime_bindings", MappingProxyType(dict(self.runtime_bindings)))


def _position(depth):
    """Return the existing expression-layout coordinates for *depth*."""
    return depth * 240, -depth * 90


def _materialized_value(materialized, value):
    """Return one already-materialized operand or raise a controlled invariant error."""
    try:
        return materialized[value.id]
    except KeyError as exc:
        raise CompileError(
            f"Internal error: Semantic IR value v{value.id} was used before materialization"
        ) from exc


def _store_result(materialized, result, value):
    """Validate and register one backend value for an IR result."""
    if not isinstance(value, Value):
        raise CompileError(
            f"Internal error: Semantic IR value v{result.id} materialized as {type(value).__name__}, not Value"
        )
    if value.typ != result.typ:
        raise CompileError(
            f"Internal error: Semantic IR value v{result.id} expected type {result.typ}, got {value.typ}"
        )
    materialized[result.id] = value


def _lower_literal(context, operation, materialized, x, y):
    """Materialize one literal operation with the existing Blender node topology."""
    if operation.result.typ == TYPE_BOOL:
        val = _value(context.group, 1.0 if operation.value else 0.0, x, y)
        zero = _value(context.group, 0.0, x + 20, y - 40)
        result = _compare(context.group, "NOT_EQUAL", val, zero, x, y)
    elif operation.result.typ == TYPE_FLOAT:
        result = _value(context.group, operation.value, x, y)
    else:
        result = _string_value(context.group, operation.value, x, y)
    _store_result(materialized, operation.result, result)


def _lower_binding(context, operation, materialized):
    """Resolve one semantic runtime binding to its backend materialization."""
    value = context.runtime_bindings.get(operation.binding_id)
    if not isinstance(value, Value):
        raise CompileError(
            f"Internal error: Semantic IR binding {operation.binding_id!r} is no longer a runtime Value"
        )
    if value.typ != operation.result.typ:
        raise CompileError(
            f"Internal error: Semantic IR binding {operation.binding_id!r} changed type "
            f"from {operation.result.typ} to {value.typ}"
        )
    _store_result(materialized, operation.result, value)


def _lower_unary(context, operation, materialized, x, y):
    """Materialize one typed unary IR operation."""
    operand = _materialized_value(materialized, operation.operand)
    if operation.op == "-":
        if operation.operand.typ in {TYPE_FLOAT, TYPE_INT}:
            zero = _value(context.group, 0.0, x, y - 40)
            result = _math(context.group, "SUBTRACT", [zero, operand], x, y)
        elif operation.operand.typ == TYPE_VECTOR:
            minus_one = _value(context.group, -1.0, x, y - 40)
            result = _vector_math(context.group, "SCALE", [operand, minus_one], TYPE_VECTOR, x, y)
        else:
            raise CompileError(
                f"Internal error: unsupported Semantic IR unary '-' operand {operation.operand.typ}"
            )
    elif operation.op == "not":
        result = _boolean_math(context.group, "NOT", [operand], x, y)
    else:
        raise CompileError(f"Internal error: unsupported Semantic IR unary operation {operation.op!r}")
    _store_result(materialized, operation.result, result)


def _lower_binary(context, operation, materialized, x, y):
    """Materialize one typed binary IR operation."""
    left = _materialized_value(materialized, operation.left)
    right = _materialized_value(materialized, operation.right)
    if operation.left.typ in {TYPE_FLOAT, TYPE_INT} and operation.right.typ in {TYPE_FLOAT, TYPE_INT}:
        result = _math(context.group, operation.op, [left, right], x, y)
    elif operation.op in {"ADD", "SUBTRACT"} and operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_VECTOR:
        result = _vector_math(context.group, operation.op, [left, right], TYPE_VECTOR, x, y)
    elif operation.op == "MULTIPLY":
        if operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_FLOAT:
            result = _vector_math(context.group, "SCALE", [left, right], TYPE_VECTOR, x, y)
        elif operation.left.typ == TYPE_FLOAT and operation.right.typ == TYPE_VECTOR:
            result = _vector_math(context.group, "SCALE", [right, left], TYPE_VECTOR, x, y)
        elif operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_VECTOR:
            result = _vector_math(context.group, "MULTIPLY", [left, right], TYPE_VECTOR, x, y)
        else:
            raise CompileError(
                f"Internal error: unsupported Semantic IR binary lowering for "
                f"{operation.left.typ} {operation.op} {operation.right.typ}"
            )
    elif operation.op == "DIVIDE" and operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_FLOAT:
        inv = _math(context.group, "DIVIDE", [_value(context.group, 1.0, x, y - 40), right], x, y)
        result = _vector_math(context.group, "SCALE", [left, inv], TYPE_VECTOR, x, y)
    else:
        raise CompileError(
            f"Internal error: unsupported Semantic IR binary lowering for "
            f"{operation.left.typ} {operation.op} {operation.right.typ}"
        )
    _store_result(materialized, operation.result, result)


def _lower_bool_binary(context, operation, materialized, x, y):
    """Materialize one pairwise Boolean IR operation."""
    left = _materialized_value(materialized, operation.left)
    right = _materialized_value(materialized, operation.right)
    result = _boolean_math(context.group, operation.op, [left, right], x, y)
    _store_result(materialized, operation.result, result)


def _lower_compare(context, operation, materialized, x, y):
    """Materialize one typed comparison IR operation."""
    left = _materialized_value(materialized, operation.left)
    right = _materialized_value(materialized, operation.right)
    result = _compare(context.group, operation.op, left, right, x, y)
    _store_result(materialized, operation.result, result)


def _lower_conditional(context, operation, materialized, x, y):
    """Materialize one conditional IR operation with legacy socket ordering."""
    condition = _materialized_value(materialized, operation.condition)
    true_value = _materialized_value(materialized, operation.true_value)
    false_value = _materialized_value(materialized, operation.false_value)
    result = _switch(context.group, condition, false_value, true_value, x, y)
    _store_result(materialized, operation.result, result)



def _lower_vector_literal(context, operation, materialized, x, y):
    """Materialize one normalized compile-time Vector with existing topology."""
    result = _combine_xyz_mixed(context.group, list(operation.components), x, y)
    _store_result(materialized, operation.result, result)


def _lower_object_property(context, operation, materialized, x, y):
    """Materialize one validated Object property through the exact ObjectValue binding."""
    value = _materialized_value(materialized, operation.value)
    if not isinstance(value, ObjectValue):
        raise CompileError(
            f"Internal error: Semantic IR Object property expected ObjectValue, got {type(value).__name__}"
        )
    result = value.resolve_property(operation.property_name, context.group, x=x, y=y)
    _store_result(materialized, operation.result, result)

def _lower_vector_component(context, operation, materialized, x, y):
    """Materialize one Vector component projection."""
    value = _materialized_value(materialized, operation.value)
    result = _separate_xyz(context.group, value, operation.component, x, y)
    _store_result(materialized, operation.result, result)


def _execute_operation(context, operation, materialized, base_depth):
    """Dispatch one ordered IR operation to its Blender realization helper."""
    effective_depth = base_depth + operation.depth
    x, y = _position(effective_depth)

    if isinstance(operation, IRLiteral):
        _lower_literal(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRBinding):
        _lower_binding(context, operation, materialized)
        return
    if isinstance(operation, IRUnary):
        _lower_unary(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRBinary):
        _lower_binary(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRBoolBinary):
        _lower_bool_binary(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRCompare):
        _lower_compare(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRConditional):
        _lower_conditional(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRVectorLiteral):
        _lower_vector_literal(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRObjectProperty):
        _lower_object_property(context, operation, materialized, x, y)
        return
    if isinstance(operation, IRVectorComponent):
        _lower_vector_component(context, operation, materialized, x, y)
        return
    raise CompileError(f"Internal error: unsupported Semantic IR operation {type(operation).__name__}")



def _materialize_program_result(materialized, result):
    """Reconstruct one legacy backend expression result from Semantic IR structure."""
    if isinstance(result, IRArray):
        return [_materialize_program_result(materialized, item) for item in result.items]
    return _materialized_value(materialized, result)

def lower_expression(context, program, base_depth=0):
    """Execute one ordered Semantic IR program through an explicit Blender context."""
    materialized = {}
    for operation in program.operations:
        _execute_operation(context, operation, materialized, base_depth)

    # SEMANTIC_IR_VALUE_MIGRATION: compile_expr() and downstream compiler consumers still
    # expect legacy backend materializations: one socket-bound Value or a Python list of
    # such values for array expressions. Keep this return bridge while statements, calls,
    # runtime state, and interface wiring use the legacy value contract. Remove it when
    # compiler-owned runtime references replace these backend-facing expression results.
    return _materialize_program_result(materialized, program.result)


__all__ = ["BlenderIRLoweringContext", "lower_expression"]
