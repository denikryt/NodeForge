"""Pure Python semantic resolution and type checking for migrated expressions."""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from types import MappingProxyType
from typing import AbstractSet, Mapping, TypeAlias

from .compiler_identities import BindingId
from .call_resolution import (
    AnalyzedCall,
    AnalyzedCallOperand,
    CallableEnvironment,
    CallableKind,
    NamedOutputsCallResult,
    ResolvedCallable,
    RuntimeCallResult,
    TupleCallResult,
    UNRESOLVED,
    resolve_simple_callable,
)
from .builtin_call_semantics import (
    IR_CAPABLE_BUILTIN_NAMES,
    STATEFUL_FALLBACK_BUILTIN_NAMES,
    analyze_builtin_call,
)
from .function_instances import extract_function_call_modifiers, unsupported_unique
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
from .nf_types import NFType, NUMERIC_NF_TYPES


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
    typ: NFType

    def __post_init__(self):
        """Reject non-canonical runtime type identities."""
        if not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType")


@dataclass(frozen=True)
class RuntimeResultShape:
    """Describe one socket-like runtime expression result."""

    typ: NFType

    def __post_init__(self):
        """Reject non-canonical runtime type identities."""
        if not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType")


@dataclass(frozen=True)
class ArrayResultShape:
    """Describe one compiler-structural array expression result."""

    items: tuple["SemanticResultShape", ...]


@dataclass(frozen=True)
class TupleResultShape:
    """Describe one fixed tuple returned by a compiler-owned runtime call."""

    items: tuple[RuntimeResultShape, ...]


@dataclass(frozen=True)
class NamedOutputsResultShape:
    """Describe declared outputs returned by raw ``node(..., outputs=...)`` syntax."""

    items: tuple[tuple[str, RuntimeResultShape], ...]

    @property
    def names(self) -> tuple[str, ...]:
        """Return declared output names in source order."""
        return tuple(name for name, _ in self.items)

    def get(self, name: str) -> RuntimeResultShape:
        """Return one declared output shape or preserve the current NodeResult diagnostic."""
        for item_name, shape in self.items:
            if item_name == name:
                return shape
        known = ", ".join(repr(item_name) for item_name, _ in self.items) or "<none>"
        raise CompileError(f"Unknown raw node output {name!r}; declared outputs are: {known}")


SemanticResultShape: TypeAlias = RuntimeResultShape | ArrayResultShape | TupleResultShape | NamedOutputsResultShape


@dataclass(frozen=True)
class SemanticConstant:
    """Detached immutable runtime-materializable view of one compile-time value."""

    kind: str
    typ: NFType | None = None
    value: object | None = None
    items: tuple["SemanticConstant", ...] = ()

    def __post_init__(self):
        """Reject non-canonical runtime type identities when a type is present."""
        if self.typ is not None and not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType or None")


@dataclass(frozen=True)
class SemanticEnvironment:
    """Immutable semantic metadata snapshot used by one expression analysis."""

    runtime_bindings: Mapping[str, RuntimeBindingSymbol]
    legacy_binding_names: AbstractSet[str]
    constants: Mapping[str, SemanticConstant]
    const_eval_values: Mapping[str, object]
    reserved_name_labels: Mapping[str, str]
    callable_environment: CallableEnvironment


@dataclass(frozen=True)
class ResolvedName:
    """Describe how one reached source name resolved in the semantic frontend."""

    kind: str
    typ: NFType | None
    name: str | None = None
    value: object | None = None
    binding_id: BindingId | None = None

    def __post_init__(self):
        """Reject non-canonical runtime type identities when a type is present."""
        if self.typ is not None and not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType or None")


@dataclass(frozen=True)
class ExpressionFact:
    """Store normalized semantic facts and result shape for one AST expression."""

    result_shape: SemanticResultShape
    operation: str | None = None
    compare_operations: tuple[str, ...] = ()
    resolved_name: ResolvedName | None = None
    literal_value: object | None = None
    analyzed_call: AnalyzedCall | None = None
    call_operand_nodes: tuple[ast.expr, ...] = ()


@dataclass(frozen=True)
class ExpressionAnalysis:
    """Ephemeral AST-associated semantic facts for one fully owned expression."""

    root: ast.expr
    facts: Mapping[ast.AST, ExpressionFact]


@dataclass(frozen=True)
class _Unsupported:
    """Internal result indicating that this migration stage does not own a path."""


UNSUPPORTED = _Unsupported()


class _BuiltinOperandUnsupported(Exception):
    """Abort semantic call ownership when one runtime operand still requires legacy fallback."""



def _is_number_type(typ):
    """Return whether *typ* follows the existing scalar Math-node contract."""
    return typ in NUMERIC_NF_TYPES


