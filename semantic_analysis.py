"""Pure Python semantic resolution and type checking for migrated expressions."""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from types import MappingProxyType
from typing import AbstractSet, Mapping, TypeAlias

from .compiler_identities import BindingId
from .constants import (
    OBJECT_PROPERTY_TYPES,
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
from .consteval import ConstVector, _const_eval, _is_const_vector
from .errors import CompileError


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
class RuntimeResultShape:
    """Describe one socket-like runtime expression result."""

    typ: str


@dataclass(frozen=True)
class ArrayResultShape:
    """Describe one compiler-structural array expression result."""

    items: tuple["SemanticResultShape", ...]


SemanticResultShape: TypeAlias = RuntimeResultShape | ArrayResultShape


@dataclass(frozen=True)
class SemanticConstant:
    """Detached immutable runtime-materializable view of one compile-time value."""

    kind: str
    typ: str | None = None
    value: object | None = None
    items: tuple["SemanticConstant", ...] = ()


@dataclass(frozen=True)
class SemanticEnvironment:
    """Immutable semantic metadata snapshot used by one expression analysis."""

    runtime_bindings: Mapping[str, RuntimeBindingSymbol]
    legacy_binding_names: AbstractSet[str]
    constants: Mapping[str, SemanticConstant]
    const_eval_values: Mapping[str, object]
    reserved_name_labels: Mapping[str, str]


@dataclass(frozen=True)
class ResolvedName:
    """Describe how one reached source name resolved in the semantic frontend."""

    kind: str
    typ: str | None
    name: str | None = None
    value: object | None = None
    binding_id: BindingId | None = None


@dataclass(frozen=True)
class ExpressionFact:
    """Store normalized semantic facts and result shape for one AST expression."""

    result_shape: SemanticResultShape
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


def _require_runtime_type(fact, context):
    """Return a runtime type or reject a structural result in *context*."""
    if isinstance(fact.result_shape, RuntimeResultShape):
        return fact.result_shape.typ
    if context == "if-expression result":
        raise CompileError("if-expression cannot return arrays")
    raise CompileError(f"{context} requires a runtime value")


def _is_array_result(fact):
    """Return whether one analyzed expression yields compiler-structural array data."""
    return isinstance(fact.result_shape, ArrayResultShape)


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


class _UnsupportedConstEvalValue:
    """Frontend-owned fail-closed placeholder for non-detachable compile-time values."""




def _normalize_semantic_constant_inner(value, active):
    """Return one semantic constant plus whether its container graph contains a cycle."""
    if isinstance(value, bool):
        return SemanticConstant("scalar", TYPE_BOOL, value), False
    if isinstance(value, (int, float)):
        return SemanticConstant("scalar", TYPE_FLOAT, value), False
    if isinstance(value, str):
        return SemanticConstant("scalar", TYPE_STRING, value), False
    if _is_const_vector(value) or (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    ):
        return (
            SemanticConstant("vector", TYPE_VECTOR, tuple(float(item) for item in value)),
            False,
        )
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            return SemanticConstant("unsupported"), True
        active.add(identity)
        try:
            normalized_items = tuple(
                _normalize_semantic_constant_inner(item, active)
                for item in value
            )
        finally:
            active.remove(identity)
        if any(cyclic for _, cyclic in normalized_items):
            return SemanticConstant("unsupported"), True
        return SemanticConstant(
            "array",
            items=tuple(item for item, _ in normalized_items),
        ), False
    return SemanticConstant("unsupported"), False


def _normalize_semantic_constant(value):
    """Return the immutable semantic runtime view of one legacy constant value, cycle-safely."""
    constant, _ = _normalize_semantic_constant_inner(value, set())
    return constant


def _seed_const_eval_copy_memo(value, memo, visited):
    """Pre-seed deepcopy replacements while preserving supported container graph identity."""
    if _is_const_vector(value):
        memo[id(value)] = tuple(float(item) for item in value)
        return
    if isinstance(value, (bool, int, float, str, type(None))):
        return
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in visited:
            return
        visited.add(identity)
        for item in value:
            _seed_const_eval_copy_memo(item, memo, visited)
        return
    memo[id(value)] = _UnsupportedConstEvalValue()


def _detached_const_eval_mapping(constants):
    """Copy the complete const mapping while preserving cycles, aliasing, and container kinds."""
    memo = {}
    visited = set()
    for value in constants.values():
        _seed_const_eval_copy_memo(value, memo, visited)
    return copy.deepcopy(dict(constants), memo)


def build_semantic_constant_snapshot(constants):
    """Build detached semantic and const-eval mappings from legacy constant storage."""
    semantic = {
        name: _normalize_semantic_constant(value)
        for name, value in constants.items()
    }
    const_eval_values = _detached_const_eval_mapping(constants)
    return MappingProxyType(semantic), MappingProxyType(const_eval_values)


def _shape_for_constant(constant):
    """Return the recursive semantic result shape for one supported constant."""
    if constant.kind in {"scalar", "vector"}:
        return RuntimeResultShape(constant.typ)
    if constant.kind == "array":
        return ArrayResultShape(tuple(_shape_for_constant(item) for item in constant.items))
    raise CompileError("Unsupported compile-time value in runtime expression")


def analyze_expression(expr, environment):
    """Resolve and type-check one complete non-call Semantic IR expression tree."""
    facts = {}

    def record(node, fact):
        facts[node] = fact
        return fact

    def runtime_fact(typ, **kwargs):
        return ExpressionFact(RuntimeResultShape(typ), **kwargs)

    def analyze(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return record(node, runtime_fact(TYPE_BOOL, literal_value=node.value))
            if isinstance(node.value, (int, float)):
                return record(node, runtime_fact(TYPE_FLOAT, literal_value=node.value))
            if isinstance(node.value, str):
                return record(node, runtime_fact(TYPE_STRING, literal_value=node.value))
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
                return record(node, runtime_fact(symbol.typ, resolved_name=resolved))
            # COMPLETE_EXPRESSION_IR_LEGACY_BINDING_FALLBACK: Non-Value compiler bindings still
            # have no frontend-owned semantic result shape. Keep the complete enclosing expression
            # on the legacy dispatcher when one is reached; do not carry the compiler object into IR.
            # Remove this fallback when those binding categories are represented semantically and
            # Blender/materialization objects are no longer required to determine their meaning.
            if node.id in environment.legacy_binding_names:
                return UNSUPPORTED
            if node.id in environment.constants:
                constant = environment.constants[node.id]
                if constant.kind == "unsupported":
                    raise CompileError("Unsupported compile-time value in runtime expression")
                resolved = ResolvedName(
                    "semantic_constant",
                    constant.typ,
                    name=node.id,
                    value=constant,
                )
                return record(node, ExpressionFact(_shape_for_constant(constant), resolved_name=resolved))
            if node.id in _ALLOWED_CONSTS:
                value = _ALLOWED_CONSTS[node.id]
                resolved = ResolvedName("allowed_constant", TYPE_FLOAT, name=node.id, value=value)
                return record(node, runtime_fact(TYPE_FLOAT, resolved_name=resolved, literal_value=value))
            label = environment.reserved_name_labels.get(node.id)
            if label in _RESERVED_VALUE_LABELS:
                raise CompileError(f"Name {node.id} is registered as {label} and cannot be used as a value")
            raise CompileError(f"Unknown name: {node.id}")

        if isinstance(node, (ast.List, ast.Tuple)):
            items = []
            for child in node.elts:
                item = analyze(child)
                if item is UNSUPPORTED:
                    return UNSUPPORTED
                items.append(item.result_shape)
            return record(node, ExpressionFact(ArrayResultShape(tuple(items))))

        if isinstance(node, ast.Attribute):
            base = analyze(node.value)
            if base is UNSUPPORTED:
                return UNSUPPORTED
            base_typ = _require_runtime_type(base, "attribute access")
            if base_typ == TYPE_OBJECT:
                if node.attr not in OBJECT_PROPERTY_TYPES:
                    raise CompileError("Object values support only .geometry, .location, .rotation and .scale")
                return record(node, runtime_fact(OBJECT_PROPERTY_TYPES[node.attr], operation=node.attr))
            if node.attr in {"x", "y", "z"}:
                if base_typ != TYPE_VECTOR:
                    raise CompileError(".x/.y/.z can only be used on Vector values")
                return record(node, runtime_fact(TYPE_FLOAT, operation=node.attr))
            return UNSUPPORTED

        if isinstance(node, ast.Subscript):
            base = analyze(node.value)
            if base is UNSUPPORTED:
                return UNSUPPORTED
            try:
                index = int(_const_eval(node.slice, environment.const_eval_values))
            except (CompileError, TypeError, ValueError, OverflowError) as exc:
                raise CompileError("array/vector indexing currently requires a compile-time integer index") from exc
            if isinstance(base.result_shape, ArrayResultShape):
                try:
                    selected_shape = base.result_shape.items[index]
                except IndexError as exc:
                    raise CompileError("array index out of range") from exc
                return record(node, ExpressionFact(selected_shape, operation="array_index", literal_value=index))
            base_typ = _require_runtime_type(base, "subscript")
            if base_typ == TYPE_VECTOR:
                if index not in (0, 1, 2):
                    raise CompileError("vector index must be 0, 1 or 2")
                return record(node, runtime_fact(TYPE_FLOAT, operation=("x", "y", "z")[index]))
            raise CompileError("indexing is supported for arrays and Vector values only")

        if isinstance(node, ast.BinOp):
            left = analyze(node.left)
            if left is UNSUPPORTED:
                return UNSUPPORTED
            right = analyze(node.right)
            if right is UNSUPPORTED:
                return UNSUPPORTED
            left_typ = _require_runtime_type(left, "binary expression")
            right_typ = _require_runtime_type(right, "binary expression")
            op_type = type(node.op)
            typ = _validate_binary(op_type, left_typ, right_typ)
            return record(node, runtime_fact(typ, operation=_BIN_OPS[op_type]))

        if isinstance(node, ast.UnaryOp):
            operand = analyze(node.operand)
            if operand is UNSUPPORTED:
                return UNSUPPORTED
            if isinstance(node.op, ast.UAdd):
                return record(node, ExpressionFact(operand.result_shape, operation="+"))
            operand_typ = _require_runtime_type(operand, "unary expression")
            if isinstance(node.op, ast.USub):
                if _is_number_type(operand_typ):
                    return record(node, runtime_fact(TYPE_FLOAT, operation="-"))
                if operand_typ == TYPE_VECTOR:
                    return record(node, runtime_fact(TYPE_VECTOR, operation="-"))
                raise CompileError(f"Unsupported unary operator: {type(node.op).__name__}")
            if isinstance(node.op, ast.Not):
                if operand_typ != TYPE_BOOL:
                    raise CompileError("not expects Bool")
                return record(node, runtime_fact(TYPE_BOOL, operation="not"))
            raise CompileError(f"Unsupported unary operator: {type(node.op).__name__}")

        if isinstance(node, ast.BoolOp):
            if not node.values:
                return UNSUPPORTED
            first = analyze(node.values[0])
            if first is UNSUPPORTED:
                return UNSUPPORTED
            if len(node.values) < 2:
                return record(node, ExpressionFact(first.result_shape))
            op = _BOOLEAN_OPS.get(type(node.op))
            if not op:
                raise CompileError("Unsupported boolean operator")
            current_typ = _require_runtime_type(first, "boolean expression")
            for child in node.values[1:]:
                nxt = analyze(child)
                if nxt is UNSUPPORTED:
                    return UNSUPPORTED
                next_typ = _require_runtime_type(nxt, "boolean expression")
                if current_typ != TYPE_BOOL or next_typ != TYPE_BOOL:
                    raise CompileError("Boolean operations expect Bool values")
                current_typ = TYPE_BOOL
            return record(node, runtime_fact(TYPE_BOOL, operation=op))

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
                _validate_compare(
                    _require_runtime_type(left, "comparison"),
                    _require_runtime_type(right, "comparison"),
                )
                normalized_ops.append(op)
                left_expr = right_expr
            return record(node, runtime_fact(TYPE_BOOL, compare_operations=tuple(normalized_ops)))

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
            condition_typ = _require_runtime_type(condition, "if-expression condition")
            if _is_array_result(true_value) or _is_array_result(false_value):
                raise CompileError("if-expression cannot return arrays")
            true_typ = _require_runtime_type(true_value, "if-expression result")
            false_typ = _require_runtime_type(false_value, "if-expression result")
            if condition_typ != TYPE_BOOL:
                raise CompileError("select(cond, true, false): cond must be Bool")
            if false_typ != true_typ:
                raise CompileError("select() true/false values must have same type")
            if false_typ not in _SWITCH_TYPES:
                return UNSUPPORTED
            return record(node, runtime_fact(true_typ, operation="select"))

        # COMPLETE_EXPRESSION_IR_CALL_FALLBACK: Call resolution, argument/result shapes, keyword
        # rules, Object.info(), raw nodes, and reusable/local/imported materialization still belong
        # to the legacy call compiler. Keep the complete enclosing expression on that path; do not
        # represent a call as an opaque AST/backend IR leaf. Remove this fallback when semantic
        # callable resolution and call IR own every supported ast.Call category end-to-end.
        if isinstance(node, ast.Call):
            return UNSUPPORTED

        return UNSUPPORTED

    result = analyze(expr)
    if result is UNSUPPORTED:
        return None
    return ExpressionAnalysis(expr, MappingProxyType(dict(facts)))


__all__ = [
    "RuntimeBindingSymbol",
    "RuntimeResultShape",
    "ArrayResultShape",
    "SemanticResultShape",
    "SemanticConstant",
    "SemanticEnvironment",
    "ResolvedName",
    "ExpressionFact",
    "ExpressionAnalysis",
    "build_semantic_constant_snapshot",
    "analyze_expression",
]
