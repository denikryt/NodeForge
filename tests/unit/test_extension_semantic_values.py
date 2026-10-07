"""Expression-local package semantic value tests."""

from __future__ import annotations

import ast
from pathlib import Path
from types import MappingProxyType

import pytest

from NodeForge.semantic.call_resolution import CallableEnvironment
from NodeForge.compiler_identities import BindingId
from NodeForge.errors import CompileError
from NodeForge.extension_contracts import ExtensionTypeId, TypeSpec
from NodeForge.extension_registry import ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot
from NodeForge.extension_semantics import (
    ExtensionDependencySource,
    ExtensionSemanticPayload,
    compact_extension_semantic_payload,
)
from NodeForge.extension_values import (
    ExtensionDependencySlot,
    ExtensionValue,
)
from NodeForge.nf_types import NFType
from NodeForge.semantic.runtime_bindings import RuntimeBindingSymbol
from NodeForge.resolved_environment import PackageCallableExport, ResolvedPackageNamespace
from NodeForge.semantic.source_bindings import PackageNamespaceBinding
from NodeForge.semantic.analysis import SemanticEnvironment, analyze_expression
from NodeForge.semantic.ir import IRCall, IRCallOperandRef
from NodeForge.semantic.lowering import lower_analyzed_expression
from NodeForge.semantic.values import ArrayResultShape, ExtensionResultShape

pytestmark = pytest.mark.unit


INTERFACE = '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float, Int, Object

EXTENSION_API = 2

@dataclass(frozen=True)
class Part:
    value: Float | Int | Object

@dataclass(frozen=True)
class AlphaPart(Part):
    pass

@dataclass(frozen=True)
class BetaPart(Part):
    pass

@dataclass(frozen=True)
class Other:
    value: Float

@dataclass(frozen=True)
class AmbiguousBaseA:
    pass

@dataclass(frozen=True)
class AmbiguousBaseB:
    pass

@dataclass(frozen=True)
class AmbiguousPartA(AmbiguousBaseA, AmbiguousBaseB):
    pass

@dataclass(frozen=True)
class AmbiguousPartB(AmbiguousBaseA, AmbiguousBaseB):
    pass

@dataclass(frozen=True)
class Pair:
    left: Part
    right: Part

@dataclass(frozen=True)
class _BuildSpec:
    part: Part
    factor: float

@dataclass(frozen=True)
class _PartsSpec:
    parts: list[Part]

@dataclass(frozen=True)
class _PairSpec:
    pair: Pair

EXTENSIONS = {
    "make": None,
    "make_static": None,
    "make_object": None,
    "make_alpha": None,
    "make_beta": None,
    "make_other": None,
    "make_ambiguous_a": None,
    "make_ambiguous_b": None,
    "ignore": None,
    "runtime_value": ".backend:runtime_value",
    "make_empty_parts": None,
    "make_other_empty": None,
    "make_nested_empty": None,
    "make_parts": None,
    "select_first": None,
    "pair_ab": None,
    "pair_ba": None,
    "consume": ".backend:consume",
    "consume_pair": ".backend:consume_pair",
    "consume_many": ".backend:consume_many",
    "consume_nested": ".backend:consume_nested",
    "consume_star": ".backend:consume_star",
}