def _require_runtime_type(fact, context):
    """Return a runtime type or reject a structural result in *context*."""
    shape = fact.result_shape
    if isinstance(shape, RuntimeResultShape):
        return shape.typ
    if context == "if-expression result" and isinstance(shape, ArrayResultShape):
        raise CompileError("if-expression cannot return arrays")
    if isinstance(shape, TupleResultShape):
        raise CompileError(
            f"{context} received a tuple of {len(shape.items)} values; unpack it or select an element by a compile-time index"
        )
    if isinstance(shape, NamedOutputsResultShape):
        raise CompileError(f"NodeResult is compile-time only and cannot be used in {context}")
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


def _call_result_shape(result):
    """Convert one normalized call result contract to semantic expression shape."""
    if isinstance(result, RuntimeCallResult):
        return RuntimeResultShape(result.typ)
    if isinstance(result, TupleCallResult):
        return TupleResultShape(tuple(RuntimeResultShape(typ) for typ in result.types))
    if isinstance(result, NamedOutputsCallResult):
        return NamedOutputsResultShape(
            tuple((name, RuntimeResultShape(typ)) for name, typ in result.items)
        )
    raise CompileError("Internal error: unsupported analyzed call result contract")


def _unregistered_keyword_error(name):
    """Create the exact legacy diagnostic for keywords on an unresolved call."""
    return CompileError(
        f"Keyword arguments are only supported for builtins, library functions, local functions or local backend helpers; {name} is not registered as one"
    )


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
            if isinstance(base.result_shape, NamedOutputsResultShape):
                return record(
                    node,
                    ExpressionFact(
                        base.result_shape.get(node.attr),
                        operation="named_output",
                        literal_value=node.attr,
                    ),
                )
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
            if isinstance(base.result_shape, NamedOutputsResultShape):
                try:
                    key = _const_eval(node.slice, environment.const_eval_values)
                except CompileError as exc:
                    raise CompileError("raw node output lookup requires a compile-time string key") from exc
                if not isinstance(key, str) or not key:
                    raise CompileError("raw node output lookup requires a non-empty string key")
                return record(
                    node,
                    ExpressionFact(
                        base.result_shape.get(key),
                        operation="named_output",
                        literal_value=key,
                    ),
                )
            if isinstance(base.result_shape, TupleResultShape):
                try:
                    index = _const_eval(node.slice, environment.const_eval_values)
                except CompileError as exc:
                    raise CompileError("tuple result indexing requires a compile-time integer index") from exc
                if not isinstance(index, int) or isinstance(index, bool):
                    raise CompileError("tuple result indexing requires a compile-time integer index")
                try:
                    selected_shape = base.result_shape.items[index]
                except IndexError as exc:
                    raise CompileError(
                        f"tuple result index {index} is out of range for {len(base.result_shape.items)} values"
                    ) from exc
                return record(node, ExpressionFact(selected_shape, operation="tuple_index", literal_value=index))
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

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr != "info":
                    raise CompileError("Object values support only the .info() method")
                receiver = analyze(node.func.value)
                if receiver is UNSUPPORTED:
                    return UNSUPPORTED
                if _require_runtime_type(receiver, ".info() receiver") != TYPE_OBJECT:
                    raise CompileError(".info() can only be used on Object values")
                if node.args:
                    raise CompileError("Object.info() accepts only keyword arguments")
                if any(kw.arg is None for kw in node.keywords):
                    raise CompileError("Object.info() does not support **kwargs")
                kws = {kw.arg: kw.value for kw in node.keywords}
                extra = set(kws) - {"transform_space", "as_instance"}
                if extra:
                    raise CompileError("Object.info() accepts only transform_space= and as_instance=")
                options = []
                if "transform_space" in kws:
                    try:
                        value = _const_eval(kws["transform_space"], environment.const_eval_values)
                    except CompileError as exc:
                        raise CompileError("Object.info() transform_space must be 'ORIGINAL' or 'RELATIVE'") from exc
                    if value not in {"ORIGINAL", "RELATIVE"}:
                        raise CompileError("Object.info() transform_space must be 'ORIGINAL' or 'RELATIVE'")
                    options.append(("transform_space", value))
                if "as_instance" in kws:
                    try:
                        value = _const_eval(kws["as_instance"], environment.const_eval_values)
                    except CompileError as exc:
                        raise CompileError("Object.info() as_instance must be a compile-time Bool") from exc
                    if not isinstance(value, bool):
                        raise CompileError("Object.info() as_instance must be a compile-time Bool")
                    options.append(("as_instance", value))
                target = ResolvedCallable(CallableKind.OBJECT_INFO, "Object.info", target="Object.info")
                analyzed_call = AnalyzedCall(
                    target=target,
                    runtime_operands=(AnalyzedCallOperand("receiver", TYPE_OBJECT),),
                    options=tuple(options),
                    result=RuntimeCallResult(TYPE_OBJECT),
                )
                return record(
                    node,
                    runtime_fact(
                        TYPE_OBJECT,
                        analyzed_call=analyzed_call,
                        call_operand_nodes=(node.func.value,),
                    ),
                )
            if not isinstance(node.func, ast.Name):
                raise CompileError("Only simple function calls are supported")
            name = node.func.id
            resolved = resolve_simple_callable(name, environment.callable_environment)
            cleaned_call, modifiers = extract_function_call_modifiers(
                node, name, environment.const_eval_values
            )
            if resolved is UNRESOLVED:
                if cleaned_call.keywords:
                    raise _unregistered_keyword_error(name)
                if modifiers.unique_was_explicit:
                    raise unsupported_unique(name)
                raise CompileError(f"Unsupported function: {name}")
            if resolved.kind is CallableKind.TOP_LEVEL_ONLY:
                if cleaned_call.keywords:
                    raise _unregistered_keyword_error(name)
                raise CompileError(f"{name}() is only supported as a top-level call")
            if resolved.kind is CallableKind.BUILTIN:
                if modifiers.unique_was_explicit:
                    raise unsupported_unique(name)
                if name in STATEFUL_FALLBACK_BUILTIN_NAMES:
                    # SEMANTIC_CALL_IR_STATEFUL_BUILTIN_FALLBACK: grid/grid_uv and input_* still depend on
                    # compilation-scoped mutable Compiler state across expression boundaries: grid UV context,
                    # interface socket reuse/registration/default metadata, and publication into comp.vars.
                    # Keep these calls on legacy realization rather than introducing a temporary lowering
                    # session or passing Compiler through the IR backend boundary. Remove this fallback when
                    # frontend-owned runtime bindings/session state permanently owns those effects.
                    return UNSUPPORTED
                if name not in IR_CAPABLE_BUILTIN_NAMES:
                    raise CompileError(f"Internal error: unclassified callable builtin {name!r}")
                runtime_nodes = []

                def add_runtime(child, parameter_name, context):
                    child_fact = analyze(child)
                    if child_fact is UNSUPPORTED:
                        raise _BuiltinOperandUnsupported
                    typ = _require_runtime_type(child_fact, context)
                    runtime_nodes.append(child)
                    return typ

                try:
                    builtin = analyze_builtin_call(
                        name, cleaned_call, environment.const_eval_values, add_runtime
                    )
                except _BuiltinOperandUnsupported:
                    return UNSUPPORTED
                analyzed_call = AnalyzedCall(
                    target=resolved,
                    runtime_operands=builtin.operands,
                    options=builtin.options,
                    result=builtin.result,
                )
                return record(
                    node,
                    ExpressionFact(
                        _call_result_shape(builtin.result),
                        analyzed_call=analyzed_call,
                        call_operand_nodes=tuple(runtime_nodes),
                    ),
                )
            if resolved.kind in {
                CallableKind.SYSTEM,
                CallableKind.LOCAL_FUNCTION,
                CallableKind.BACKEND_HELPER,
                CallableKind.LIBRARY,
            }:
                if resolved.kind in {CallableKind.SYSTEM, CallableKind.BACKEND_HELPER} and modifiers.unique_was_explicit:
                    raise unsupported_unique(name)
                # SEMANTIC_CALL_IR_DYNAMIC_FALLBACK: Callable identity is resolved here, but local,
                # imported, system, backend-helper, and native-Python calls do not yet expose a complete
                # Blender-independent result signature. Keep the enclosing expression on the legacy
                # realization path instead of inventing unknown types, opaque IR, or semantic-time Blender
                # probes. Remove this fallback when each remaining callable category has a compiler-owned
                # typed signature/result contract and can emit AST-free Call IR before materialization.
                return UNSUPPORTED
            raise CompileError(f"Internal error: unsupported resolved callable category {resolved.kind}")

        return UNSUPPORTED

    result = analyze(expr)
    if result is UNSUPPORTED:
        return None
    return ExpressionAnalysis(expr, MappingProxyType(dict(facts)))


__all__ = [
    "RuntimeBindingSymbol",
    "RuntimeResultShape",
    "ArrayResultShape",
    "NamedOutputsResultShape",
    "TupleResultShape",
    "SemanticResultShape",
    "SemanticConstant",
    "SemanticEnvironment",
    "ResolvedName",
    "ExpressionFact",
    "ExpressionAnalysis",
    "build_semantic_constant_snapshot",
    "analyze_expression",
]
