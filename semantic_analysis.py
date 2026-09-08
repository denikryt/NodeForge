"""Pure Python semantic resolution and type checking for migrated expressions."""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import AbstractSet, Mapping

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
    INPUT_DECLARATION_BUILTIN_NAMES,
    INPUT_DECLARATION_PLACEMENT_ERROR,
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
from .runtime_bindings import RuntimeBindingSymbol
from .semantic_values import (
    ArrayResultShape,
    NamedOutputsResultShape,
    ObjectInfoState,
    ObjectSemanticId,
    ObjectSemanticSnapshot,
    RuntimeResultShape,
    SemanticResultShape,
    StructuralBindingSymbol,
    TupleResultShape,
)


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
    structural_bindings: Mapping[str, StructuralBindingSymbol] = field(default_factory=lambda: MappingProxyType({}))
    object_semantics: ObjectSemanticSnapshot | None = None


@dataclass(frozen=True)
class ResolvedName:
    """Describe how one reached source name resolved in the semantic frontend."""

    kind: str
    typ: NFType | None
    name: str | None = None
    value: object | None = None
    binding_id: BindingId | None = None
    structural_binding: StructuralBindingSymbol | None = None

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
    object_info_state: ObjectInfoState | None = None


@dataclass(frozen=True)
class ExpressionAnalysis:
    """Ephemeral AST-associated semantic facts plus detached Object semantic state."""

    root: ast.expr
    facts: Mapping[ast.AST, ExpressionFact]
    object_semantics: ObjectSemanticSnapshot | None = None


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


def _call_result_types(result):
    """Return runtime leaf types from one normalized call result contract."""
    if isinstance(result, RuntimeCallResult):
        return (result.typ,)
    if isinstance(result, TupleCallResult):
        return tuple(result.types)
    if isinstance(result, NamedOutputsCallResult):
        return tuple(typ for _name, typ in result.items)
    raise CompileError("Internal error: unsupported analyzed call result contract")


def _unregistered_keyword_error(name):
    """Create the exact legacy diagnostic for keywords on an unresolved call."""
    return CompileError(
        f"Keyword arguments are only supported for builtins, library functions, local functions or local backend helpers; {name} is not registered as one"
    )




def build_semantic_environment(
    *,
    runtime_bindings,
    legacy_binding_names,
    constants,
    reserved_name_labels,
    callable_environment,
    structural_bindings=None,
    object_semantics=None,
):
    """Build one immutable expression environment from detached compiler-owned snapshots."""
    structural_bindings = {} if structural_bindings is None else dict(structural_bindings)
    runtime_bindings = dict(runtime_bindings)
    overlap = set(runtime_bindings) & set(structural_bindings)
    if overlap:
        raise CompileError("Internal error: source name is both runtime and structural binding")
    if object_semantics is not None and not isinstance(object_semantics, ObjectSemanticSnapshot):
        raise TypeError("object_semantics must be an ObjectSemanticSnapshot or None")
    if object_semantics is not None:
        object_ids = object_semantics.object_ids_by_binding
        object_states = object_semantics.states
        active_types = {symbol.binding_id: symbol.typ for symbol in runtime_bindings.values()}
        for structural in structural_bindings.values():
            for leaf in structural.leaves:
                active_types[leaf.binding_id] = leaf.typ
        for binding_id, typ in active_types.items():
            if typ is NFType.OBJECT:
                object_id = object_ids.get(binding_id)
                if not isinstance(object_id, ObjectSemanticId):
                    raise CompileError("Internal error: active Object binding has no ObjectSemanticId")
                if object_id not in object_states:
                    raise CompileError("Internal error: active Object binding references missing ObjectInfoState")
            elif binding_id in object_ids:
                raise CompileError("Internal error: non-Object binding has ObjectSemanticId")
    semantic_constants, const_eval_values = build_semantic_constant_snapshot(constants)
    return SemanticEnvironment(
        runtime_bindings=MappingProxyType(runtime_bindings),
        legacy_binding_names=frozenset(legacy_binding_names),
        constants=semantic_constants,
        const_eval_values=const_eval_values,
        reserved_name_labels=MappingProxyType(dict(reserved_name_labels)),
        callable_environment=callable_environment,
        structural_bindings=MappingProxyType(structural_bindings),
        object_semantics=object_semantics,
    )


