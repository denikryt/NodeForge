"""Expression-local package semantic value tests."""

from __future__ import annotations

import ast
from pathlib import Path
from types import MappingProxyType

import pytest

from NodeForge.call_resolution import CallableEnvironment
from NodeForge.compiler_identities import BindingId
from NodeForge.errors import CompileError
from NodeForge.extension_contracts import TypeSpec
from NodeForge.extension_registry import ExtensionOwnerSession, ExtensionRegistry, capture_owner_code_snapshot
from NodeForge.extension_semantics import ExtensionSemanticPayload
from NodeForge.extension_values import (
    ExtensionDependencySlot,
    ExtensionValue,
)
from NodeForge.nf_types import NFType
from NodeForge.runtime_bindings import RuntimeBindingSymbol
from NodeForge.semantic_analysis import SemanticEnvironment, analyze_expression
from NodeForge.semantic_ir import IRCall, IRCallOperandRef
from NodeForge.semantic_lowering import lower_analyzed_expression
from NodeForge.semantic_values import ArrayResultShape, ExtensionResultShape

pytestmark = pytest.mark.unit


INTERFACE = '''
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float, Int

EXTENSION_API = 2

@dataclass(frozen=True)
class Part:
    value: Float | Int

@dataclass(frozen=True)
class Other:
    value: Float

@dataclass(frozen=True)
class _BuildSpec:
    part: Part
    factor: float

@dataclass(frozen=True)
class _PartsSpec:
    parts: list[Part]

EXTENSIONS = {
    "make": None,
    "make_static": None,
    "make_empty_parts": None,
    "make_other_empty": None,
    "make_nested_empty": None,
    "consume": ".backend:consume",
    "consume_many": ".backend:consume_many",
    "consume_nested": ".backend:consume_nested",
}

def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part: ...
def make_static(value: Annotated[Float, EvaluationMode.COMPILE_TIME_ONLY]) -> Part: ...
def make_empty_parts() -> list[Part]: ...
def make_other_empty() -> list[Other]: ...
def make_nested_empty() -> list[list[Part]]: ...
def consume(part: Part) -> Float: ...
def consume_many(parts: list[Part]) -> Float: ...
def consume_nested(parts: list[list[Part]]) -> Float: ...
'''

SEMANTIC = '''
from .interface import Part, Other, _BuildSpec, _PartsSpec

def make(value) -> Part:
    return Part(value)

def make_static(value) -> Part:
    return Part(value)

def make_empty_parts() -> list[Part]:
    return []

def make_other_empty() -> list[Other]:
    return []

def make_nested_empty() -> list[list[Part]]:
    return [[]]

def consume(part) -> _BuildSpec:
    return _BuildSpec(part, 3.0)

def consume_many(parts) -> _PartsSpec:
    return _PartsSpec(parts)

def consume_nested(parts) -> _PartsSpec:
    flat = [part for group in parts for part in group]
    return _PartsSpec(flat)
'''

BACKEND = '''
def consume(context, semantic_state):
    return None

def consume_many(context, semantic_state):
    return None

def consume_nested(context, semantic_state):
    return None
'''


def _registry(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "interface.py").write_text(INTERFACE, encoding="utf-8")
    (tmp_path / "semantic.py").write_text(SEMANTIC, encoding="utf-8")
    (tmp_path / "backend.py").write_text(BACKEND, encoding="utf-8")
    session = ExtensionOwnerSession(capture_owner_code_snapshot(("system", "vendor.semantic", "records"), tmp_path))
    registry = ExtensionRegistry((session,))
    specs, _refs = session.normalize_interface()
    by_name = {callable_id.name: callable_id for callable_id in specs}
    return session, registry, by_name


def _environment(registry, by_name):
    runtime = {"x": RuntimeBindingSymbol(BindingId("scope", 0), NFType.INT)}
    callables = CallableEnvironment(
        callable_builtins=frozenset(),
        system_constructors={},
        local_functions={},
        backend_helper_names=frozenset(),
        imported_functions={},
        extension_system_callables=by_name,
    )
    return SemanticEnvironment(
        MappingProxyType(runtime),
        frozenset(runtime),
        MappingProxyType({}),
        MappingProxyType({}),
        MappingProxyType({}),
        callables,
        extension_registry=registry,
    )


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
    from NodeForge.extension_contracts import TypeSpec
    from NodeForge.semantic_ir import IRCallArgument, IRCallOperandRef, IRValue, validate_extension_ir_state

    spec = TypeSpec("NF_SET", frozenset({NFType.FLOAT}))
    arguments = (IRCallArgument(None, IRValue(0, NFType.VECTOR)),)
    with pytest.raises(TypeError, match="incompatible"):
        validate_extension_ir_state(spec, IRCallOperandRef(0), arguments, registry=None)


def test_dependency_slot_actual_type_mismatch_fails_before_ir_emission():
    """Frontend slot type metadata cannot drift from the corresponding analyzed runtime operand."""
    from NodeForge.call_resolution import AnalyzedCallOperand
    from NodeForge.semantic_lowering import _detach_extension_state

    slot = ExtensionDependencySlot(0, NFType.INT)
    operands = (AnalyzedCallOperand(None, NFType.FLOAT),)
    with pytest.raises(CompileError, match="actual type changed"):
        _detach_extension_state(slot, operands)
