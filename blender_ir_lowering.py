"""Materialize validated NodeForge Semantic IR into Blender Geometry Nodes."""

from __future__ import annotations

from .constants import TYPE_BOOL, TYPE_FLOAT, TYPE_INT, TYPE_VECTOR
from .errors import CompileError
from .nodes import _boolean_math, _compare, _math, _separate_xyz, _string_value, _switch, _value, _vector_math
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
from .values import Value


def _position(depth):
    """Return the existing expression-layout coordinates for *depth*."""
    return depth * 240, -depth * 90


def _lower_compare(comp, operation, left, right, depth):
    """Materialize one comparison using the current expression placement."""
    x, y = _position(depth)
    return _compare(comp.group, operation, left, right, x, y)


def _lower_boolean_and(comp, values, depth):
    """Combine ordered comparison results with the current Boolean AND topology."""
    if not values:
        raise CompileError("Internal error: Semantic IR comparison chain has no results")
    x, y = _position(depth)
    current = values[0]
    for nxt in values[1:]:
        current = _boolean_math(comp.group, "AND", [current, nxt], x, y)
    return current


def lower_expression(comp, ir, depth=0):
    """Lower one validated Semantic IR expression to the existing runtime ``Value``."""
    x, y = _position(depth)

    if isinstance(ir, IRLiteral):
        if ir.typ == TYPE_BOOL:
            val = _value(comp.group, 1.0 if ir.value else 0.0, x, y)
            zero = _value(comp.group, 0.0, x + 20, y - 40)
            return _compare(comp.group, "NOT_EQUAL", val, zero, x, y)
        if ir.typ == TYPE_FLOAT:
            return _value(comp.group, ir.value, x, y)
        return _string_value(comp.group, ir.value, x, y)

    if isinstance(ir, IRBinding):
        value = comp.vars.get(ir.name)
        if not isinstance(value, Value):
            raise CompileError(f"Internal error: Semantic IR binding {ir.name!r} is no longer a runtime Value")
        if value.typ != ir.typ:
            raise CompileError(
                f"Internal error: Semantic IR binding {ir.name!r} changed type from {ir.typ} to {value.typ}"
            )
        return value

    if isinstance(ir, IRUnary):
        if ir.op == "+":
            return lower_expression(comp, ir.operand, depth + 1)
        operand = lower_expression(comp, ir.operand, depth + 1)
        if ir.op == "-":
            if ir.operand.typ in {TYPE_FLOAT, TYPE_INT}:
                zero = _value(comp.group, 0.0, x, y - 40)
                return _math(comp.group, "SUBTRACT", [zero, operand], x, y)
            if ir.operand.typ == TYPE_VECTOR:
                minus_one = _value(comp.group, -1.0, x, y - 40)
                return _vector_math(comp.group, "SCALE", [operand, minus_one], TYPE_VECTOR, x, y)
        if ir.op == "not":
            return _boolean_math(comp.group, "NOT", [operand], x, y)
        raise CompileError(f"Internal error: unsupported Semantic IR unary operation {ir.op!r}")

    if isinstance(ir, IRBinary):
        left = lower_expression(comp, ir.left, depth + 1)
        right = lower_expression(comp, ir.right, depth + 1)
        if ir.left.typ in {TYPE_FLOAT, TYPE_INT} and ir.right.typ in {TYPE_FLOAT, TYPE_INT}:
            return _math(comp.group, ir.op, [left, right], x, y)
        if ir.op in {"ADD", "SUBTRACT"} and ir.left.typ == TYPE_VECTOR and ir.right.typ == TYPE_VECTOR:
            return _vector_math(comp.group, ir.op, [left, right], TYPE_VECTOR, x, y)
        if ir.op == "MULTIPLY":
            if ir.left.typ == TYPE_VECTOR and ir.right.typ == TYPE_FLOAT:
                return _vector_math(comp.group, "SCALE", [left, right], TYPE_VECTOR, x, y)
            if ir.left.typ == TYPE_FLOAT and ir.right.typ == TYPE_VECTOR:
                return _vector_math(comp.group, "SCALE", [right, left], TYPE_VECTOR, x, y)
            if ir.left.typ == TYPE_VECTOR and ir.right.typ == TYPE_VECTOR:
                return _vector_math(comp.group, "MULTIPLY", [left, right], TYPE_VECTOR, x, y)
        if ir.op == "DIVIDE" and ir.left.typ == TYPE_VECTOR and ir.right.typ == TYPE_FLOAT:
            inv = _math(comp.group, "DIVIDE", [_value(comp.group, 1.0, x, y - 40), right], x, y)
            return _vector_math(comp.group, "SCALE", [left, inv], TYPE_VECTOR, x, y)
        raise CompileError(
            f"Internal error: unsupported Semantic IR binary lowering for {ir.left.typ} {ir.op} {ir.right.typ}"
        )

    if isinstance(ir, IRBoolOp):
        if not ir.values:
            raise CompileError("Internal error: Semantic IR Boolean expression has no values")
        current = lower_expression(comp, ir.values[0], depth + 1)
        for child in ir.values[1:]:
            nxt = lower_expression(comp, child, depth + 1)
            current = _boolean_math(comp.group, ir.op, [current, nxt], x, y)
        return current

    if isinstance(ir, IRCompareChain):
        results = []
        # SEMANTIC_IR_MIGRATION: Lower each comparison pair independently to preserve
        # the current Geometry Nodes topology, including repeated materialization of a
        # shared middle operand. The target architecture may define an explicit IR
        # value-reuse/materialization policy. Change or remove this compatibility rule
        # only in a dedicated topology-changing plan with updated Blender regressions.
        for i, op in enumerate(ir.ops):
            left = lower_expression(comp, ir.values[i], depth + 1)
            right = lower_expression(comp, ir.values[i + 1], depth + 1)
            results.append(_lower_compare(comp, op, left, right, depth))
        return _lower_boolean_and(comp, results, depth)

    if isinstance(ir, IRConditional):
        condition = lower_expression(comp, ir.condition, depth + 1)
        true_value = lower_expression(comp, ir.true_value, depth + 1)
        false_value = lower_expression(comp, ir.false_value, depth + 1)
        return _switch(comp.group, condition, false_value, true_value, x, y)

    if isinstance(ir, IRVectorComponent):
        value = lower_expression(comp, ir.value, depth + 1)
        return _separate_xyz(comp.group, value, ir.component, x, y)

    raise CompileError(f"Internal error: unsupported Semantic IR node {type(ir).__name__}")


__all__ = ["lower_expression"]
