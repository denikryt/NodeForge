"""Pure Python semantic resolution and type checking for migrated expressions."""

from __future__ import annotations

import ast
import copy
import inspect
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Mapping

from .compiler_identities import (
    BindingId,
    GroupCompilationIdentity,
    local_function_id,
)
from .call_resolution import (
    AnalyzedCall,
    AnalyzedCallOperand,
    AnalyzedStaticCallOperand,
    CallableEnvironment,
    CallableKind,
    ContextReadCallResult,
    NamedOutputsCallResult,
    ProjectedCallResult,
    ResolvedCallable,
    RuntimeCallResult,
    TupleCallResult,
    UNRESOLVED,
    non_callable_source_binding_error,
    package_candidates_for_unqualified,
    resolve_package_callable,
    resolve_simple_callable,
)
from .builtin_call_semantics import (
    IR_CAPABLE_BUILTIN_NAMES,
    INPUT_DECLARATION_BUILTIN_NAMES,
    INPUT_DECLARATION_PLACEMENT_ERROR,
    analyze_builtin_call,
)
from .function_instances import (
    extract_function_call_modifiers,
    function_group_owner_scope,
    function_materialization_owner_scope,
    unsupported_unique,
)
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
from .consteval import NOT_FOLDABLE, ConstEvalUnavailable, _const_eval, _is_const_vector, try_runtime_fold
from .compile_time import CompileTimeSnapshot, ConstVector
from .errors import CompileError
from .nf_types import NFType, NUMERIC_NF_TYPES
from .numeric_semantics import (
    INT_MIN,
    normalize_float_constant,
    normalize_int_constant,
    resolve_numeric_binary,
    resolve_numeric_unary_minus,
)
from .runtime_bindings import RuntimeBindingSymbol
from .group_context import GroupContextSlot
from .callable_contracts import (
    bind_imported_source_arguments,
    bind_local_source_arguments,
    canonicalize_group_input_default,
    source_argument_type_matches,
)
from .semantic_ir import IRFunctionMaterialization, IRFunctionMaterializationMode
from .evaluation_modes import CompileTimeSelection, EvaluationMode, RuntimeRequired, resolve_argument_evaluation
from .extension_contracts import (
    ExtensionCallableSpec,
    ExtensionParameterSpec,
    TypeSpec,
    is_frontend_semantic_type_spec,
)
from .extension_semantics import (
    ExtensionDependencySource,
    ExtensionExecutionForm,
    ExtensionSemanticPayload,
    SemanticInvocation,
    compact_extension_semantic_payload,
    classify_extension_execution,
    semantic_type_compatible,
)
from .extension_values import (
    ExtensionValue,
    select_declared_nf_type,
    static_nf_type,
    type_spec_accepts_nf,
)
from .source_callables import (
    analyze_local_captures,
    analyze_local_return_shape,
    local_binding_names,
    local_function_source,
    resolve_local_parameter_annotation,
    serialize_local_signature,
    has_source_value_binding,
)
from .semantic_values import (
    ArrayResultShape,
    ExtensionResultShape,
    NamedOutputsResultShape,
    ObjectInfoState,
    ObjectSemanticId,
    ObjectSemanticSnapshot,
    RuntimeResultShape,
    SemanticResultShape,
    StructuralArrayId,
    StructuralArrayRef,
    StructuralArraySnapshot,
    StructuralBindingSymbol,
    StructuralRuntimeLeaf,
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
    constants: Mapping[str, SemanticConstant]
    const_eval_values: Mapping[str, object]
    reserved_name_labels: Mapping[str, str]
    callable_environment: CallableEnvironment
    structural_bindings: Mapping[str, StructuralBindingSymbol] = field(default_factory=lambda: MappingProxyType({}))
    structural_arrays: StructuralArraySnapshot = field(default_factory=lambda: StructuralArraySnapshot({}, {}))
    builder_bindings: Mapping[str, BindingId] = field(default_factory=lambda: MappingProxyType({}))
    extension_bindings: Mapping[str, ExtensionSemanticPayload] = field(default_factory=lambda: MappingProxyType({}))
    object_semantics: ObjectSemanticSnapshot | None = None
    available_group_context_slots: frozenset[GroupContextSlot] = frozenset()
    source_callable_session: object | None = None
    source_definition_owner: str | None = None
    source_owner_scope: str | None = None
    source_call_site_allocator: object | None = None
    helper_namespace: str = "Group"
    extension_registry: object | None = None


@dataclass(frozen=True)
class ResolvedName:
    """Describe how one reached source name resolved in the semantic frontend."""

    kind: str
    typ: NFType | None
    name: str | None = None
    value: object | None = None
    binding_id: BindingId | None = None
    structural_binding: StructuralBindingSymbol | None = None
    array_id: StructuralArrayId | None = None

    def __post_init__(self):
        """Reject inconsistent canonical source-name resolution metadata."""
        if self.typ is not None and not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType or None")
        if self.kind == "structural_array":
            if self.typ is not None or not isinstance(self.array_id, StructuralArrayId):
                raise ValueError("structural_array resolution requires array_id and no runtime type")
            if self.binding_id is not None or self.structural_binding is not None:
                raise ValueError("structural_array resolution cannot carry runtime/fixed structural binding metadata")
        elif self.array_id is not None:
            raise ValueError("array_id is valid only for structural_array resolution")


@dataclass(frozen=True)
class ExpressionFact:
    """Store normalized semantic facts and result shape for one AST expression."""

    result_shape: SemanticResultShape
    operation: str | None = None
    compare_operations: tuple[str, ...] = ()
    resolved_name: ResolvedName | None = None
    literal_value: object | None = None
    analyzed_call: AnalyzedCall | None = None
    call_operand_nodes: tuple[ast.expr | BindingId, ...] = ()
    object_info_state: ObjectInfoState | None = None
    array_id: StructuralArrayId | None = None
    semantic_payload: ExtensionSemanticPayload | None = None


@dataclass(frozen=True)
class ExpressionAnalysis:
    """Ephemeral AST-associated semantic facts plus detached frontend semantic snapshots."""

    root: ast.expr
    facts: Mapping[ast.AST, ExpressionFact]
    object_semantics: ObjectSemanticSnapshot | None = None
    available_group_context_slots: frozenset[GroupContextSlot] = frozenset()
    source_callable_session: object | None = None
    source_definition_owner: str | None = None
    source_owner_scope: str | None = None
    source_call_site_allocator: object | None = None
    helper_namespace: str = "Group"
    structural_arrays: StructuralArraySnapshot = field(default_factory=lambda: StructuralArraySnapshot({}, {}))
    used_extension_owners: frozenset[tuple[str, ...]] = frozenset()
    extension_registry: object | None = None



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
        raise CompileError(f"Named multi-output results are structural and cannot be used in {context}")
    raise CompileError(f"{context} requires a runtime value")


def _is_array_result(fact):
    """Return whether one analyzed expression yields compiler-structural array data."""
    return isinstance(fact.result_shape, ArrayResultShape)


def _validate_known_int_domain(expr, environment):
    """Reject hard Int-domain errors when one typed numeric expression is statically known.

    This is semantic validation only. A successful compile-time evaluation does not
    replace the runtime expression or authorize graph folding.
    """
    try:
        _const_eval(expr, environment.const_eval_values)
    except ConstEvalUnavailable:
        return


def _validate_binary(op_type, left_typ, right_typ):
    """Return the type-directed result for one semantically valid binary operation."""
    operation = _BIN_OPS.get(op_type)
    if operation is None:
        raise CompileError(f"Unsupported binary operator: {op_type.__name__}")
    numeric_result = resolve_numeric_binary(operation, left_typ, right_typ)
    if numeric_result is not None:
        return numeric_result
    if op_type in {ast.Add, ast.Sub} and left_typ == TYPE_VECTOR and right_typ == TYPE_VECTOR:
        return TYPE_VECTOR
    if op_type is ast.Mult:
        if left_typ == TYPE_VECTOR and _is_number_type(right_typ):
            return TYPE_VECTOR
        if _is_number_type(left_typ) and right_typ == TYPE_VECTOR:
            return TYPE_VECTOR
        if left_typ == TYPE_VECTOR and right_typ == TYPE_VECTOR:
            return TYPE_VECTOR
    if op_type is ast.Div and left_typ == TYPE_VECTOR and _is_number_type(right_typ):
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
    if type(value) is bool:
        return SemanticConstant("scalar", TYPE_BOOL, value), False
    if type(value) is int:
        return SemanticConstant("scalar", TYPE_INT, normalize_int_constant(value)), False
    if type(value) is float:
        return SemanticConstant("scalar", TYPE_FLOAT, normalize_float_constant(value)), False
    if isinstance(value, str):
        return SemanticConstant("scalar", TYPE_STRING, value), False
    if _is_const_vector(value) or (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    ):
        return (
            SemanticConstant(
                "vector",
                TYPE_VECTOR,
                tuple(normalize_float_constant(item) for item in value),
            ),
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
        memo[id(value)] = tuple(normalize_float_constant(item) for item in value)
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


def build_semantic_constant_snapshot(compile_time: CompileTimeSnapshot):
    """Build detached semantic and const-eval mappings from one compile-time snapshot."""
    if not isinstance(compile_time, CompileTimeSnapshot):
        raise TypeError("compile_time must be a CompileTimeSnapshot")
    values = compile_time.values
    semantic = {
        name: _normalize_semantic_constant(value)
        for name, value in values.items()
    }
    const_eval_values = _detached_const_eval_mapping(values)
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
    if isinstance(result, ProjectedCallResult):
        return tuple(result.types)
    if isinstance(result, ContextReadCallResult):
        return (result.typ,)
    raise CompileError("Internal error: unsupported analyzed call result contract")




def _extension_result_spec(spec: ExtensionCallableSpec):
    """Convert one exact extension result TypeSpec to the existing analyzed call shape."""
    if spec.result.kind == "NF_SET":
        return RuntimeCallResult(next(iter(spec.result.nf_types)))
    return TupleCallResult(tuple(next(iter(item.nf_types)) for item in spec.result.items))


def _extension_explicit_modes(spec: ExtensionCallableSpec, bound: inspect.BoundArguments) -> dict[int, EvaluationMode]:
    """Map explicit source AST occurrence identity to the candidate-required evaluation mode."""
    modes: dict[int, EvaluationMode] = {}
    by_name = {parameter.name: parameter for parameter in spec.parameters}
    for name, value in bound.arguments.items():
        parameter = by_name[name]
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            for item in value:
                if isinstance(item, ast.AST):
                    modes[id(item)] = parameter.evaluation_mode
        elif isinstance(value, ast.AST):
            modes[id(value)] = parameter.evaluation_mode
    return modes


def _analyze_extension_backend_call(
    node: ast.Call,
    cleaned_call: ast.Call,
    resolved: ResolvedCallable,
    modifiers,
    environment: SemanticEnvironment,
    analyze,
    facts,
    call_result_shape,
):
    """Preserve the reviewed backend-only extension acquisition/overload path."""
    name = resolved.source_name
    if modifiers.unique_was_explicit:
        raise unsupported_unique(name)
    if any(isinstance(argument, ast.Starred) for argument in cleaned_call.args):
        raise CompileError(f"{name}() does not support caller-side * argument expansion")
    if any(keyword.arg is None for keyword in cleaned_call.keywords):
        raise CompileError(f"{name}() does not support caller-side ** argument expansion")
    seen_keywords: set[str] = set()
    keyword_values: dict[str, ast.expr] = {}
    for keyword in cleaned_call.keywords:
        if keyword.arg in seen_keywords:
            raise CompileError(f"{name}() got multiple values for keyword argument {keyword.arg!r}")
        seen_keywords.add(keyword.arg)
        keyword_values[keyword.arg] = keyword.value

    registry = environment.extension_registry
    if registry is None:
        raise CompileError("Internal error: extension call has no immutable extension registry")
    family = registry.callable_specs(resolved.target)
    candidates: list[tuple[int, ExtensionCallableSpec, inspect.BoundArguments]] = []
    for index, spec in enumerate(family):
        signature = spec.python_signature()
        try:
            bound = signature.bind(*cleaned_call.args, **keyword_values)
        except TypeError:
            continue
        bound.apply_defaults()
        candidates.append((index, spec, bound))
    if not candidates:
        raise CompileError(f"{name}() arguments do not match any declared extension signature")

    explicit_nodes = tuple(cleaned_call.args) + tuple(keyword.value for keyword in cleaned_call.keywords)
    candidate_modes = [_extension_explicit_modes(spec, bound) for _idx, spec, bound in candidates]
    required_modes: dict[int, EvaluationMode] = {}
    for source_node in explicit_nodes:
        modes = {mapping.get(id(source_node)) for mapping in candidate_modes}
        if None in modes:
            raise CompileError("Internal error: syntactically bound extension candidate lost source argument")
        if len(modes) != 1:
            raise CompileError(
                f"{name}() overload candidates require conflicting EvaluationMode for one source argument"
            )
        required_modes[id(source_node)] = next(iter(modes))

    # Each explicit source occurrence is acquired exactly once before type-based
    # candidate selection. Defaults are already detached contract data.
    acquired: dict[int, tuple[str, object, NFType]] = {}
    for source_node in explicit_nodes:
        mode = required_modes[id(source_node)]
        try:
            selection = resolve_argument_evaluation(source_node, environment.const_eval_values, mode)
        except ConstEvalUnavailable as exc:
            raise CompileError(f"{name}() argument must be available at compile time") from exc
        if isinstance(selection, CompileTimeSelection):
            actual_type = static_nf_type(selection.value)
            acquired[id(source_node)] = ("static", selection.value, actual_type)
            continue
        if not isinstance(selection, RuntimeRequired):
            raise CompileError("Internal error: extension evaluation selector returned unsupported state")
        fact = analyze(source_node)
        actual_type = _require_runtime_type(fact, f"{name}() argument")
        acquired[id(source_node)] = ("runtime", source_node, actual_type)

    def candidate_matches(item, *, exact: bool) -> bool:
        """Return whether one bound overload candidate accepts all acquired types."""
        _index, spec, bound = item
        parameters = {parameter.name: parameter for parameter in spec.parameters}
        for parameter_name, value in bound.arguments.items():
            parameter = parameters[parameter_name]
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                occurrences = tuple(value)
            else:
                occurrences = (value,)
            for occurrence in occurrences:
                if isinstance(occurrence, ast.AST):
                    actual_type = acquired[id(occurrence)][2]
                else:
                    if parameter.default_type is None:
                        raise CompileError("Internal error: extension bound default lost canonical type")
                    actual_type = parameter.default_type
                if not type_spec_accepts_nf(parameter.type_spec, actual_type, exact=exact):
                    return False
        return True

    exact_candidates = [item for item in candidates if candidate_matches(item, exact=True)]
    if len(exact_candidates) == 1:
        selected = exact_candidates[0]
    elif len(exact_candidates) > 1:
        raise CompileError(f"{name}() call is ambiguous between multiple exact extension overloads")
    else:
        compatible = [item for item in candidates if candidate_matches(item, exact=False)]
        if len(compatible) != 1:
            if not compatible:
                raise CompileError(f"{name}() argument types do not match any declared extension overload")
            raise CompileError(f"{name}() call is ambiguous between multiple compatible extension overloads")
        selected = compatible[0]

    selected_index, selected_spec, selected_bound = selected
    runtime_nodes: list[ast.expr] = []
    runtime_operands: list[AnalyzedCallOperand] = []
    static_operands: list[AnalyzedStaticCallOperand] = []
    parameter_by_name = {parameter.name: parameter for parameter in selected_spec.parameters}
    for parameter_index, parameter in enumerate(selected_spec.parameters):
        value = selected_bound.arguments.get(parameter.name, ()) if parameter.kind is inspect.Parameter.VAR_POSITIONAL else selected_bound.arguments.get(parameter.name)
        occurrences = tuple(value) if parameter.kind is inspect.Parameter.VAR_POSITIONAL else (value,)
        for variadic_index, occurrence in enumerate(occurrences):
            if occurrence is None and parameter.kind is not inspect.Parameter.VAR_POSITIONAL:
                raise CompileError("Internal error: selected extension binding lost fixed parameter")
            transport_variadic = variadic_index if parameter.kind is inspect.Parameter.VAR_POSITIONAL else None
            if isinstance(occurrence, ast.AST):
                representation, payload, actual_type = acquired[id(occurrence)]
                if representation == "runtime":
                    runtime_nodes.append(occurrence)
                    runtime_operands.append(
                        AnalyzedCallOperand(parameter.name, actual_type, parameter_index, transport_variadic)
                    )
                else:
                    expected_type = select_declared_nf_type(parameter.type_spec, actual_type)
                    canonical = canonicalize_group_input_default(expected_type, payload)
                    static_operands.append(
                        AnalyzedStaticCallOperand(parameter_index, canonical, transport_variadic)
                    )
            else:
                if parameter.default_type is None:
                    raise CompileError("Internal error: selected extension default has no canonical type")
                static_operands.append(
                    AnalyzedStaticCallOperand(parameter_index, occurrence, transport_variadic)
                )

    overload_index = selected_index if len(family) > 1 else None
    result_spec = _extension_result_spec(selected_spec)
    analyzed_call = AnalyzedCall(
        target=resolved,
        runtime_operands=tuple(runtime_operands),
        options=(),
        result=result_spec,
        static_operands=tuple(static_operands),
        extension_overload_index=overload_index,
    )
    return ExpressionFact(
        call_result_shape(result_spec),
        analyzed_call=analyzed_call,
        call_operand_nodes=tuple(runtime_nodes),
    )



def _semantic_result_shape(type_spec: TypeSpec, stored: object) -> SemanticResultShape:
    """Describe the concrete expression-local shape of one packed semantic result."""
    if type_spec.kind == "RECORD":
        if not isinstance(stored, ExtensionValue):
            raise CompileError("Internal error: semantic RECORD result did not pack to ExtensionValue")
        return ExtensionResultShape(stored.type_id)
    if type_spec.kind == "LIST":
        if not isinstance(stored, tuple):
            raise CompileError("Internal error: semantic LIST result did not pack to immutable tuple storage")
        return ArrayResultShape(tuple(_semantic_result_shape(type_spec.item, item) for item in stored))
    raise CompileError("Internal error: non-semantic result requested semantic result shape")


def _common_semantic_list_item_type_spec(
    type_specs: tuple[TypeSpec, ...],
    registry,
) -> TypeSpec:
    """Infer the common nominal RECORD TypeSpec for one non-empty semantic list literal."""
    if not type_specs:
        raise ValueError("semantic list type inference requires at least one TypeSpec")
    if any(type_spec.kind != "RECORD" for type_spec in type_specs):
        raise CompileError("Package semantic list literal elements must be package-defined semantic records")
    record_type = registry.most_specific_common_nominal_base(
        type_spec.record_type for type_spec in type_specs
    )
    return TypeSpec("RECORD", record_type=record_type)


def _infer_semantic_list_literal_payload(
    payloads: tuple[ExtensionSemanticPayload, ...],
    registry,
) -> ExtensionSemanticPayload:
    """Combine semantic list-literal elements through existing payload import and compaction rules."""
    item_type = _common_semantic_list_item_type_spec(
        tuple(payload.type_spec for payload in payloads),
        registry,
    )
    invocation = SemanticInvocation(registry)
    items = tuple(invocation.import_payload_value(payload) for payload in payloads)
    return compact_extension_semantic_payload(
        TypeSpec("LIST", item=item_type),
        items,
        invocation.dependencies,
    )


def _contextual_semantic_list_payload(
    expression: ast.expr,
    expected: TypeSpec,
    *,
    analyze,
    facts,
    invocation: SemanticInvocation,
    registry,
    call_name: str,
) -> ExtensionSemanticPayload:
    """Acquire one semantic LIST, preserving its declared TypeSpec even for empty literals."""
    if expected.kind != "LIST":
        raise CompileError("Internal error: semantic list acquisition received non-LIST TypeSpec")

    if not isinstance(expression, ast.List):
        fact = analyze(expression)
        payload = fact.semantic_payload
        if payload is None:
            raise CompileError(f"{call_name}() expects a frontend semantic list value")
        if not semantic_type_compatible(payload.type_spec, expected, registry):
            raise CompileError(f"{call_name}() semantic list argument has incompatible declared record type")
        return payload

    existing = facts.get(expression)
    if existing is not None and existing.semantic_payload is not None:
        payload = existing.semantic_payload
        if not semantic_type_compatible(payload.type_spec, expected, registry):
            raise CompileError(f"{call_name}() semantic list argument has incompatible declared record type")
        return payload

    packed_items: list[object] = []
    for child in expression.elts:
        if expected.item.kind == "LIST":
            child_payload = _contextual_semantic_list_payload(
                child,
                expected.item,
                analyze=analyze,
                facts=facts,
                invocation=invocation,
                registry=registry,
                call_name=call_name,
            )
        elif expected.item.kind == "RECORD":
            child_fact = analyze(child)
            child_payload = child_fact.semantic_payload
            if child_payload is None or child_payload.type_spec.kind != "RECORD":
                raise CompileError(f"{call_name}() semantic list elements must be package-defined semantic records")
            if not semantic_type_compatible(child_payload.type_spec, expected.item, registry):
                raise CompileError(f"{call_name}() semantic list element has incompatible record type")
        else:
            raise CompileError("Internal error: public semantic LIST must recursively contain RECORD leaves")
        packed_items.append(invocation.import_payload_value(child_payload))

    payload = compact_extension_semantic_payload(expected, tuple(packed_items), invocation.dependencies)
    facts[expression] = ExpressionFact(
        _semantic_result_shape(payload.type_spec, payload.value),
        semantic_payload=payload,
    )
    return payload


def _analyze_extension_semantic_call(
    node: ast.Call,
    cleaned_call: ast.Call,
    resolved: ResolvedCallable,
    modifiers,
    environment: SemanticEnvironment,
    analyze,
    facts,
    call_result_shape,
    spec: ExtensionCallableSpec,
    semantic_return: TypeSpec,
    execution_form: ExtensionExecutionForm,
):
    """Analyze one non-overloaded semantic-capable extension call without body persistence."""
    name = resolved.source_name
    if modifiers.unique_was_explicit:
        raise unsupported_unique(name)
    if any(keyword.arg is None for keyword in cleaned_call.keywords):
        raise CompileError(f"{name}() does not support caller-side ** argument expansion")

    normalized_args: list[ast.expr | ExtensionSemanticPayload] = []
    for argument in cleaned_call.args:
        if not isinstance(argument, ast.Starred):
            normalized_args.append(argument)
            continue
        starred_fact = analyze(argument.value)
        payload = starred_fact.semantic_payload
        if payload is None or payload.type_spec.kind != "LIST":
            raise CompileError(f"{name}() caller-side * requires a package semantic LIST value")
        if not isinstance(payload.value, tuple):
            raise CompileError("Internal error: semantic LIST payload storage is malformed")
        for item in payload.value:
            child = compact_extension_semantic_payload(
                payload.type_spec.item,
                item,
                payload.dependencies,
            )
            normalized_args.append(child)

    keyword_values: dict[str, ast.expr] = {}
    for keyword in cleaned_call.keywords:
        if keyword.arg in keyword_values:
            raise CompileError(f"{name}() got multiple values for keyword argument {keyword.arg!r}")
        keyword_values[keyword.arg] = keyword.value
    signature = spec.python_signature()
    try:
        bound = signature.bind(*normalized_args, **keyword_values)
    except TypeError as exc:
        raise CompileError(f"{name}() arguments do not match its declared extension signature") from exc
    bound.apply_defaults()

    registry = environment.extension_registry
    if registry is None:
        raise CompileError("Internal error: semantic extension call has no immutable extension registry")
    invocation = SemanticInvocation(registry)

    for parameter in spec.parameters:
        bound_value = (
            bound.arguments.get(parameter.name, ())
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL
            else bound.arguments.get(parameter.name)
        )
        occurrences = tuple(bound_value) if parameter.kind is inspect.Parameter.VAR_POSITIONAL else (bound_value,)
        acquired_items: list[object] = []
        type_spec = parameter.type_spec
        for occurrence in occurrences:
            if occurrence is None and parameter.kind is not inspect.Parameter.VAR_POSITIONAL:
                raise CompileError("Internal error: semantic extension binding lost fixed parameter")
            if isinstance(occurrence, ExtensionSemanticPayload):
                payload = occurrence
                if type_spec.kind not in {"RECORD", "LIST"}:
                    raise CompileError(f"{name}() caller-side * semantic item is incompatible with parameter contract")
                if not semantic_type_compatible(payload.type_spec, type_spec, registry):
                    raise CompileError(f"{name}() caller-side * semantic item has incompatible declared type")
                acquired_items.append(invocation.reconstruct_payload(payload))
                continue
            if not isinstance(occurrence, ast.AST):
                if parameter.type_spec.kind != "NF_SET" or parameter.default_type is None:
                    raise CompileError("Internal error: semantic extension default lost canonical NF contract")
                acquired_items.append(occurrence)
                continue

            if type_spec.kind == "NF_SET":
                try:
                    selection = resolve_argument_evaluation(
                        occurrence,
                        environment.const_eval_values,
                        parameter.evaluation_mode,
                    )
                except ConstEvalUnavailable as exc:
                    raise CompileError(f"{name}() argument must be available at compile time") from exc
                if isinstance(selection, CompileTimeSelection):
                    actual_type = static_nf_type(selection.value)
                    if not type_spec_accepts_nf(type_spec, actual_type):
                        raise CompileError(f"{name}() argument type {actual_type} is not accepted by its extension contract")
                    expected_type = select_declared_nf_type(type_spec, actual_type)
                    acquired_items.append(canonicalize_group_input_default(expected_type, selection.value))
                elif isinstance(selection, RuntimeRequired):
                    fact = analyze(occurrence)
                    actual_type = _require_runtime_type(fact, f"{name}() argument")
                    if not type_spec_accepts_nf(type_spec, actual_type):
                        raise CompileError(f"{name}() argument type {actual_type} is not accepted by its extension contract")
                    source = ExtensionDependencySource(occurrence, actual_type)
                    acquired_items.append(invocation.runtime_ref_for_source(source))
                else:
                    raise CompileError("Internal error: unsupported extension evaluation selection")
            elif type_spec.kind == "RECORD":
                fact = analyze(occurrence)
                payload = fact.semantic_payload
                if payload is None or not isinstance(fact.result_shape, ExtensionResultShape):
                    raise CompileError(f"{name}() expects a package-defined semantic record")
                if not registry.is_nominal_subtype(fact.result_shape.type_id, type_spec.record_type):
                    raise CompileError(f"{name}() semantic record argument has incompatible nominal type")
                acquired_items.append(invocation.reconstruct_payload(payload))
            elif type_spec.kind == "LIST":
                payload = _contextual_semantic_list_payload(
                    occurrence,
                    type_spec,
                    analyze=analyze,
                    facts=facts,
                    invocation=invocation,
                    registry=registry,
                    call_name=name,
                )
                acquired_items.append(invocation.reconstruct_payload(payload))
            else:
                raise CompileError("Internal error: unsupported public semantic extension parameter TypeSpec")

        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            bound.arguments[parameter.name] = tuple(acquired_items)
        else:
            bound.arguments[parameter.name] = acquired_items[0]

    semantic_value = registry.invoke_semantic(spec.id, *bound.args, **bound.kwargs)
    packed = invocation.pack_result(semantic_return, semantic_value)
    semantic_payload = compact_extension_semantic_payload(semantic_return, packed, invocation.dependencies)
    packed = semantic_payload.value
    dependencies = semantic_payload.dependencies

    if execution_form is ExtensionExecutionForm.SEMANTIC_ONLY:
        return ExpressionFact(
            _semantic_result_shape(spec.result, packed),
            semantic_payload=semantic_payload,
        )

    if execution_form is not ExtensionExecutionForm.SEMANTIC_THEN_BACKEND:
        raise CompileError("Internal error: semantic extension call has unexpected execution form")
    result_spec = _extension_result_spec(spec)
    runtime_operands = tuple(AnalyzedCallOperand(None, source.typ) for source in dependencies)
    analyzed_call = AnalyzedCall(
        target=resolved,
        runtime_operands=runtime_operands,
        options=(),
        result=result_spec,
        extension_state_type=semantic_return,
        extension_state=packed,
    )
    return ExpressionFact(
        call_result_shape(result_spec),
        analyzed_call=analyzed_call,
        call_operand_nodes=tuple(source.source for source in dependencies),
    )


def _analyze_extension_call(
    node: ast.Call,
    cleaned_call: ast.Call,
    resolved: ResolvedCallable,
    modifiers,
    environment: SemanticEnvironment,
    analyze,
    facts,
    call_result_shape,
):
    """Dispatch backend-only or semantic-capable extension calls by normalized contract."""
    registry = environment.extension_registry
    if registry is None:
        raise CompileError("Internal error: extension call has no immutable extension registry")
    family = registry.callable_specs(resolved.target)
    semantic_return = registry.semantic_return_type(resolved.target)
    if len(family) > 1:
        if semantic_return is not None:
            raise CompileError(f"Overloaded extension callable {resolved.source_name}() cannot define semantic.py implementation")
        return _analyze_extension_backend_call(
            node,
            cleaned_call,
            resolved,
            modifiers,
            environment,
            analyze,
            facts,
            call_result_shape,
        )

    spec = family[0]
    execution_form = classify_extension_execution(
        spec,
        semantic_return,
        has_implementation=registry.has_implementation(spec.id),
    )
    if execution_form is ExtensionExecutionForm.BACKEND_ONLY:
        return _analyze_extension_backend_call(
            node,
            cleaned_call,
            resolved,
            modifiers,
            environment,
            analyze,
            facts,
            call_result_shape,
        )
    if semantic_return is None:
        raise CompileError("Internal error: semantic execution form has no semantic return contract")
    return _analyze_extension_semantic_call(
        node,
        cleaned_call,
        resolved,
        modifiers,
        environment,
        analyze,
        facts,
        call_result_shape,
        spec,
        semantic_return,
        execution_form,
    )

def _unregistered_keyword_error(name):
    """Create the exact legacy diagnostic for keywords on an unresolved call."""
    return CompileError(
        f"Keyword arguments are only supported for builtins, library functions, local functions or local backend helpers; {name} is not registered as one"
    )




def build_semantic_environment(
    *,
    runtime_bindings,
    compile_time,
    reserved_name_labels,
    callable_environment,
    structural_bindings=None,
    structural_arrays=None,
    builder_bindings=None,
    extension_bindings=None,
    object_semantics=None,
    available_group_context_slots=frozenset(),
    source_callable_session=None,
    source_definition_owner=None,
    source_owner_scope=None,
    source_call_site_allocator=None,
    helper_namespace="Group",
    extension_registry=None,
):
    """Build one immutable expression environment from detached compiler-owned snapshots."""
    structural_bindings = {} if structural_bindings is None else dict(structural_bindings)
    structural_arrays = StructuralArraySnapshot({}, {}) if structural_arrays is None else structural_arrays
    if not isinstance(structural_arrays, StructuralArraySnapshot):
        raise TypeError("structural_arrays must be a StructuralArraySnapshot")
    runtime_bindings = dict(runtime_bindings)
    builder_bindings = {} if builder_bindings is None else dict(builder_bindings)
    if not all(isinstance(value, BindingId) for value in builder_bindings.values()):
        raise TypeError("builder_bindings values must be BindingId records")
    extension_bindings = {} if extension_bindings is None else dict(extension_bindings)
    if not all(isinstance(value, ExtensionSemanticPayload) for value in extension_bindings.values()):
        raise TypeError("extension_bindings values must be ExtensionSemanticPayload records")
    if any(
        any(not dependency.is_persistent for dependency in payload.dependencies)
        for payload in extension_bindings.values()
    ):
        raise ValueError("extension_bindings payload dependencies must be BindingId-backed")
    ownership_sets = (
        set(runtime_bindings), set(structural_bindings), set(structural_arrays.bindings),
        set(builder_bindings), set(extension_bindings),
    )
    if any(ownership_sets[i] & ownership_sets[j] for i in range(len(ownership_sets)) for j in range(i + 1, len(ownership_sets))):
        raise CompileError("Internal error: source name is active in multiple semantic binding domains")
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
    semantic_constants, const_eval_values = build_semantic_constant_snapshot(compile_time)
    return SemanticEnvironment(
        runtime_bindings=MappingProxyType(runtime_bindings),
        constants=semantic_constants,
        const_eval_values=const_eval_values,
        reserved_name_labels=MappingProxyType(dict(reserved_name_labels)),
        callable_environment=callable_environment,
        structural_bindings=MappingProxyType(structural_bindings),
        structural_arrays=structural_arrays,
        builder_bindings=MappingProxyType(builder_bindings),
        extension_bindings=MappingProxyType(extension_bindings),
        object_semantics=object_semantics,
        available_group_context_slots=frozenset(available_group_context_slots),
        source_callable_session=source_callable_session,
        source_definition_owner=source_definition_owner,
        source_owner_scope=source_owner_scope,
        source_call_site_allocator=source_call_site_allocator,
        helper_namespace=str(helper_namespace or "Group"),
        extension_registry=extension_registry,
    )


def analyze_expression(expr, environment):
    """Resolve and type-check one complete expression with detached semantic state."""
    facts = {}
    if not isinstance(environment, SemanticEnvironment):
        raise TypeError("environment must be a SemanticEnvironment")
    available_group_context_slots = set(environment.available_group_context_slots)
    used_extension_owners: set[tuple[str, ...]] = set()
    if not all(isinstance(slot, GroupContextSlot) for slot in available_group_context_slots):
        raise TypeError("available_group_context_slots must contain GroupContextSlot values")
    if environment.object_semantics is None:
        object_ids_by_binding = None
        object_states = None
        next_object_id = None
    else:
        object_ids_by_binding = dict(environment.object_semantics.object_ids_by_binding)
        object_states = dict(environment.object_semantics.states)
        next_object_id = environment.object_semantics.next_object_id

    def require_body_object_semantics():
        """Reject Object analysis that lacks the persistent body-owned semantic registry."""
        if object_ids_by_binding is None or object_states is None:
            raise CompileError(
                "Internal error: Object expression analysis requires body-owned Object semantic state"
            )

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
        if any(typ is NFType.OBJECT for typ in types):
            require_body_object_semantics()
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
        if isinstance(result, ProjectedCallResult):
            return shapes[result.exposed_index]
        if isinstance(result, ContextReadCallResult):
            return shapes[0]
        raise CompileError("Internal error: unsupported analyzed call result contract")

    def analyze(node):
        if isinstance(node, ast.Constant):
            if type(node.value) is bool:
                return record(node, runtime_fact(TYPE_BOOL, literal_value=node.value))
            if type(node.value) is int:
                value = normalize_int_constant(node.value)
                return record(node, runtime_fact(TYPE_INT, literal_value=value))
            if type(node.value) is float:
                value = normalize_float_constant(node.value)
                return record(node, runtime_fact(TYPE_FLOAT, literal_value=value))
            if isinstance(node.value, str):
                return record(node, runtime_fact(TYPE_STRING, literal_value=node.value))
            raise CompileError("Only numeric, boolean and string constants are supported")

        if isinstance(node, ast.Name):
            if node.id in TYPE_TOKEN_NAMES:
                raise CompileError(f"Type token {node.id} may only be used in node(...) type declarations")
            if node.id in environment.callable_environment.package_namespaces:
                raise CompileError(f"Package namespace {node.id!r} cannot be used as a value")
            if node.id in environment.runtime_bindings:
                symbol = environment.runtime_bindings[node.id]
                if symbol.typ is NFType.OBJECT:
                    require_body_object_semantics()
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
                if any(leaf.typ is NFType.OBJECT for leaf in structural.leaves):
                    require_body_object_semantics()
                shape = structural.result_shape({} if object_ids_by_binding is None else object_ids_by_binding)
                resolved = ResolvedName(
                    "structural_binding",
                    None,
                    name=node.id,
                    structural_binding=structural,
                )
                return record(node, ExpressionFact(shape, resolved_name=resolved))
            if node.id in environment.structural_arrays.bindings:
                array_id = environment.structural_arrays.bindings[node.id]
                object_map = {} if object_ids_by_binding is None else object_ids_by_binding
                shape = environment.structural_arrays.result_shape(array_id, object_map)
                resolved = ResolvedName(
                    "structural_array",
                    None,
                    name=node.id,
                    array_id=array_id,
                )
                return record(node, ExpressionFact(shape, resolved_name=resolved, array_id=array_id))
            if node.id in environment.builder_bindings:
                raise CompileError("geometry_builder cannot escape script scope")
            if node.id in environment.extension_bindings:
                payload = environment.extension_bindings[node.id]
                return record(
                    node,
                    ExpressionFact(
                        _semantic_result_shape(payload.type_spec, payload.value),
                        semantic_payload=payload,
                    ),
                )
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
                value = normalize_float_constant(_ALLOWED_CONSTS[node.id])
                resolved = ResolvedName("allowed_constant", TYPE_FLOAT, name=node.id, value=value)
                return record(node, runtime_fact(TYPE_FLOAT, resolved_name=resolved, literal_value=value))
            label = environment.reserved_name_labels.get(node.id)
            if label in _RESERVED_VALUE_LABELS:
                raise CompileError(f"Name {node.id} is registered as {label} and cannot be used as a value")
            raise CompileError(f"Unknown name: {node.id}")

        if isinstance(node, ast.List):
            items = []
            semantic_payloads: list[ExtensionSemanticPayload | None] = []
            for child in node.elts:
                item = analyze(child)
                items.append(item.result_shape)
                semantic_payloads.append(item.semantic_payload)
            semantic_count = sum(payload is not None for payload in semantic_payloads)
            if semantic_count:
                if semantic_count != len(semantic_payloads):
                    raise CompileError(
                        "A list literal cannot mix package semantic values with ordinary values"
                    )
                if environment.extension_registry is None:
                    raise CompileError("Internal error: semantic list literal has no immutable extension registry")
                payload = _infer_semantic_list_literal_payload(
                    tuple(payload for payload in semantic_payloads if payload is not None),
                    environment.extension_registry,
                )
                return record(
                    node,
                    ExpressionFact(
                        _semantic_result_shape(payload.type_spec, payload.value),
                        semantic_payload=payload,
                    ),
                )
            return record(node, ExpressionFact(ArrayResultShape(tuple(items))))

        if isinstance(node, ast.Tuple):
            items = []
            for child in node.elts:
                item = analyze(child)
                items.append(item.result_shape)
            return record(node, ExpressionFact(ArrayResultShape(tuple(items))))

        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in environment.builder_bindings:
                if node.attr != "geometry":
                    raise CompileError("geometry_builder supports only .geometry")
                binding_id = environment.builder_bindings[node.value.id]
                resolved = ResolvedName(
                    "runtime_binding",
                    TYPE_GEOMETRY,
                    name=node.value.id,
                    binding_id=binding_id,
                )
                return record(node, runtime_fact(TYPE_GEOMETRY, resolved_name=resolved))
            base = analyze(node.value)
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
                require_body_object_semantics()
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
            raise CompileError("Only .x, .y and .z vector attributes are supported")

        if isinstance(node, ast.Subscript):
            base = analyze(node.value)
            if isinstance(base.result_shape, NamedOutputsResultShape):
                try:
                    key = _const_eval(node.slice, environment.const_eval_values)
                except ConstEvalUnavailable as exc:
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
                except ConstEvalUnavailable as exc:
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
                index = _const_eval(node.slice, environment.const_eval_values)
            except ConstEvalUnavailable as exc:
                raise CompileError("array/vector indexing currently requires a compile-time integer index") from exc
            if type(index) is not int:
                raise CompileError("array/vector indexing currently requires a compile-time integer index")
            if isinstance(base.result_shape, ArrayResultShape):
                try:
                    selected_shape = base.result_shape.items[index]
                except IndexError as exc:
                    raise CompileError("array index out of range") from exc
                resolved_name = None
                selected_array_id = None
                if base.array_id is not None:
                    array_state = environment.structural_arrays.states.get(base.array_id)
                    if array_state is None:
                        raise CompileError("Internal error: analyzed array provenance references missing state")
                    try:
                        selected_item = array_state.items[index]
                    except IndexError as exc:
                        raise CompileError("Internal error: analyzed array index became invalid") from exc
                    if isinstance(selected_item, StructuralArrayRef):
                        selected_array_id = selected_item.array_id
                        resolved_name = ResolvedName(
                            "structural_array",
                            None,
                            name=base.resolved_name.name if base.resolved_name is not None else None,
                            array_id=selected_array_id,
                        )
                    elif isinstance(selected_item, StructuralRuntimeLeaf):
                        resolved_name = ResolvedName(
                            "runtime_binding",
                            selected_item.typ,
                            name=base.resolved_name.name if base.resolved_name is not None else None,
                            binding_id=selected_item.binding_id,
                        )
                    elif isinstance(selected_item, StructuralBindingSymbol):
                        resolved_name = ResolvedName(
                            "structural_binding",
                            None,
                            name=base.resolved_name.name if base.resolved_name is not None else None,
                            structural_binding=selected_item,
                        )
                return record(
                    node,
                    ExpressionFact(
                        selected_shape,
                        operation="array_index",
                        literal_value=index,
                        resolved_name=resolved_name,
                        array_id=selected_array_id,
                    ),
                )
            base_typ = _require_runtime_type(base, "subscript")
            if base_typ == TYPE_VECTOR:
                if index not in (0, 1, 2):
                    raise CompileError("vector index must be 0, 1 or 2")
                return record(node, runtime_fact(TYPE_FLOAT, operation=("x", "y", "z")[index]))
            raise CompileError("indexing is supported for arrays and Vector values only")

        if isinstance(node, ast.BinOp):
            left = analyze(node.left)
            right = analyze(node.right)
            left_typ = _require_runtime_type(left, "binary expression")
            right_typ = _require_runtime_type(right, "binary expression")
            op_type = type(node.op)
            typ = _validate_binary(op_type, left_typ, right_typ)
            if typ is TYPE_INT:
                _validate_known_int_domain(node, environment)
            return record(node, runtime_fact(typ, operation=_BIN_OPS[op_type]))

        if isinstance(node, ast.UnaryOp):
            if (
                isinstance(node.op, ast.USub)
                and isinstance(node.operand, ast.Constant)
                and type(node.operand.value) is int
                and node.operand.value == -INT_MIN
            ):
                return record(
                    node,
                    runtime_fact(TYPE_INT, operation="SIGNED_INT_LITERAL", literal_value=INT_MIN),
                )
            operand = analyze(node.operand)
            if isinstance(node.op, ast.UAdd):
                return record(node, ExpressionFact(operand.result_shape, operation="+", array_id=operand.array_id))
            operand_typ = _require_runtime_type(operand, "unary expression")
            if isinstance(node.op, ast.USub):
                numeric_type = resolve_numeric_unary_minus(operand_typ)
                if numeric_type is not None:
                    if numeric_type is TYPE_INT:
                        _validate_known_int_domain(node, environment)
                    return record(node, runtime_fact(numeric_type, operation="-"))
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
                raise CompileError("Internal error: boolean expression has no operands")
            first = analyze(node.values[0])
            if len(node.values) < 2:
                return record(node, ExpressionFact(first.result_shape))
            op = _BOOLEAN_OPS.get(type(node.op))
            if not op:
                raise CompileError("Unsupported boolean operator")
            current_typ = _require_runtime_type(first, "boolean expression")
            for child in node.values[1:]:
                nxt = analyze(child)
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
                right = analyze(right_expr)
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
            true_value = analyze(node.body)
            false_value = analyze(node.orelse)
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
                raise CompileError("select() result type is not supported")
            return record(node, runtime_fact(true_typ, operation="select"))

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id in environment.callable_environment.package_namespaces
                ):
                    package_alias = node.func.value.id
                    name = f"{package_alias}.{node.func.attr}"
                    resolved = resolve_package_callable(
                        package_alias, node.func.attr, environment.callable_environment
                    )
                    cleaned_call, modifiers = extract_function_call_modifiers(
                        node, name, environment.const_eval_values
                    )
                else:
                    if node.func.attr != "info":
                        raise CompileError("Object values support only the .info() method")
                    require_body_object_semantics()
                    receiver = analyze(node.func.value)
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
                        except ConstEvalUnavailable as exc:
                            raise CompileError("Object.info() transform_space must be 'ORIGINAL' or 'RELATIVE'") from exc
                        if transform_space not in {"ORIGINAL", "RELATIVE"}:
                            raise CompileError("Object.info() transform_space must be 'ORIGINAL' or 'RELATIVE'")
                    if "as_instance" in kws:
                        try:
                            as_instance = _const_eval(kws["as_instance"], environment.const_eval_values)
                        except ConstEvalUnavailable as exc:
                            raise CompileError("Object.info() as_instance must be a compile-time Bool") from exc
                        if type(as_instance) is not bool:
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
            elif isinstance(node.func, ast.Name):
                name = node.func.id
                if name in environment.callable_environment.package_namespaces:
                    raise CompileError(
                        f"Package namespace {name!r} cannot be called directly; use {name}.<member>(...)"
                    )
                package_candidates = package_candidates_for_unqualified(
                    name, environment.callable_environment
                )
                if package_candidates and has_source_value_binding(
                    name,
                    runtime_bindings=environment.runtime_bindings,
                    compile_time_values=environment.const_eval_values,
                    structural_binding_names=environment.structural_bindings,
                    structural_array_names=environment.structural_arrays.bindings,
                    builder_binding_names=environment.builder_bindings,
                    extension_binding_names=environment.extension_bindings,
                ):
                    raise non_callable_source_binding_error(name, environment.callable_environment)
                resolved = resolve_simple_callable(name, environment.callable_environment)
                cleaned_call, modifiers = extract_function_call_modifiers(
                    node, name, environment.const_eval_values
                )
            else:
                raise CompileError("Only simple or package-qualified function calls are supported")

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
                if name not in IR_CAPABLE_BUILTIN_NAMES:
                    raise CompileError(f"Internal error: unclassified callable builtin {name!r}")
                runtime_nodes = []

                # join(array) is an existing compiler-structural calling form: normalize a
                # stored semantic array into the same ordered scalar runtime operands used by
                # join(geo_a, geo_b, ...). IRCallArgument remains runtime-scalar only.
                if name == "join" and len(cleaned_call.args) == 1 and not isinstance(
                    cleaned_call.args[0], (ast.List, ast.Tuple)
                ):
                    source_array = cleaned_call.args[0]
                    source_fact = analyze(source_array)
                    if isinstance(source_fact.result_shape, ArrayResultShape):
                        if source_fact.result_shape.items:
                            expanded = []
                            for index in range(len(source_fact.result_shape.items)):
                                selector = ast.Subscript(
                                    value=source_array,
                                    slice=ast.Constant(value=index),
                                    ctx=ast.Load(),
                                )
                                ast.copy_location(selector, source_array)
                                expanded.append(selector)
                            cleaned_call = ast.Call(
                                func=cleaned_call.func,
                                args=expanded,
                                keywords=list(cleaned_call.keywords),
                            )
                            ast.copy_location(cleaned_call, node)
                        else:
                            # Preserve join([]): a single empty literal is the established
                            # syntax that normalizes to zero Geometry operands.
                            empty = ast.List(elts=[], ctx=ast.Load())
                            ast.copy_location(empty, source_array)
                            cleaned_call = ast.Call(
                                func=cleaned_call.func,
                                args=[empty],
                                keywords=list(cleaned_call.keywords),
                            )
                            ast.copy_location(cleaned_call, node)

                def add_runtime(child, parameter_name, context):
                    child_fact = analyze(child)
                    typ = _require_runtime_type(child_fact, context)
                    runtime_nodes.append(child)
                    return typ

                builtin = analyze_builtin_call(
                    name, cleaned_call, environment.const_eval_values, add_runtime
                )
                if isinstance(builtin.result, ContextReadCallResult):
                    if builtin.result.slot not in available_group_context_slots:
                        if builtin.result.slot is GroupContextSlot.GRID_UV:
                            raise CompileError("grid_uv() requires a preceding grid(width, height) call")
                        raise CompileError("Internal error: unavailable group-context slot read")
                analyzed_call = AnalyzedCall(
                    target=resolved,
                    runtime_operands=builtin.operands,
                    options=builtin.options,
                    result=builtin.result,
                )
                result_shape = call_result_shape(builtin.result)
                if isinstance(builtin.result, ProjectedCallResult):
                    for slot, _result_index in builtin.result.context_writes:
                        available_group_context_slots.add(slot)
                return record(
                    node,
                    ExpressionFact(
                        result_shape,
                        analyzed_call=analyzed_call,
                        call_operand_nodes=tuple(runtime_nodes),
                    ),
                )
            if resolved.kind is CallableKind.LOCAL_FUNCTION:
                session = environment.source_callable_session
                if session is None or not callable(environment.source_call_site_allocator):
                    raise CompileError("Internal error: local source call has no semantic source-call session")
                fn = environment.callable_environment.local_functions[name]
                if not isinstance(fn, ast.FunctionDef):
                    raise CompileError(f"Internal error: local function {name!r} is not an AST definition")
                if fn.args.posonlyargs or fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs or fn.args.defaults or fn.args.kw_defaults:
                    raise CompileError("Local functions currently support only plain positional parameters without defaults")
                for arg in fn.args.args:
                    if arg.arg == "__unique__":
                        raise CompileError("Local function parameter __unique__ is reserved by the compiler")
                params = [arg.arg for arg in fn.args.args]
                declared = {arg.arg: resolve_local_parameter_annotation(arg.annotation) for arg in fn.args.args}
                bound_nodes: dict[str, ast.expr] = {}
                bound_types: dict[str, NFType] = {}
                bound_arguments = bind_local_source_arguments(
                    tuple(params),
                    tuple(cleaned_call.args),
                    tuple((keyword.arg, keyword.value) for keyword in cleaned_call.keywords),
                    name,
                )
                for param_name, child in bound_arguments:
                    child_fact = analyze(child)
                    if child_fact.semantic_payload is not None:
                        raise CompileError(
                            f"{name}() source function arguments cannot be package semantic values"
                        )
                    if isinstance(child_fact.result_shape, ArrayResultShape):
                        raise CompileError(
                            "Local function constant arguments must be numbers, booleans, strings or vectors"
                        )
                    actual_type = _require_runtime_type(child_fact, "script-local function argument")
                    expected = declared[param_name]
                    if expected is not None and not source_argument_type_matches(expected, actual_type):
                        raise CompileError(
                            f"{name}() parameter {param_name!r} expects {expected}, got {actual_type}"
                        )
                    bound_nodes[param_name] = child
                    bound_types[param_name] = expected or actual_type

                captures = analyze_local_captures(
                    fn,
                    local_functions=environment.callable_environment.local_functions,
                    runtime_bindings=environment.runtime_bindings,
                    compile_time_values=environment.const_eval_values,
                    reserved_name_labels=environment.reserved_name_labels,
                    structural_binding_names=environment.structural_bindings,
                    structural_array_names=environment.structural_arrays.bindings,
                    builder_binding_names=environment.builder_bindings,
                    extension_binding_names=environment.extension_bindings,
                    callable_environment=environment.callable_environment,
                )
                for capture in captures:
                    bound_types[capture.name] = capture.typ
                signature_names = tuple(params) + tuple(capture.name for capture in captures)
                signature = serialize_local_signature(signature_names, bound_types)
                definition_owner = environment.source_definition_owner or environment.source_owner_scope
                if not definition_owner:
                    raise CompileError("Internal error: local source call has no definition owner")
                function_id = local_function_id(definition_owner, name, signature)
                if modifiers.unique:
                    call_site = environment.source_call_site_allocator(function_id)
                    materialization = IRFunctionMaterialization(
                        function_id, IRFunctionMaterializationMode.UNIQUE, call_site
                    )
                else:
                    materialization = IRFunctionMaterialization(
                        function_id, IRFunctionMaterializationMode.SHARED, None
                    )
                owner_scope = function_materialization_owner_scope(materialization)
                identity = GroupCompilationIdentity(
                    root_owner_id=None,
                    owner_scope=owner_scope,
                    definition_owner=function_id.definition_owner,
                    declaration_owner=function_id.stable_key(),
                )
                return_shape = analyze_local_return_shape(fn)
                generated_source = local_function_source(
                    fn,
                    bound_types,
                    hidden_captures=tuple(capture.name for capture in captures),
                    return_shape=return_shape,
                )
                local_names = local_binding_names(fn)
                inherited_package_namespaces = {
                    alias: binding
                    for alias, binding in environment.callable_environment.package_namespaces.items()
                    if alias not in local_names
                }
                prepared = session.prepare_local(
                    function_id=function_id,
                    identity=identity,
                    generated_source=generated_source,
                    explicit_parameter_names=tuple(params),
                    hidden_capture_names=tuple(capture.name for capture in captures),
                    local_functions=environment.callable_environment.local_functions,
                    imported_library_functions=environment.callable_environment.imported_functions,
                    package_namespaces=inherited_package_namespaces,
                    helper_namespace=environment.helper_namespace,
                    return_shape=return_shape,
                )
                contract_by_name = {parameter.source_name: parameter for parameter in prepared.contract.parameters}
                runtime_nodes = []
                runtime_operands = []
                static_operands = []
                for param in params:
                    parameter = contract_by_name.get(param)
                    if parameter is None:
                        raise CompileError(f"Internal error: local semantic contract lost parameter {param!r}")
                    child = bound_nodes[param]
                    actual_type = _require_runtime_type(facts[child], "script-local function argument")
                    if not source_argument_type_matches(parameter.typ, actual_type):
                        raise CompileError(f"{name}() parameter {param!r} expects {parameter.typ}, got {actual_type}")
                    folded = try_runtime_fold(child, environment.const_eval_values)
                    if folded is NOT_FOLDABLE:
                        runtime_nodes.append(child)
                        runtime_operands.append(AnalyzedCallOperand(param, actual_type, parameter.input_index))
                    else:
                        static_operands.append(AnalyzedStaticCallOperand(parameter.input_index, folded))
                for capture in captures:
                    parameter = contract_by_name.get(capture.name)
                    if parameter is None:
                        raise CompileError(f"Internal error: local semantic contract lost capture {capture.name!r}")
                    if capture.runtime:
                        child = ast.copy_location(ast.Name(id=capture.name, ctx=ast.Load()), node)
                        capture_fact = analyze(child)
                        actual_type = _require_runtime_type(capture_fact, "script-local function capture")
                        runtime_nodes.append(child)
                        runtime_operands.append(
                            AnalyzedCallOperand(capture.name, actual_type, parameter.input_index)
                        )
                    else:
                        static_operands.append(
                            AnalyzedStaticCallOperand(parameter.input_index, capture.compile_time_value)
                        )
                result_types = tuple(output.typ for output in prepared.contract.outputs)
                if not result_types:
                    raise CompileError(f"Local function {name}() has no outputs")
                result_spec = RuntimeCallResult(result_types[0]) if len(result_types) == 1 else TupleCallResult(result_types)
                analyzed_call = AnalyzedCall(
                    target=resolved,
                    runtime_operands=tuple(runtime_operands),
                    options=(),
                    result=result_spec,
                    source_function_id=function_id,
                    materialization=materialization,
                    static_operands=tuple(static_operands),
                )
                return record(
                    node,
                    ExpressionFact(
                        call_result_shape(result_spec),
                        analyzed_call=analyzed_call,
                        call_operand_nodes=tuple(runtime_nodes),
                    ),
                )
            if resolved.kind is CallableKind.EXTENSION:
                extension_fact = _analyze_extension_call(
                    node,
                    cleaned_call,
                    resolved,
                    modifiers,
                    environment,
                    analyze,
                    facts,
                    call_result_shape,
                )
                used_extension_owners.add(tuple(resolved.target.owner))
                return record(node, extension_fact)
            if resolved.kind is CallableKind.LIBRARY:
                session = environment.source_callable_session
                if session is None or not callable(environment.source_call_site_allocator):
                    raise CompileError("Internal error: imported source call has no semantic source-call session")
                binding = resolved.target
                library_record = binding.record
                if (
                    getattr(library_record, "interface_path", None) is not None
                    and getattr(library_record, "source_path", None) is not None
                ):
                    # TODO(nodeforge-migration): Source-backed library owners with interface.py are reserved for the
                    # later hybrid-extension migration. This backend-only platform supports pure source entries and
                    # native-only v2 extension owners only; do not construct an owner-local helper view here. Remove
                    # this marker when hybrid source/interface execution and its resource-mutation contract are implemented.
                    raise CompileError(
                        f"{name}() is temporarily unavailable while v2 hybrid source/interface callables are being migrated"
                    )
                if getattr(library_record, "source_path", None) is None or getattr(library_record, "module_path", None) is not None:
                    raise CompileError(
                        f"{name}() uses unsupported Extension API v1 native execution; "
                        "migrate the owner to interface.py (EXTENSION_API = 2)"
                    )
                function_id = resolved.library_function_id
                if binding.namespace == "local":
                    if modifiers.unique_was_explicit:
                        raise unsupported_unique(name)
                    materialization = None
                    owner_scope = function_group_owner_scope("LIBRARY", "local", "", function_id.name)
                    identity = GroupCompilationIdentity(
                        None, owner_scope, owner_scope, function_id.stable_key()
                    )
                else:
                    if modifiers.unique:
                        call_site = environment.source_call_site_allocator(function_id)
                        materialization = IRFunctionMaterialization(
                            function_id, IRFunctionMaterializationMode.UNIQUE, call_site
                        )
                    else:
                        materialization = IRFunctionMaterialization(
                            function_id, IRFunctionMaterializationMode.SHARED, None
                        )
                    owner_scope = function_materialization_owner_scope(materialization)
                    identity = GroupCompilationIdentity(
                        None, owner_scope, function_id.stable_key(), function_id.stable_key()
                    )
                prepared = session.prepare_library(
                    function_id=function_id,
                    identity=identity,
                    record=library_record,
                )
                public_parameters = tuple(parameter for parameter in prepared.contract.parameters if parameter.public)
                bound_pairs = bind_imported_source_arguments(
                    public_parameters,
                    tuple(cleaned_call.args),
                    tuple((keyword.arg, keyword.value) for keyword in cleaned_call.keywords),
                    name,
                )
                bound: list[tuple[object, ast.expr, NFType]] = []
                for parameter, child in bound_pairs:
                    child_fact = analyze(child)
                    if child_fact.semantic_payload is not None:
                        raise CompileError(
                            f"{name}() source function arguments cannot be package semantic values"
                        )
                    actual_type = _require_runtime_type(child_fact, "function-library argument")
                    if not source_argument_type_matches(parameter.typ, actual_type):
                        raise CompileError(
                            f"{name}() input {parameter.display_name!r} expects {parameter.typ}, got {actual_type}"
                        )
                    bound.append((parameter, child, actual_type))
                runtime_nodes = []
                runtime_operands = []
                static_operands = []
                for parameter, child, actual_type in bound:
                    folded = try_runtime_fold(child, environment.const_eval_values)
                    if folded is NOT_FOLDABLE:
                        runtime_nodes.append(child)
                        runtime_operands.append(
                            AnalyzedCallOperand(parameter.display_name, actual_type, parameter.input_index)
                        )
                    else:
                        static_operands.append(AnalyzedStaticCallOperand(parameter.input_index, folded))
                result_types = tuple(output.typ for output in prepared.contract.outputs)
                if not result_types:
                    raise CompileError(f"Library function {name} has no outputs")
                result_spec = RuntimeCallResult(result_types[0]) if len(result_types) == 1 else TupleCallResult(result_types)
                analyzed_call = AnalyzedCall(
                    target=resolved,
                    runtime_operands=tuple(runtime_operands),
                    options=(),
                    result=result_spec,
                    source_function_id=function_id,
                    materialization=materialization,
                    static_operands=tuple(static_operands),
                )
                return record(
                    node,
                    ExpressionFact(
                        call_result_shape(result_spec),
                        analyzed_call=analyzed_call,
                        call_operand_nodes=tuple(runtime_nodes),
                    ),
                )
            raise CompileError(f"Internal error: unsupported resolved callable category {resolved.kind}")

        raise CompileError(f"Unsupported expression element: {type(node).__name__}")

    analyze(expr)
    if object_ids_by_binding is None:
        snapshot = None
    else:
        snapshot = ObjectSemanticSnapshot(object_ids_by_binding, object_states, next_object_id)
    return ExpressionAnalysis(
        root=expr,
        facts=MappingProxyType(dict(facts)),
        object_semantics=snapshot,
        available_group_context_slots=frozenset(available_group_context_slots),
        source_callable_session=environment.source_callable_session,
        source_definition_owner=environment.source_definition_owner,
        source_owner_scope=environment.source_owner_scope,
        source_call_site_allocator=environment.source_call_site_allocator,
        helper_namespace=environment.helper_namespace,
        structural_arrays=environment.structural_arrays,
        used_extension_owners=frozenset(used_extension_owners),
        extension_registry=environment.extension_registry,
    )


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
