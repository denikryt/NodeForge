"""Unit coverage for the semantic-analysis stage before Semantic IR emission."""

import ast
import dataclasses
from types import MappingProxyType

import pytest

from NodeForge.constants import (
    TYPE_BOOL,
    TYPE_BUNDLE,
    TYPE_FLOAT,
    TYPE_GEOMETRY,
    TYPE_INT,
    TYPE_MATERIAL,
    TYPE_OBJECT,
    TYPE_STRING,
    TYPE_VECTOR,
)
from NodeForge.errors import CompileError
from NodeForge.semantic_analysis import SemanticEnvironment, analyze_expression


pytestmark = pytest.mark.unit


def _expr(source):
    """Parse one expression fixture."""
    return ast.parse(source, mode="eval").body


def _env(*, bindings=None, legacy=(), scalars=None, unsupported=(), labels=None):
    """Construct the immutable semantic snapshot used by analyzer tests."""
    return SemanticEnvironment(
        MappingProxyType(dict(bindings or {})),
        frozenset(legacy),
        MappingProxyType(dict(scalars or {})),
        frozenset(unsupported),
        MappingProxyType(dict(labels or {})),
    )


def _analyze(source, **kwargs):
    """Analyze one parsed expression with a compact semantic environment."""
    return analyze_expression(_expr(source), _env(**kwargs))


def test_environment_mapping_and_set_contract_is_structurally_immutable():
    env = _env(bindings={"a": TYPE_FLOAT}, legacy={"legacy"}, scalars={"k": (TYPE_FLOAT, 1)}, unsupported={"v"}, labels={"f": "DSL builtin"})
    with pytest.raises(TypeError):
        env.runtime_binding_types["b"] = TYPE_FLOAT
    with pytest.raises(TypeError):
        env.scalar_constants["x"] = (TYPE_FLOAT, 2)
    with pytest.raises(TypeError):
        env.reserved_name_labels["x"] = "DSL builtin"
    with pytest.raises(dataclasses.FrozenInstanceError):
        env.legacy_binding_names = frozenset()



def test_analysis_fact_table_is_immutable_and_ast_associated_only():
    analysis = _analyze("a + 1", bindings={"a": TYPE_FLOAT})
    assert analysis.root in analysis.facts
    with pytest.raises(TypeError):
        analysis.facts[analysis.root] = analysis.facts[analysis.root]


def test_reached_name_resolution_precedence_is_exact():
    analysis = _analyze(
        "x",
        bindings={"x": TYPE_VECTOR},
        legacy={"x"},
        scalars={"x": (TYPE_FLOAT, 1)},
        unsupported={"x"},
        labels={"x": "DSL builtin"},
    )
    assert analysis.facts[analysis.root].typ == TYPE_VECTOR
    assert analysis.facts[analysis.root].resolved_name.kind == "runtime_binding"

    assert _analyze("x", legacy={"x"}, scalars={"x": (TYPE_FLOAT, 1)}) is None
    assert _analyze("pi", unsupported={"pi"}) is None


def test_scalar_constant_resolution_uses_preclassified_type_and_literal():
    cases = [
        ((TYPE_BOOL, True), TYPE_BOOL, True),
        ((TYPE_FLOAT, 7), TYPE_FLOAT, 7),
        ((TYPE_FLOAT, 1.5), TYPE_FLOAT, 1.5),
        ((TYPE_STRING, "s"), TYPE_STRING, "s"),
    ]
    for stored, typ, value in cases:
        analysis = _analyze("k", scalars={"k": stored})
        fact = analysis.facts[analysis.root]
        assert fact.typ == typ
        assert fact.literal_value == value
        assert fact.resolved_name.kind == "scalar_constant"


def test_allowed_constant_and_reserved_and_unknown_diagnostics():
    pi = _analyze("pi")
    assert pi.facts[pi.root].typ == TYPE_FLOAT
    assert pi.facts[pi.root].resolved_name.kind == "allowed_constant"
    with pytest.raises(CompileError, match="registered as DSL builtin"):
        _analyze("foo", labels={"foo": "DSL builtin"})
    with pytest.raises(CompileError, match="Unknown name: missing"):
        _analyze("missing")


def test_type_token_diagnostic_is_preserved():
    with pytest.raises(CompileError, match="Type token Float may only be used"):
        _analyze("Float")


def test_unsupported_left_short_circuits_before_unknown_right():
    assert _analyze("legacy_call() + unknown_name") is None
    with pytest.raises(CompileError, match="Unknown name: unknown_name"):
        _analyze("a + unknown_name", bindings={"a": TYPE_FLOAT})


