"""Pure Python emission of value-based Semantic IR from analyzed AST facts."""

from __future__ import annotations

import ast

from .constants import TYPE_BOOL, TYPE_OBJECT
from .nf_types import NFType
from .call_resolution import CallableKind, NamedOutputsCallResult, RuntimeCallResult, TupleCallResult
from .errors import CompileError
from .semantic_analysis import (
    ArrayResultShape,
    ExpressionAnalysis,
    NamedOutputsResultShape,
    RuntimeResultShape,
    SemanticConstant,
    TupleResultShape,
)
from .semantic_ir import (
    IRArray,
    IRCall,
    IRCallArgument,
    IRCallableKind,
    IRCallableTarget,
    IRBinary,
    IRBinding,
    IRBoolBinary,
    IRCompare,
    IRConditional,
    IRLiteral,
    IRNamedOutputs,
    IRObjectProperty,
    IRProgram,
    IRRawNodeOutputMode,
    IRTuple,
    IRUnary,
    IRValue,
    IRVectorComponent,
    IRVectorLiteral,
)


class _IRBuilder:
    """Allocate program-local values and append operations in semantic order."""

    def __init__(self):
        self._next_value_id = 0
        self._operations = []

    def new_value(self, typ: NFType):
        """Allocate the next deterministic value identity for *typ*."""
        if not isinstance(typ, NFType):
            raise TypeError("typ must be an NFType")
        value = IRValue(self._next_value_id, typ)
        self._next_value_id += 1
        return value

    def emit(self, operation):
        """Append one operation after all of its dependencies have been emitted."""
        self._operations.append(operation)
        return operation.result

    def emit_operation(self, operation):
        """Append an operation whose public result is structural or multi-valued."""
        self._operations.append(operation)

    def finish(self, result):
        """Freeze the emitted operations and final result into one IR program."""
        return IRProgram(tuple(self._operations), result)


