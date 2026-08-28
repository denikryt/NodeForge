"""Materialize value-based NodeForge Semantic IR into Blender Geometry Nodes."""

from __future__ import annotations

from .constants import TYPE_BOOL, TYPE_FLOAT, TYPE_INT, TYPE_VECTOR
from .errors import CompileError
from .nodes import _boolean_math, _compare, _math, _separate_xyz, _string_value, _switch, _value, _vector_math
from .semantic_ir import (
    IRBinary,
    IRBinding,
    IRBoolBinary,
    IRCompare,
    IRConditional,
    IRLiteral,
    IRUnary,
    IRVectorComponent,
)
from .values import Value


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


def _execute_operation(comp, operation, materialized, base_depth):
    """Execute one ordered IR operation against the Blender backend."""
    effective_depth = base_depth + operation.depth
    x, y = _position(effective_depth)

    if isinstance(operation, IRLiteral):
        if operation.result.typ == TYPE_BOOL:
            val = _value(comp.group, 1.0 if operation.value else 0.0, x, y)
            zero = _value(comp.group, 0.0, x + 20, y - 40)
            result = _compare(comp.group, "NOT_EQUAL", val, zero, x, y)
        elif operation.result.typ == TYPE_FLOAT:
            result = _value(comp.group, operation.value, x, y)
        else:
            result = _string_value(comp.group, operation.value, x, y)
        _store_result(materialized, operation.result, result)
        return

    if isinstance(operation, IRBinding):
        # SEMANTIC_IR_VALUE_MIGRATION: Runtime bindings outside the migrated IR boundary
        # are still owned by comp.vars as socket-bound Value objects. Resolve an IR
        # binding to that backend value only here, at Blender lowering. Remove this
        # bridge when compiler runtime bindings use an explicitly owned compiler value
        # reference and backend sockets come only from the materialization environment.
        value = comp.vars.get(operation.name)
        if not isinstance(value, Value):
            raise CompileError(
                f"Internal error: Semantic IR binding {operation.name!r} is no longer a runtime Value"
            )
        if value.typ != operation.result.typ:
            raise CompileError(
                f"Internal error: Semantic IR binding {operation.name!r} changed type "
                f"from {operation.result.typ} to {value.typ}"
            )
        _store_result(materialized, operation.result, value)
        return

    if isinstance(operation, IRUnary):
        operand = _materialized_value(materialized, operation.operand)
        if operation.op == "+":
            result = operand
        elif operation.op == "-":
            if operation.operand.typ in {TYPE_FLOAT, TYPE_INT}:
                zero = _value(comp.group, 0.0, x, y - 40)
                result = _math(comp.group, "SUBTRACT", [zero, operand], x, y)
            elif operation.operand.typ == TYPE_VECTOR:
                minus_one = _value(comp.group, -1.0, x, y - 40)
                result = _vector_math(comp.group, "SCALE", [operand, minus_one], TYPE_VECTOR, x, y)
            else:
                raise CompileError(
                    f"Internal error: unsupported Semantic IR unary '-' operand {operation.operand.typ}"
                )
        elif operation.op == "not":
            result = _boolean_math(comp.group, "NOT", [operand], x, y)
        else:
            raise CompileError(f"Internal error: unsupported Semantic IR unary operation {operation.op!r}")
        _store_result(materialized, operation.result, result)
        return

    if isinstance(operation, IRBinary):
        left = _materialized_value(materialized, operation.left)
        right = _materialized_value(materialized, operation.right)
        if operation.left.typ in {TYPE_FLOAT, TYPE_INT} and operation.right.typ in {TYPE_FLOAT, TYPE_INT}:
            result = _math(comp.group, operation.op, [left, right], x, y)
        elif operation.op in {"ADD", "SUBTRACT"} and operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_VECTOR:
            result = _vector_math(comp.group, operation.op, [left, right], TYPE_VECTOR, x, y)
        elif operation.op == "MULTIPLY":
            if operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_FLOAT:
                result = _vector_math(comp.group, "SCALE", [left, right], TYPE_VECTOR, x, y)
            elif operation.left.typ == TYPE_FLOAT and operation.right.typ == TYPE_VECTOR:
                result = _vector_math(comp.group, "SCALE", [right, left], TYPE_VECTOR, x, y)
            elif operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_VECTOR:
                result = _vector_math(comp.group, "MULTIPLY", [left, right], TYPE_VECTOR, x, y)
            else:
                raise CompileError(
                    f"Internal error: unsupported Semantic IR binary lowering for "
                    f"{operation.left.typ} {operation.op} {operation.right.typ}"
                )
        elif operation.op == "DIVIDE" and operation.left.typ == TYPE_VECTOR and operation.right.typ == TYPE_FLOAT:
            inv = _math(comp.group, "DIVIDE", [_value(comp.group, 1.0, x, y - 40), right], x, y)
            result = _vector_math(comp.group, "SCALE", [left, inv], TYPE_VECTOR, x, y)
        else:
            raise CompileError(
                f"Internal error: unsupported Semantic IR binary lowering for "
                f"{operation.left.typ} {operation.op} {operation.right.typ}"
            )
        _store_result(materialized, operation.result, result)
        return

    if isinstance(operation, IRBoolBinary):
        left = _materialized_value(materialized, operation.left)
        right = _materialized_value(materialized, operation.right)
        result = _boolean_math(comp.group, operation.op, [left, right], x, y)
        _store_result(materialized, operation.result, result)
        return

    if isinstance(operation, IRCompare):
        left = _materialized_value(materialized, operation.left)
        right = _materialized_value(materialized, operation.right)
        result = _compare(comp.group, operation.op, left, right, x, y)
        _store_result(materialized, operation.result, result)
        return

    if isinstance(operation, IRConditional):
        condition = _materialized_value(materialized, operation.condition)
        true_value = _materialized_value(materialized, operation.true_value)
        false_value = _materialized_value(materialized, operation.false_value)
        result = _switch(comp.group, condition, false_value, true_value, x, y)
        _store_result(materialized, operation.result, result)
        return

    if isinstance(operation, IRVectorComponent):
        value = _materialized_value(materialized, operation.value)
        result = _separate_xyz(comp.group, value, operation.component, x, y)
        _store_result(materialized, operation.result, result)
        return

    raise CompileError(f"Internal error: unsupported Semantic IR operation {type(operation).__name__}")


def lower_expression(comp, program, base_depth=0):
    """Execute one ordered Semantic IR program and return its legacy backend value."""
    materialized = {}
    for operation in program.operations:
        _execute_operation(comp, operation, materialized, base_depth)

    # SEMANTIC_IR_VALUE_MIGRATION: compile_expr() and downstream compiler consumers
    # still expect a socket-bound Value result. Return the materialized backend value
    # for the IR result until the compiler-facing expression contract uses an
    # explicitly owned compiler runtime value/reference model. Remove this bridge
    # when explicit Blender materialization happens only at a backend boundary.
    # Program-local IRValue handles alone are not compiler-wide identity.
    return _materialized_value(materialized, program.result)


__all__ = ["lower_expression"]