def analyze_expression(expr, environment):
    """Resolve and type-check one complete expression with detached semantic state."""
    facts = {}
    if not isinstance(environment, SemanticEnvironment):
        raise TypeError("environment must be a SemanticEnvironment")
    if environment.object_semantics is None:
        object_ids_by_binding = None
        object_states = None
        next_object_id = None
    else:
        object_ids_by_binding = dict(environment.object_semantics.object_ids_by_binding)
        object_states = dict(environment.object_semantics.states)
        next_object_id = environment.object_semantics.next_object_id

    def has_body_object_semantics():
        """Return whether this expression runs with persistent body-owned Object semantics."""
        # STRUCTURAL_SEMANTICS_LEGACY_OBJECT_EXPRESSION_FALLBACK: Object semantics are stateful across
        # expressions. Only lower_basic_body() supplies the persistent frontend Object registry required
        # to own Object identity, Object.info() configuration, aliases, and post-resolution locking. When
        # expression-only semantic analysis runs from the legacy compile_expr() path without that registry,
        # reject Object bindings/results, Object.info(), Object properties, and Object-preserving projections
        # as UNSUPPORTED so compile_expr() reaches the existing ObjectValue legacy implementation. Remove
        # this fallback only when legacy body compilation no longer routes Object expressions through
        # compile_expr(), or every remaining legacy body route has an explicitly planned persistent frontend
        # Object semantic context with one unambiguous owner.
        return object_ids_by_binding is not None and object_states is not None

    def allocate_object_id():
        nonlocal next_object_id
        if object_ids_by_binding is None or object_states is None or next_object_id is None:
            raise CompileError("Internal error: Object identity allocation requires body-owned semantic state")
        object_id = ObjectSemanticId(next_object_id)
        next_object_id += 1
        object_states[object_id] = ObjectInfoState()
        return object_id

    def record(node, fact):
        facts[node] = fact
        return fact

    def runtime_fact(typ, *, object_id=None, **kwargs):
        if typ is NFType.OBJECT and object_id is None:
            raise CompileError("Internal error: Object runtime fact requires ObjectSemanticId")
        return ExpressionFact(RuntimeResultShape(typ, object_id), **kwargs)

    def call_result_shape(result):
        types = _call_result_types(result)
        if any(typ is NFType.OBJECT for typ in types) and object_ids_by_binding is None:
            return UNSUPPORTED
        shapes = tuple(
            RuntimeResultShape(typ, allocate_object_id() if typ is NFType.OBJECT else None)
            for typ in types
        )
        if isinstance(result, RuntimeCallResult):
            return shapes[0]
        if isinstance(result, TupleCallResult):
            return TupleResultShape(shapes)
        if isinstance(result, NamedOutputsCallResult):
            return NamedOutputsResultShape(tuple((name, shape) for (name, _), shape in zip(result.items, shapes)))
        raise CompileError("Internal error: unsupported analyzed call result contract")

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
                if symbol.typ is NFType.OBJECT:
                    if not has_body_object_semantics():
                        return UNSUPPORTED
                    object_id = object_ids_by_binding.get(symbol.binding_id)
                    if not isinstance(object_id, ObjectSemanticId):
                        raise CompileError("Internal error: Object runtime binding has no ObjectSemanticId")
                else:
                    object_id = None
                resolved = ResolvedName(
                    "runtime_binding",
                    symbol.typ,
                    name=node.id,
                    binding_id=symbol.binding_id,
                )
                return record(node, runtime_fact(symbol.typ, object_id=object_id, resolved_name=resolved))
            if node.id in environment.structural_bindings:
                structural = environment.structural_bindings[node.id]
                if object_ids_by_binding is None and any(leaf.typ is NFType.OBJECT for leaf in structural.leaves):
                    return UNSUPPORTED
                shape = structural.result_shape({} if object_ids_by_binding is None else object_ids_by_binding)
                resolved = ResolvedName(
                    "structural_binding",
                    None,
                    name=node.id,
                    structural_binding=structural,
                )
                return record(node, ExpressionFact(shape, resolved_name=resolved))
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
                shape = base.result_shape.get(node.attr)
                resolved_name = None
                structural = base.resolved_name.structural_binding if base.resolved_name is not None else None
                if structural is not None:
                    leaf = structural.leaf_by_name(node.attr)
                    resolved_name = ResolvedName(
                        "runtime_binding",
                        leaf.typ,
                        name=base.resolved_name.name,
                        binding_id=leaf.binding_id,
                    )
                return record(
                    node,
                    ExpressionFact(
                        shape,
                        operation="named_output",
                        literal_value=node.attr,
                        resolved_name=resolved_name,
                    ),
                )
            base_typ = _require_runtime_type(base, "attribute access")
            if base_typ == TYPE_OBJECT:
                if not has_body_object_semantics():
                    return UNSUPPORTED
                if node.attr not in OBJECT_PROPERTY_TYPES:
                    raise CompileError("Object values support only .geometry, .location, .rotation and .scale")
                object_id = base.result_shape.object_id
                if not isinstance(object_id, ObjectSemanticId):
                    raise CompileError("Internal error: Object property receiver has no ObjectSemanticId")
                state = object_states.get(object_id)
                if not isinstance(state, ObjectInfoState):
                    raise CompileError("Internal error: Object property receiver has no ObjectInfoState")
                property_state = state
                object_states[object_id] = ObjectInfoState(
                    transform_space=state.transform_space,
                    as_instance=state.as_instance,
                    resolved=True,
                )
                return record(
                    node,
                    runtime_fact(
                        OBJECT_PROPERTY_TYPES[node.attr],
                        operation=node.attr,
                        object_info_state=property_state,
                    ),
                )
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
                shape = base.result_shape.get(key)
                resolved_name = None
                structural = base.resolved_name.structural_binding if base.resolved_name is not None else None
                if structural is not None:
                    leaf = structural.leaf_by_name(key)
                    resolved_name = ResolvedName(
                        "runtime_binding",
                        leaf.typ,
                        name=base.resolved_name.name,
                        binding_id=leaf.binding_id,
                    )
                return record(
                    node,
                    ExpressionFact(
                        shape,
                        operation="named_output",
                        literal_value=key,
                        resolved_name=resolved_name,
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
                resolved_name = None
                structural = base.resolved_name.structural_binding if base.resolved_name is not None else None
                if structural is not None:
                    leaf = structural.leaf_by_index(index)
                    resolved_name = ResolvedName(
                        "runtime_binding",
                        leaf.typ,
                        name=base.resolved_name.name,
                        binding_id=leaf.binding_id,
                    )
                return record(
                    node,
                    ExpressionFact(
                        selected_shape,
                        operation="tuple_index",
                        literal_value=index,
                        resolved_name=resolved_name,
                    ),
                )
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
                if not has_body_object_semantics():
                    return UNSUPPORTED
                receiver = analyze(node.func.value)
                if receiver is UNSUPPORTED:
                    return UNSUPPORTED
                if _require_runtime_type(receiver, ".info() receiver") != TYPE_OBJECT:
                    raise CompileError(".info() can only be used on Object values")
                object_id = receiver.result_shape.object_id
                if not isinstance(object_id, ObjectSemanticId):
                    raise CompileError("Internal error: Object.info receiver has no ObjectSemanticId")
                state = object_states.get(object_id)
                if not isinstance(state, ObjectInfoState):
                    raise CompileError("Internal error: Object.info receiver has no ObjectInfoState")
                if state.resolved:
                    raise CompileError("Object.info() cannot be changed after Object Info has been resolved")
                if node.args:
                    raise CompileError("Object.info() accepts only keyword arguments")
                if any(kw.arg is None for kw in node.keywords):
                    raise CompileError("Object.info() does not support **kwargs")
                kws = {kw.arg: kw.value for kw in node.keywords}
                extra = set(kws) - {"transform_space", "as_instance"}
                if extra:
                    raise CompileError("Object.info() accepts only transform_space= and as_instance=")
                transform_space = state.transform_space
                as_instance = state.as_instance
                if "transform_space" in kws:
                    try:
                        transform_space = _const_eval(kws["transform_space"], environment.const_eval_values)
                    except CompileError as exc:
                        raise CompileError("Object.info() transform_space must be 'ORIGINAL' or 'RELATIVE'") from exc
                    if transform_space not in {"ORIGINAL", "RELATIVE"}:
                        raise CompileError("Object.info() transform_space must be 'ORIGINAL' or 'RELATIVE'")
                if "as_instance" in kws:
                    try:
                        as_instance = _const_eval(kws["as_instance"], environment.const_eval_values)
                    except CompileError as exc:
                        raise CompileError("Object.info() as_instance must be a compile-time Bool") from exc
                    if not isinstance(as_instance, bool):
                        raise CompileError("Object.info() as_instance must be a compile-time Bool")
                new_state = ObjectInfoState(transform_space=transform_space, as_instance=as_instance, resolved=False)
                object_states[object_id] = new_state
                target = ResolvedCallable(CallableKind.OBJECT_INFO, "Object.info", target="Object.info")
                analyzed_call = AnalyzedCall(
                    target=target,
                    runtime_operands=(AnalyzedCallOperand("receiver", TYPE_OBJECT),),
                    options=(),
                    result=RuntimeCallResult(TYPE_OBJECT),
                )
                return record(
                    node,
                    runtime_fact(
                        TYPE_OBJECT,
                        object_id=object_id,
                        analyzed_call=analyzed_call,
                        call_operand_nodes=(node.func.value,),
                        object_info_state=new_state,
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
                if name in INPUT_DECLARATION_BUILTIN_NAMES:
                    raise CompileError(INPUT_DECLARATION_PLACEMENT_ERROR)
                if name in STATEFUL_FALLBACK_BUILTIN_NAMES:
                    # BASIC_BODY_IR_REMAINING_STATEFUL_CALL_FALLBACK: grid/grid_uv still depend on
                    # compilation-scoped state. Keep those calls on whole-body legacy fallback until
                    # they have permanent compiler-owned semantic operations; input_* is declaration-only
                    # language syntax and is rejected before reaching this fallback.
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
                result_shape = call_result_shape(builtin.result)
                if result_shape is UNSUPPORTED:
                    return UNSUPPORTED
                return record(
                    node,
                    ExpressionFact(
                        result_shape,
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
                # STRUCTURAL_SEMANTICS_DYNAMIC_RESULT_FALLBACK: Fixed tuple/named-output structures are frontend-owned
                # only when the resolved callable already provides a typed semantic result contract. Local/imported/
                # system/backend/native call categories that still derive result shape or behavior from dynamic
                # compilation/execution remain whole-expression/body fallback. Do not probe Blender interfaces,
                # execute Python helpers, or invent opaque tuple signatures during semantic analysis. Remove this
                # fallback when those callable categories expose compiler-owned typed result/signature contracts.
                return UNSUPPORTED
            raise CompileError(f"Internal error: unsupported resolved callable category {resolved.kind}")

        return UNSUPPORTED

    result = analyze(expr)
    if result is UNSUPPORTED:
        return None
    if object_ids_by_binding is None:
        snapshot = None
    else:
        snapshot = ObjectSemanticSnapshot(object_ids_by_binding, object_states, next_object_id)
    return ExpressionAnalysis(expr, MappingProxyType(dict(facts)), snapshot)


__all__ = [
    "RuntimeBindingSymbol",
    "SemanticConstant",
    "SemanticEnvironment",
    "ResolvedName",
    "ExpressionFact",
    "ExpressionAnalysis",
    "build_semantic_constant_snapshot",
    "build_semantic_environment",
    "analyze_expression",
]