def lower_analyzed_expression(expr, analysis):
    """Emit one ordered :class:`IRProgram` from resolved and type-checked facts."""
    if not isinstance(analysis, ExpressionAnalysis) or analysis.root is not expr:
        raise CompileError("Internal error: invalid Semantic IR expression analysis")

    builder = _IRBuilder()

    def fact(node):
        try:
            return analysis.facts[node]
        except KeyError as exc:
            raise CompileError(
                f"Internal error: missing semantic fact for {type(node).__name__}"
            ) from exc

    def runtime_type(node_fact):
        shape = node_fact.result_shape
        if not isinstance(shape, RuntimeResultShape):
            raise CompileError("Internal error: runtime IR operation received an array result")
        return shape.typ

    def emit_constant(constant: SemanticConstant, depth):
        """Emit one detached semantic constant recursively at a fixed expression depth."""
        if constant.kind == "scalar":
            result = builder.new_value(constant.typ)
            return builder.emit(IRLiteral(result, depth, constant.value))
        if constant.kind == "vector":
            result = builder.new_value(constant.typ)
            return builder.emit(IRVectorLiteral(result, depth, constant.value))
        if constant.kind == "array":
            return IRArray(tuple(emit_constant(item, depth) for item in constant.items))
        raise CompileError("Unsupported compile-time value in runtime expression")

    def emit(node, depth):
        node_fact = fact(node)

        if isinstance(node, ast.Constant):
            result = builder.new_value(runtime_type(node_fact))
            return builder.emit(IRLiteral(result, depth, node_fact.literal_value))

        if isinstance(node, ast.Name):
            resolved = node_fact.resolved_name
            if resolved is None:
                raise CompileError("Internal error: analyzed name has no resolution fact")
            if resolved.kind == "runtime_binding":
                if resolved.binding_id is None:
                    raise CompileError("Internal error: resolved runtime binding has no BindingId")
                result = builder.new_value(runtime_type(node_fact))
                return builder.emit(IRBinding(result, depth, resolved.binding_id))
            if resolved.kind == "semantic_constant":
                if not isinstance(resolved.value, SemanticConstant):
                    raise CompileError("Internal error: semantic constant resolution has invalid payload")
                return emit_constant(resolved.value, depth)
            if resolved.kind == "allowed_constant":
                result = builder.new_value(runtime_type(node_fact))
                return builder.emit(IRLiteral(result, depth, node_fact.literal_value))
            raise CompileError(f"Internal error: unsupported resolved-name kind {resolved.kind!r}")

        if isinstance(node, (ast.List, ast.Tuple)):
            return IRArray(tuple(emit(child, depth + 1) for child in node.elts))

        if isinstance(node, ast.Attribute):
            value = emit(node.value, depth + 1)
            if isinstance(value, IRNamedOutputs):
                if node_fact.operation != "named_output":
                    raise CompileError("Internal error: named-output attribute has invalid semantic operation")
                try:
                    return value.get(node_fact.literal_value)
                except KeyError as exc:
                    raise CompileError("Internal error: analyzed raw output selection became invalid") from exc
            if not isinstance(value, IRValue):
                raise CompileError("Internal error: attribute base lowered to a structural result")
            result = builder.new_value(runtime_type(node_fact))
            if value.typ == TYPE_OBJECT:
                return builder.emit(IRObjectProperty(result, depth, value, node_fact.operation))
            return builder.emit(IRVectorComponent(result, depth, value, node_fact.operation))

        if isinstance(node, ast.Subscript):
            base = emit(node.value, depth + 1)
            if isinstance(base, IRArray):
                try:
                    return base.items[node_fact.literal_value]
                except IndexError as exc:
                    raise CompileError("Internal error: analyzed array index became invalid") from exc
            if isinstance(base, IRTuple):
                try:
                    return base.items[node_fact.literal_value]
                except IndexError as exc:
                    raise CompileError("Internal error: analyzed tuple index became invalid") from exc
            if isinstance(base, IRNamedOutputs):
                try:
                    return base.get(node_fact.literal_value)
                except KeyError as exc:
                    raise CompileError("Internal error: analyzed raw output selection became invalid") from exc
            if not isinstance(base, IRValue):
                raise CompileError("Internal error: subscript base has invalid IR result")
            result = builder.new_value(runtime_type(node_fact))
            return builder.emit(IRVectorComponent(result, depth, base, node_fact.operation))

        if isinstance(node, ast.Call):
            analyzed = node_fact.analyzed_call
            if analyzed is None:
                raise CompileError("Internal error: semantic call fact has no normalized call payload")
            if len(analyzed.runtime_operands) != len(node_fact.call_operand_nodes):
                raise CompileError("Internal error: analyzed call operand metadata is inconsistent")
            arguments = []
            for operand_meta, operand_node in zip(analyzed.runtime_operands, node_fact.call_operand_nodes):
                operand = emit(operand_node, depth + 1)
                if not isinstance(operand, IRValue):
                    raise CompileError("Internal error: call runtime operand lowered to a structural result")
                if operand.typ != operand_meta.typ:
                    raise CompileError("Internal error: analyzed call operand type changed during IR lowering")
                arguments.append(IRCallArgument(operand_meta.parameter_name, operand))

            if analyzed.target.kind is CallableKind.BUILTIN:
                target = IRCallableTarget(IRCallableKind.BUILTIN, analyzed.target.source_name)
            elif analyzed.target.kind is CallableKind.OBJECT_INFO:
                target = IRCallableTarget(IRCallableKind.OBJECT_INFO, "Object.info")
            else:
                raise CompileError("Internal error: dynamic callable reached Semantic Call IR lowering")

            if isinstance(analyzed.result, RuntimeCallResult):
                results = (builder.new_value(analyzed.result.typ),)
                structural_result = results[0]
            elif isinstance(analyzed.result, TupleCallResult):
                results = tuple(builder.new_value(typ) for typ in analyzed.result.types)
                structural_result = IRTuple(results)
            elif isinstance(analyzed.result, NamedOutputsCallResult):
                results = tuple(builder.new_value(typ) for _, typ in analyzed.result.items)
                structural_result = IRNamedOutputs(
                    tuple((name, value) for (name, _), value in zip(analyzed.result.items, results))
                )
            else:
                raise CompileError("Internal error: unsupported analyzed call result")

            options = tuple(analyzed.options)
            raw_mode = None
            if target.kind is IRCallableKind.BUILTIN and target.name == "node":
                option_map = dict(options)
                mode = option_map.get("raw_output_mode")
                if mode == "SINGLE_OUTPUT":
                    raw_mode = IRRawNodeOutputMode.SINGLE_OUTPUT
                elif mode == "NAMED_OUTPUTS":
                    raw_mode = IRRawNodeOutputMode.NAMED_OUTPUTS
                else:
                    raise CompileError("Internal error: raw node Call IR is missing output mode")
            builder.emit_operation(
                IRCall(
                    results=results,
                    depth=depth,
                    target=target,
                    arguments=tuple(arguments),
                    options=options,
                    raw_output_mode=raw_mode,
                )
            )
            return structural_result

        if isinstance(node, ast.BinOp):
            left = emit(node.left, depth + 1)
            right = emit(node.right, depth + 1)
            if not isinstance(left, IRValue) or not isinstance(right, IRValue):
                raise CompileError("Internal error: binary operand lowered to an array result")
            result = builder.new_value(runtime_type(node_fact))
            return builder.emit(IRBinary(result, depth, node_fact.operation, left, right))

        if isinstance(node, ast.UnaryOp):
            operand = emit(node.operand, depth + 1)
            if isinstance(node.op, ast.UAdd):
                return operand
            if not isinstance(operand, IRValue):
                raise CompileError("Internal error: unary operand lowered to an array result")
            result = builder.new_value(runtime_type(node_fact))
            return builder.emit(IRUnary(result, depth, node_fact.operation, operand))

        if isinstance(node, ast.BoolOp):
            if len(node.values) < 2:
                return emit(node.values[0], depth + 1)
            current = emit(node.values[0], depth + 1)
            if not isinstance(current, IRValue):
                raise CompileError("Internal error: boolean operand lowered to an array result")
            for child in node.values[1:]:
                nxt = emit(child, depth + 1)
                if not isinstance(nxt, IRValue):
                    raise CompileError("Internal error: boolean operand lowered to an array result")
                result = builder.new_value(TYPE_BOOL)
                current = builder.emit(IRBoolBinary(result, depth, node_fact.operation, current, nxt))
            return current

        if isinstance(node, ast.Compare):
            if len(node_fact.compare_operations) != len(node.ops):
                raise CompileError("Internal error: comparison semantic operation count mismatch")
            comparisons = []
            left_expr = node.left
            # SEMANTIC_IR_VALUE_MIGRATION: Re-lower the shared middle source expression for
            # each comparison pair so this behavior-preserving stage emits distinct IR values
            # and preserves the current duplicated Geometry Nodes topology. Remove this rule
            # only in a dedicated topology-changing plan that defines IR value reuse and
            # updates the corresponding graph-shape contract tests.
            for op, right_expr in zip(node_fact.compare_operations, node.comparators):
                left = emit(left_expr, depth + 1)
                right = emit(right_expr, depth + 1)
                if not isinstance(left, IRValue) or not isinstance(right, IRValue):
                    raise CompileError("Internal error: comparison operand lowered to an array result")
                result = builder.new_value(TYPE_BOOL)
                comparisons.append(builder.emit(IRCompare(result, depth, op, left, right)))
                left_expr = right_expr
            current = comparisons[0]
            for nxt in comparisons[1:]:
                result = builder.new_value(TYPE_BOOL)
                current = builder.emit(IRBoolBinary(result, depth, "AND", current, nxt))
            return current

        if isinstance(node, ast.IfExp):
            condition = emit(node.test, depth + 1)
            true_value = emit(node.body, depth + 1)
            false_value = emit(node.orelse, depth + 1)
            if not all(isinstance(value, IRValue) for value in (condition, true_value, false_value)):
                raise CompileError("Internal error: conditional operand lowered to an array result")
            result = builder.new_value(runtime_type(node_fact))
            return builder.emit(IRConditional(result, depth, condition, true_value, false_value))

        raise CompileError(f"Internal error: analyzed AST node {type(node).__name__} has no IR emitter")

    result = emit(expr, 0)
    return builder.finish(result)


__all__ = ["lower_analyzed_expression"]