def test_binary_validation_and_normalization_match_current_matrix():
    cases = [
        ("a + b", TYPE_FLOAT, TYPE_FLOAT, "ADD", TYPE_FLOAT),
        ("a + b", TYPE_INT, TYPE_INT, "ADD", TYPE_FLOAT),
        ("a + b", TYPE_VECTOR, TYPE_VECTOR, "ADD", TYPE_VECTOR),
        ("a * b", TYPE_VECTOR, TYPE_FLOAT, "MULTIPLY", TYPE_VECTOR),
        ("a * b", TYPE_FLOAT, TYPE_VECTOR, "MULTIPLY", TYPE_VECTOR),
        ("a * b", TYPE_VECTOR, TYPE_VECTOR, "MULTIPLY", TYPE_VECTOR),
        ("a / b", TYPE_VECTOR, TYPE_FLOAT, "DIVIDE", TYPE_VECTOR),
    ]
    for source, left, right, op, typ in cases:
        analysis = _analyze(source, bindings={"a": left, "b": right})
        fact = analysis.facts[analysis.root]
        assert (fact.operation, fact.typ) == (op, typ)
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and FLOAT"):
        _analyze("a + b", bindings={"a": TYPE_BOOL, "b": TYPE_FLOAT})


def test_unary_validation_and_result_types_match_current_semantics():
    for typ in [TYPE_FLOAT, TYPE_INT, TYPE_VECTOR, TYPE_BOOL, TYPE_GEOMETRY, TYPE_MATERIAL, TYPE_OBJECT, TYPE_STRING, TYPE_BUNDLE]:
        analysis = _analyze("+a", bindings={"a": typ})
        assert analysis.facts[analysis.root].typ == typ
        assert analysis.facts[analysis.root].operation == "+"
    scalar_neg = _analyze("-a", bindings={"a": TYPE_FLOAT})
    vector_neg = _analyze("-v", bindings={"v": TYPE_VECTOR})
    boolean_not = _analyze("not flag", bindings={"flag": TYPE_BOOL})
    assert scalar_neg.facts[scalar_neg.root].typ == TYPE_FLOAT
    assert vector_neg.facts[vector_neg.root].typ == TYPE_VECTOR
    assert boolean_not.facts[boolean_not.root].typ == TYPE_BOOL
    with pytest.raises(CompileError, match="not expects Bool"):
        _analyze("not a", bindings={"a": TYPE_FLOAT})


def test_boolean_validation_preserves_pairwise_first_error_order():
    ok = _analyze("a and b and c", bindings={"a": TYPE_BOOL, "b": TYPE_BOOL, "c": TYPE_BOOL})
    assert ok.facts[ok.root].operation == "AND"
    with pytest.raises(CompileError, match="Boolean operations expect Bool values"):
        _analyze("True and 1 and (True + 1)")
    with pytest.raises(CompileError, match="Boolean operations expect Bool values"):
        _analyze("True and 1 and legacy_call()")


def test_comparison_fact_stores_all_normalized_operations_in_source_order():
    analysis = _analyze(
        "a < b == c >= d",
        bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT, "d": TYPE_FLOAT},
    )
    fact = analysis.facts[analysis.root]
    assert fact.compare_operations == ("LESS_THAN", "EQUAL", "GREATER_EQUAL")
    assert len(fact.compare_operations) == len(analysis.root.ops)
    with pytest.raises(CompileError, match="Comparison inputs must both be numeric"):
        _analyze("a == b", bindings={"a": TYPE_BOOL, "b": TYPE_VECTOR})


def test_conditional_validation_and_switch_supported_boundary():
    analysis = _analyze("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_VECTOR, "b": TYPE_VECTOR})
    assert analysis.facts[analysis.root].typ == TYPE_VECTOR
    with pytest.raises(CompileError, match="cond must be Bool"):
        _analyze("a if flag else b", bindings={"flag": TYPE_FLOAT, "a": TYPE_FLOAT, "b": TYPE_FLOAT})
    with pytest.raises(CompileError, match="same type"):
        _analyze("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_FLOAT, "b": TYPE_VECTOR})
    assert _analyze("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_MATERIAL, "b": TYPE_MATERIAL}) is None


def test_vector_component_and_object_attribute_boundaries():
    analysis = _analyze("v.x", bindings={"v": TYPE_VECTOR})
    assert analysis.facts[analysis.root].typ == TYPE_FLOAT
    assert analysis.facts[analysis.root].operation == "x"
    with pytest.raises(CompileError, match="can only be used on Vector"):
        _analyze("a.x", bindings={"a": TYPE_FLOAT})
    assert _analyze("obj.geometry", bindings={"obj": TYPE_OBJECT}) is None


def test_other_expression_families_remain_unsupported():
    for source in ["f()", "[a]", "(a,)", "a[0]"]:
        assert _analyze(source, bindings={"a": TYPE_FLOAT}) is None