def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
def make_static(value: Annotated[Float, EvaluationMode.COMPILE_TIME_ONLY]) -> Part: ...
def make_object(value: Annotated[Object, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
def make_alpha(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> AlphaPart: ...
def make_beta(value: Annotated[Float, EvaluationMode.COMPILE_TIME_ONLY]) -> BetaPart: ...
def make_other(value: Annotated[Float, EvaluationMode.COMPILE_TIME_ONLY]) -> Other: ...
def make_ambiguous_a() -> AmbiguousPartA: ...
def make_ambiguous_b() -> AmbiguousPartB: ...
def ignore(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
def runtime_value(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
def make_empty_parts() -> list[Part]: ...
def make_other_empty() -> list[Other]: ...
def make_nested_empty() -> list[list[Part]]: ...
def make_parts(left: Part, right: Part) -> list[Part]: ...
def select_first(*parts: Part) -> Part: ...
def pair_ab(left: Part, right: Part) -> Pair: ...
def pair_ba(right: Part, left: Part) -> Pair: ...
def consume(part: Part) -> Float: ...
def consume_pair(pair: Pair) -> Float: ...
def consume_many(parts: list[Part]) -> Float: ...
def consume_nested(parts: list[list[Part]]) -> Float: ...
def consume_star(*parts: Part) -> Float: ...
'''

SEMANTIC = '''
from .interface import (
    Part, AlphaPart, BetaPart, Other, AmbiguousPartA, AmbiguousPartB,
    Pair, _BuildSpec, _PartsSpec, _PairSpec,
)

def make(value) -> Part:
    return Part(value)

def make_static(value) -> Part:
    return Part(value)

def make_object(value) -> Part:
    return Part(value)

def make_alpha(value) -> AlphaPart:
    return AlphaPart(value)

def make_beta(value) -> BetaPart:
    return BetaPart(value)

def make_other(value) -> Other:
    return Other(value)

def make_ambiguous_a() -> AmbiguousPartA:
    return AmbiguousPartA()

def make_ambiguous_b() -> AmbiguousPartB:
    return AmbiguousPartB()

def ignore(value) -> Part:
    return Part(1.0)

def make_empty_parts() -> list[Part]:
    return []

def make_other_empty() -> list[Other]:
    return []

def make_nested_empty() -> list[list[Part]]:
    return [[]]

def make_parts(left, right) -> list[Part]:
    return [left, right]

def select_first(*parts) -> Part:
    return parts[0]

def pair_ab(left, right) -> Pair:
    return Pair(left, right)

def pair_ba(right, left) -> Pair:
    return Pair(left, right)

def consume(part) -> _BuildSpec:
    return _BuildSpec(part, 3.0)

def consume_pair(pair) -> _PairSpec:
    return _PairSpec(pair)

def consume_many(parts) -> _PartsSpec:
    return _PartsSpec(parts)

def consume_nested(parts) -> _PartsSpec:
    flat = [part for group in parts for part in group]
    return _PartsSpec(flat)

def consume_star(*parts) -> _PartsSpec:
    return _PartsSpec(list(parts))
'''

BACKEND = '''
def runtime_value(context, value):
    return None

def consume(context, semantic_state):
    return None

def consume_pair(context, semantic_state):
    return None

def consume_many(context, semantic_state):
    return None

def consume_nested(context, semantic_state):
    return None

def consume_star(context, semantic_state):
    return None
'''


def _registry(tmp_path: Path, *, owner_key=("system", "vendor.semantic", "records")):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "interface.py").write_text(INTERFACE, encoding="utf-8")
    (tmp_path / "semantic.py").write_text(SEMANTIC, encoding="utf-8")
    (tmp_path / "backend.py").write_text(BACKEND, encoding="utf-8")
    session = ExtensionOwnerSession(capture_owner_code_snapshot(owner_key, tmp_path))
    registry = ExtensionRegistry((session,))
    specs, _refs = session.normalize_interface()
    by_name = {callable_id.name: callable_id for callable_id in specs}
    return session, registry, by_name


def _environment(registry, by_name):
    runtime = {"x": RuntimeBindingSymbol(BindingId("scope", 0), NFType.INT)}
    exports = {
        name: PackageCallableExport("vendor.semantic", name, extension_callable_id=callable_id)
        for name, callable_id in by_name.items()
    }
    namespace = ResolvedPackageNamespace("vendor.semantic", "semantic", "Semantic", "1.0.0", exports)
    callables = CallableEnvironment(
        callable_builtins=frozenset(),
        local_functions={},
        imported_functions={},
        package_namespaces={"semantic": PackageNamespaceBinding("semantic", namespace)},
    )
    return SemanticEnvironment(
        MappingProxyType(runtime),
        MappingProxyType({}),
        MappingProxyType({}),
        MappingProxyType({}),
        callables,
        extension_registry=registry,
    )



def test_semantic_record_list_literal_infers_common_nominal_base_and_preserves_order_and_dependencies(tmp_path):
    """Sibling semantic records form one generic LIST[common-base] payload in source order."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("[make_alpha(x), make_beta(2.0)]", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, by_name))
    fact = analysis.facts[root]

    payload = fact.semantic_payload
    assert isinstance(payload, ExtensionSemanticPayload)
    assert payload.type_spec.kind == "LIST"
    assert payload.type_spec.item.kind == "RECORD"
    assert payload.type_spec.item.record_type.name == "Part"
    assert [item.type_id.name for item in payload.value] == ["AlphaPart", "BetaPart"]
    assert len(payload.dependencies) == 1
    assert payload.dependencies[0].typ is NFType.INT
    assert payload.dependencies[0].source is root.elts[0].args[0]
    assert isinstance(payload.value[0].storage[0], ExtensionDependencySlot)
    assert payload.value[0].storage[0].dependency_index == 0


def test_semantic_record_list_literal_static_elements_have_no_runtime_dependencies(tmp_path):
    """A fully static semantic record list does not invent a runtime dependency carrier."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("[make_static(1.0), make_static(2.0)]", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, by_name))
    payload = analysis.facts[root].semantic_payload
    assert payload is not None
    assert payload.dependencies == ()
    assert [item.type_id.name for item in payload.value] == ["Part", "Part"]


def test_semantic_record_list_literal_rejects_mixed_semantic_and_ordinary_runtime_values(tmp_path):
    """A semantic list literal fails closed instead of falling back to structural/runtime lowering."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("[make_alpha(x), x]", mode="eval").body
    with pytest.raises(CompileError, match="cannot mix package semantic values with ordinary values"):
        analyze_expression(root, _environment(registry, by_name))


def test_semantic_record_list_literal_rejects_unrelated_nominal_types(tmp_path):
    """Semantic records without one common nominal base produce a controlled compiler diagnostic."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("[make_alpha(x), make_other(2.0)]", mode="eval").body
    with pytest.raises(CompileError, match="no common nominal record base"):
        analyze_expression(root, _environment(registry, by_name))


def test_semantic_record_list_literal_rejects_ambiguous_common_nominal_bases(tmp_path):
    """Multiple incomparable common nominal bases are rejected deterministically."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("[make_ambiguous_a(), make_ambiguous_b()]", mode="eval").body
    with pytest.raises(CompileError, match="ambiguous common nominal record bases"):
        analyze_expression(root, _environment(registry, by_name))


def test_ordinary_list_literal_remains_structural_array_without_semantic_payload(tmp_path):
    """Lists with no package semantic elements keep the existing structural-array analysis path."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("[x, 1]", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, by_name))
    fact = analysis.facts[root]
    assert fact.semantic_payload is None
    assert isinstance(fact.result_shape, ArrayResultShape)
    assert len(fact.result_shape.items) == 2


def test_common_nominal_base_rejects_cross_owner_record_types(tmp_path):
    """Semantic list inference cannot merge nominal records from different extension owners."""
    session_a, _registry_a, _ = _registry(
        tmp_path / "owner_a",
        owner_key=("system", "vendor.semantic.a", "records"),
    )
    session_b, _registry_b, _ = _registry(
        tmp_path / "owner_b",
        owner_key=("system", "vendor.semantic.b", "records"),
    )
    registry = ExtensionRegistry((session_a, session_b))
    alpha = next(type_id for type_id in session_a.type_specs() if type_id.name == "AlphaPart")
    beta = next(type_id for type_id in session_b.type_specs() if type_id.name == "BetaPart")
    with pytest.raises(CompileError, match="one extension owner"):
        registry.most_specific_common_nominal_base((alpha, beta))


def test_contextual_nested_semantic_list_keeps_empty_literal_typing(tmp_path):
    """Consumer-declared nested LIST typing still gives an empty inner literal its semantic type."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("consume_nested([[make(x)], []])", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, by_name))
    assert analysis.facts[root].analyzed_call is not None

def test_semantic_only_record_and_semantic_then_backend_reuse_runtime_dependency(tmp_path):
    """A nested semantic child stays frontend-only while its dependency becomes an ordinary parent IR operand."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("consume(make(x))", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, by_name))

    child = root.args[0]
    child_fact = analysis.facts[child]
    assert isinstance(child_fact.result_shape, ExtensionResultShape)
    assert isinstance(child_fact.semantic_payload, ExtensionSemanticPayload)
    assert child_fact.analyzed_call is None
    assert child_fact.semantic_payload.dependencies[0].typ is NFType.INT

    root_fact = analysis.facts[root]
    assert root_fact.semantic_payload is None
    assert root_fact.analyzed_call.extension_state_type.kind == "RECORD"
    assert root_fact.analyzed_call.runtime_operands[0].typ is NFType.INT
    assert root_fact.analyzed_call.runtime_operands[0].parameter_index is None

    program = lower_analyzed_expression(root, analysis)
    calls = [operation for operation in program.operations if isinstance(operation, IRCall)]
    assert len(calls) == 1
    call = calls[0]
    assert call.extension_state_type.kind == "RECORD"
    assert isinstance(call.extension_state, ExtensionValue)
    # _BuildSpec.part.value is the runtime dependency after detachment.
    part = call.extension_state.storage[0]
    assert isinstance(part, ExtensionValue)
    assert isinstance(part.storage[0], IRCallOperandRef)
    assert part.storage[0].operand_index == 0
    assert call.arguments[0].value.typ is NFType.INT


def test_empty_semantic_list_keeps_declared_typespec_and_is_consumable(tmp_path):
    """Empty semantic list cardinality must not erase its record element contract."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("make_empty_parts()", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, by_name))
    fact = analysis.facts[root]
    assert isinstance(fact.result_shape, ArrayResultShape)
    assert fact.result_shape.items == ()
    assert fact.semantic_payload.type_spec.kind == "LIST"
    assert fact.semantic_payload.type_spec.item.kind == "RECORD"
    assert fact.semantic_payload.value == ()

    outer = ast.parse("consume_many(make_empty_parts())", mode="eval").body
    outer_analysis = analyze_expression(outer, _environment(registry, by_name))
    assert outer_analysis.facts[outer].analyzed_call.extension_state_type.kind == "RECORD"


def test_empty_incompatible_semantic_list_is_rejected(tmp_path):
    """An empty list still carries enough semantic type information to reject the wrong record family."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("consume_many(make_other_empty())", mode="eval").body
    with pytest.raises(CompileError, match="incompatible"):
        analyze_expression(root, _environment(registry, by_name))


def test_nested_empty_semantic_list_preserves_recursive_typespec(tmp_path):
    """Nested empty lists retain recursive LIST TypeSpec independently of concrete element shape."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("make_nested_empty()", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, by_name))
    payload = analysis.facts[root].semantic_payload
    assert payload.type_spec.kind == "LIST"
    assert payload.type_spec.item.kind == "LIST"
    assert payload.type_spec.item.item.kind == "RECORD"
    assert payload.value == ((),)

    outer = ast.parse("consume_nested(make_nested_empty())", mode="eval").body
    outer_analysis = analyze_expression(outer, _environment(registry, by_name))
    assert outer_analysis.facts[outer].analyzed_call is not None


def test_contextual_empty_list_is_semantic_but_plain_empty_list_is_not(tmp_path):
    """Only a semantic LIST consumer supplies the empty literal's semantic contract."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("consume_many([])", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, by_name))
    list_node = root.args[0]
    assert analysis.facts[list_node].semantic_payload.type_spec.kind == "LIST"

    plain = ast.parse("[]", mode="eval").body
    plain_analysis = analyze_expression(plain, _environment(registry, by_name))
    assert plain_analysis.facts[plain].semantic_payload is None
    assert plain_analysis.facts[plain].result_shape.items == ()


def test_semantic_static_parameter_arrives_detached_without_runtime_dependency(tmp_path):
    """Compile-time-only public NF arguments remain static semantic values rather than RuntimeRefs."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("make_static(2)", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, by_name))
    payload = analysis.facts[root].semantic_payload
    assert payload.dependencies == ()
    assert isinstance(payload.value, ExtensionValue)
    assert payload.value.storage == (2.0,)


def test_semantic_state_ir_validator_rejects_in_range_incompatible_operand_type():
    """Tagged operand refs are validated against their paired NF_SET rather than range alone."""
    from NodeForge.extension_contracts import ExtensionTypeId, TypeSpec
    from NodeForge.semantic.ir import IRCallArgument, IRCallOperandRef, IRValue, validate_extension_ir_state

    spec = TypeSpec("NF_SET", frozenset({NFType.FLOAT}))
    arguments = (IRCallArgument(None, IRValue(0, NFType.VECTOR)),)
    with pytest.raises(TypeError, match="incompatible"):
        validate_extension_ir_state(spec, IRCallOperandRef(0), arguments, registry=None)


def test_dependency_slot_actual_type_mismatch_fails_before_ir_emission():
    """Frontend slot type metadata cannot drift from the corresponding analyzed runtime operand."""
    from NodeForge.semantic.call_resolution import AnalyzedCallOperand
    from NodeForge.semantic.lowering import _detach_extension_state

    slot = ExtensionDependencySlot(0, NFType.INT)
    operands = (AnalyzedCallOperand(None, NFType.FLOAT),)
    with pytest.raises(CompileError, match="actual type changed"):
        _detach_extension_state(slot, operands)



def test_semantic_payload_compaction_drops_discarded_runtime_dependency(tmp_path):
    """A RuntimeRef discarded by semantic code is not live runtime demand."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("ignore(x)", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, by_name))
    payload = analysis.facts[root].semantic_payload
    assert payload.dependencies == ()
    assert isinstance(payload.value, ExtensionValue)
    assert payload.value.storage == (1.0,)


def test_discarded_nested_runtime_extension_call_is_not_lowered(tmp_path):
    """A nested runtime extension call ignored by semantic state produces no nested IRCall."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("consume(ignore(runtime_value(x)))", mode="eval").body
    analysis = analyze_expression(root, _environment(registry, by_name))
    program = lower_analyzed_expression(root, analysis)
    calls = [operation for operation in program.operations if isinstance(operation, IRCall)]
    assert len(calls) == 1
    assert calls[0].target.name == "consume"
    assert calls[0].arguments == ()


def test_payload_dependency_order_is_first_semantic_use_not_acquisition_order():
    """Canonical payload identity ignores transient dependency acquisition history."""
    a = ast.Name(id="a", ctx=ast.Load())
    b = ast.Name(id="b", ctx=ast.Load())
    sources = (
        ExtensionDependencySource(b, NFType.FLOAT),
        ExtensionDependencySource(a, NFType.INT),
    )
    type_spec = TypeSpec("RECORD", record_type=ExtensionTypeId(("system", "test"), "Pair"))
    value = ExtensionValue(
        type_spec.record_type,
        (ExtensionDependencySlot(1, NFType.INT), ExtensionDependencySlot(0, NFType.FLOAT)),
    )
    payload = compact_extension_semantic_payload(type_spec, value, sources)
    assert payload.dependencies == (sources[1], sources[0])
    assert payload.value == ExtensionValue(
        type_spec.record_type,
        (ExtensionDependencySlot(0, NFType.INT), ExtensionDependencySlot(1, NFType.FLOAT)),
    )


def test_dependency_source_uses_ast_identity_and_bindingid_value_semantics():
    """Frozen dataclass equality preserves exact AST occurrences and canonical BindingId identity."""
    same_ast = ast.Name(id="x", ctx=ast.Load())
    other_ast = ast.Name(id="x", ctx=ast.Load())
    first_ast_source = ExtensionDependencySource(same_ast, NFType.INT)
    same_ast_source = ExtensionDependencySource(same_ast, NFType.INT)
    other_ast_source = ExtensionDependencySource(other_ast, NFType.INT)
    assert first_ast_source == same_ast_source
    assert hash(first_ast_source) == hash(same_ast_source)
    assert first_ast_source != other_ast_source
    first_binding_source = ExtensionDependencySource(BindingId("scope", 1), NFType.INT)
    same_binding_source = ExtensionDependencySource(BindingId("scope", 1), NFType.INT)
    assert first_binding_source == same_binding_source
    assert hash(first_binding_source) == hash(same_binding_source)
    assert ExtensionDependencySource(BindingId("scope", 1), NFType.INT) != ExtensionDependencySource(
        BindingId("scope", 2), NFType.INT
    )
    assert ExtensionDependencySource(same_ast, NFType.INT) != ExtensionDependencySource(same_ast, NFType.FLOAT)


def test_payload_compaction_deduplicates_equal_dependency_sources():
    """Canonical payload identity contains one dependency entry per actual runtime source identity."""
    source = ExtensionDependencySource(BindingId("scope", 9), NFType.INT)
    type_spec = TypeSpec("RECORD", record_type=ExtensionTypeId(("system", "test"), "Pair"))
    value = ExtensionValue(
        type_spec.record_type,
        (ExtensionDependencySlot(0, NFType.INT), ExtensionDependencySlot(1, NFType.INT)),
    )
    payload = compact_extension_semantic_payload(type_spec, value, (source, source))
    assert payload.dependencies == (source,)
    assert payload.value.storage == (
        ExtensionDependencySlot(0, NFType.INT),
        ExtensionDependencySlot(0, NFType.INT),
    )


def test_payload_constructor_rejects_duplicate_dependency_sources():
    """Noncanonical duplicate dependency-table entries are rejected at the payload boundary."""
    source = ExtensionDependencySource(BindingId("scope", 9), NFType.INT)
    type_spec = TypeSpec("RECORD", record_type=ExtensionTypeId(("system", "test"), "Pair"))
    value = ExtensionValue(
        type_spec.record_type,
        (ExtensionDependencySlot(0, NFType.INT), ExtensionDependencySlot(1, NFType.INT)),
    )
    with pytest.raises(CompileError, match="not canonical"):
        ExtensionSemanticPayload(type_spec, value, (source, source))


def test_payload_constructor_rejects_unreferenced_dependency_history():
    """Canonical payloads cannot retain acquisition-only dependency entries."""
    source = ExtensionDependencySource(ast.Name(id="x", ctx=ast.Load()), NFType.FLOAT)
    type_spec = TypeSpec("RECORD", record_type=ExtensionTypeId(("system", "test"), "Part"))
    with pytest.raises(CompileError, match="not canonical"):
        ExtensionSemanticPayload(type_spec, ExtensionValue(type_spec.record_type, (1.0,)), (source,))



def test_caller_side_semantic_list_star_expands_before_signature_binding(tmp_path):
    """A semantic LIST expands in source order into declared semantic variadic parameters."""
    _session, registry, by_name = _registry(tmp_path)
    runtime = {
        "x": RuntimeBindingSymbol(BindingId("scope", 0), NFType.INT),
        "y": RuntimeBindingSymbol(BindingId("scope", 1), NFType.INT),
    }
    env = _environment(registry, by_name)
    env = SemanticEnvironment(
        MappingProxyType(runtime),
        env.constants,
        env.const_eval_values,
        env.reserved_name_labels,
        env.callable_environment,
        extension_registry=registry,
    )
    root = ast.parse("consume_star(*make_parts(make(x), make(y)))", mode="eval").body
    analysis = analyze_expression(root, env)
    program = lower_analyzed_expression(root, analysis)
    calls = [operation for operation in program.operations if isinstance(operation, IRCall)]
    assert len(calls) == 1
    call = calls[0]
    assert call.target.name == "consume_star"
    assert [argument.value.typ for argument in call.arguments] == [NFType.INT, NFType.INT]
    parts = call.extension_state.storage[0]
    assert [item.storage[0].operand_index for item in parts] == [0, 1]


def test_semantic_list_star_children_are_dependency_compact_independently(tmp_path):
    """Star expansion does not attach sibling dependencies to an element-local semantic payload."""
    _session, registry, by_name = _registry(tmp_path)
    runtime = {
        "x": RuntimeBindingSymbol(BindingId("scope", 0), NFType.INT),
        "y": RuntimeBindingSymbol(BindingId("scope", 1), NFType.INT),
    }
    base = _environment(registry, by_name)
    env = SemanticEnvironment(
        MappingProxyType(runtime),
        base.constants,
        base.const_eval_values,
        base.reserved_name_labels,
        base.callable_environment,
        extension_registry=registry,
    )
    root = ast.parse("select_first(*make_parts(make(x), make(y)))", mode="eval").body
    analysis = analyze_expression(root, env)
    payload = analysis.facts[root].semantic_payload
    assert payload is not None
    assert len(payload.dependencies) == 1
    assert payload.dependencies[0].source.id == "x"
    assert payload.value.storage == (ExtensionDependencySlot(0, NFType.INT),)


def test_caller_side_semantic_list_star_rejects_nonsemantic_operand(tmp_path):
    """Caller-side star remains restricted to package semantic LIST payloads."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("consume_star(*[1, 2])", mode="eval").body
    with pytest.raises(CompileError, match="package semantic LIST"):
        analyze_expression(root, _environment(registry, by_name))



def test_caller_side_semantic_list_star_preserves_mixed_source_order(tmp_path):
    """Ordinary and starred semantic arguments preserve Python positional expansion order."""
    _session, registry, by_name = _registry(tmp_path)
    runtime = {
        "x": RuntimeBindingSymbol(BindingId("scope", 0), NFType.INT),
        "y": RuntimeBindingSymbol(BindingId("scope", 1), NFType.INT),
        "z": RuntimeBindingSymbol(BindingId("scope", 2), NFType.INT),
        "w": RuntimeBindingSymbol(BindingId("scope", 3), NFType.INT),
    }
    base = _environment(registry, by_name)
    env = SemanticEnvironment(
        MappingProxyType(runtime),
        base.constants,
        base.const_eval_values,
        base.reserved_name_labels,
        base.callable_environment,
        extension_registry=registry,
    )
    root = ast.parse(
        "consume_star(make(x), *make_parts(make(y), make(z)), make(w))",
        mode="eval",
    ).body
    analysis = analyze_expression(root, env)
    program = lower_analyzed_expression(root, analysis)
    call = next(operation for operation in program.operations if isinstance(operation, IRCall))
    parts = call.extension_state.storage[0]
    assert [item.storage[0].operand_index for item in parts] == [0, 1, 2, 3]


def test_caller_side_star_rejects_semantic_record_not_list(tmp_path):
    """Caller-side star cannot reinterpret one semantic RECORD as a semantic LIST."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("consume_star(*make(x))", mode="eval").body
    with pytest.raises(CompileError, match="package semantic LIST"):
        analyze_expression(root, _environment(registry, by_name))

def test_double_star_remains_unsupported_for_semantic_extensions(tmp_path):
    """persistent extension semantic-value adds only caller-side semantic LIST star expansion, not ** mapping expansion."""
    _session, registry, by_name = _registry(tmp_path)
    root = ast.parse("consume_star(**{})", mode="eval").body
    with pytest.raises(CompileError, match=r"caller-side \*\*"):
        analyze_expression(root, _environment(registry, by_name))


def test_dependency_slot_rejects_negative_index_at_storage_boundary():
    """Detached storage rejects a negative dependency index before payload canonicalization."""
    with pytest.raises(ValueError, match="non-negative"):
        ExtensionDependencySlot(-1, NFType.FLOAT)
