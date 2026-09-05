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
from NodeForge.consteval import _const_eval
from NodeForge.compiler_identities import BindingId
from NodeForge.call_resolution import CallableEnvironment
from NodeForge.semantic_analysis import (
    ArrayResultShape, RuntimeBindingSymbol, RuntimeResultShape, SemanticEnvironment,
    analyze_expression, build_semantic_constant_snapshot,
)


pytestmark = pytest.mark.unit

def _empty_callable_environment():
    """Return an empty immutable callable namespace for non-call semantic tests."""
    return CallableEnvironment(frozenset(), {}, {}, frozenset(), {})



def _expr(source):
    """Parse one expression fixture."""
    return ast.parse(source, mode="eval").body


def _env(*, bindings=None, legacy=(), consts=None, labels=None, backend_helpers=(), builtins=(), systems=None, local_functions=None, imported_functions=None, **_ignored):
    """Construct the immutable semantic snapshot used by analyzer tests."""
    runtime_bindings = {
        name: RuntimeBindingSymbol(BindingId("test-owner", index), typ)
        for index, (name, typ) in enumerate((bindings or {}).items())
    }
    constants, const_eval_values = build_semantic_constant_snapshot(consts or {})
    return SemanticEnvironment(
        MappingProxyType(runtime_bindings),
        frozenset(legacy),
        constants,
        const_eval_values,
        MappingProxyType(dict(labels or {})),
        callable_environment=CallableEnvironment(
            frozenset(builtins),
            systems or {},
            local_functions or {},
            frozenset(backend_helpers),
            imported_functions or {},
        ),
    )

def _typ(fact):
    """Return the runtime type of one runtime-shaped fact."""
    assert isinstance(fact.result_shape, RuntimeResultShape)
    return fact.result_shape.typ


def _analyze(source, **kwargs):
    """Analyze one parsed expression with a compact semantic environment."""
    return analyze_expression(_expr(source), _env(**kwargs))


def test_environment_mapping_and_set_contract_is_structurally_immutable():
    env = _env(bindings={"a": TYPE_FLOAT}, legacy={"legacy"}, consts={"k": 1, "v": object()}, labels={"f": "DSL builtin"})
    with pytest.raises(TypeError):
        env.runtime_bindings["b"] = RuntimeBindingSymbol(BindingId("test-owner", 1), TYPE_FLOAT)
    with pytest.raises(TypeError):
        env.constants["x"] = object()
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
        consts={"x": 1},
        labels={"x": "DSL builtin"},
    )
    assert _typ(analysis.facts[analysis.root]) == TYPE_VECTOR
    assert analysis.facts[analysis.root].resolved_name.kind == "runtime_binding"
    assert analysis.facts[analysis.root].resolved_name.binding_id == BindingId("test-owner", 0)

    assert _analyze("x", legacy={"x"}, consts={"x": 1}) is None
    pi = _analyze("pi")
    assert _typ(pi.facts[pi.root]) == TYPE_FLOAT


def test_semantic_constant_resolution_uses_detached_records():
    cases = [(True, TYPE_BOOL), (7, TYPE_FLOAT), (1.5, TYPE_FLOAT), ("s", TYPE_STRING)]
    for value, typ in cases:
        analysis = _analyze("k", consts={"k": value})
        fact = analysis.facts[analysis.root]
        assert _typ(fact) == typ
        assert fact.resolved_name.kind == "semantic_constant"


def test_constant_snapshot_is_cycle_safe_and_never_retains_unsupported_object_identity():
    """Unused cyclic or opaque legacy constants must not break or leak into semantic analysis."""
    cyclic = [1]
    cyclic.append(cyclic)
    opaque = object()

    constants, const_eval_values = build_semantic_constant_snapshot({
        "cyclic": cyclic,
        "opaque": opaque,
    })

    assert constants["cyclic"].kind == "unsupported"
    assert constants["opaque"].kind == "unsupported"
    assert isinstance(const_eval_values["cyclic"], list)
    assert const_eval_values["cyclic"] is not cyclic
    assert const_eval_values["cyclic"][1] is const_eval_values["cyclic"]
    assert const_eval_values["opaque"] is not opaque

    environment = SemanticEnvironment(
        MappingProxyType({}),
        frozenset(),
        constants,
        const_eval_values,
        MappingProxyType({}),
        callable_environment=_empty_callable_environment(),
    )
    analysis = analyze_expression(_expr("1"), environment)
    assert _typ(analysis.facts[analysis.root]) == TYPE_FLOAT



def test_detached_constant_snapshot_preserves_cycles_aliasing_and_const_eval_semantics():
    """Detached const-eval storage must preserve the supported Python container graph."""
    xs = [1]
    xs.append(xs)
    shared = [2, 3]
    original = {"xs": xs, "left": shared, "right": shared}
    _, detached = build_semantic_constant_snapshot(original)

    assert detached["xs"] is not xs
    assert detached["xs"][1] is detached["xs"]
    assert detached["left"] is detached["right"]
    assert detached["left"] is not shared

    index_expr = _expr("xs[1] == xs")
    assert _const_eval(index_expr, original) is True
    assert _const_eval(index_expr, detached) is True


def test_detached_constant_snapshot_never_invokes_opaque_deepcopy():
    """Opaque compiler/backend leaves are replaced before deepcopy can execute user hooks."""
    class Opaque:
        def __deepcopy__(self, memo):
            raise AssertionError("opaque __deepcopy__ must not run")

    opaque = Opaque()
    _, detached = build_semantic_constant_snapshot({"opaque": opaque, "items": [opaque]})
    assert detached["opaque"] is not opaque
    assert detached["items"][0] is detached["opaque"]


def test_detached_constant_snapshot_preserves_distinct_opaque_identities():
    """Distinct unsupported leaves must not collapse to one frontend placeholder."""
    x = object()
    y = object()
    _, detached = build_semantic_constant_snapshot({"x": x, "x_alias": x, "y": y})

    assert detached["x"] is detached["x_alias"]
    assert detached["x"] is not detached["y"]
    assert _const_eval(_expr("x == y"), detached) is False
    assert _const_eval(_expr("x == x_alias"), detached) is True

def test_cyclic_constant_is_reported_only_when_used_as_runtime_value():
    """Cycle detection keeps the existing unsupported-runtime-constant diagnostic on use."""
    cyclic = [1]
    cyclic.append(cyclic)
    with pytest.raises(CompileError, match="Unsupported compile-time value in runtime expression"):
        _analyze("cyclic", consts={"cyclic": cyclic})

def test_allowed_constant_and_reserved_and_unknown_diagnostics():
    pi = _analyze("pi")
    assert _typ(pi.facts[pi.root]) == TYPE_FLOAT
    assert pi.facts[pi.root].resolved_name.kind == "allowed_constant"
    with pytest.raises(CompileError, match="registered as DSL builtin"):
        _analyze("foo", labels={"foo": "DSL builtin"})
    with pytest.raises(CompileError, match="Unknown name: missing"):
        _analyze("missing")


def test_type_token_diagnostic_is_preserved():
    with pytest.raises(CompileError, match="Type token Float may only be used"):
        _analyze("Float")


def test_unsupported_left_short_circuits_before_unknown_right():
    assert _analyze("legacy_call() + unknown_name", backend_helpers={"legacy_call"}) is None
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
        assert (fact.operation, _typ(fact)) == (op, typ)
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and FLOAT"):
        _analyze("a + b", bindings={"a": TYPE_BOOL, "b": TYPE_FLOAT})


def test_unary_validation_and_result_types_match_current_semantics():
    for typ in [TYPE_FLOAT, TYPE_INT, TYPE_VECTOR, TYPE_BOOL, TYPE_GEOMETRY, TYPE_MATERIAL, TYPE_OBJECT, TYPE_STRING, TYPE_BUNDLE]:
        analysis = _analyze("+a", bindings={"a": typ})
        assert _typ(analysis.facts[analysis.root]) == typ
        assert analysis.facts[analysis.root].operation == "+"
    scalar_neg = _analyze("-a", bindings={"a": TYPE_FLOAT})
    vector_neg = _analyze("-v", bindings={"v": TYPE_VECTOR})
    boolean_not = _analyze("not flag", bindings={"flag": TYPE_BOOL})
    assert _typ(scalar_neg.facts[scalar_neg.root]) == TYPE_FLOAT
    assert _typ(vector_neg.facts[vector_neg.root]) == TYPE_VECTOR
    assert _typ(boolean_not.facts[boolean_not.root]) == TYPE_BOOL
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
    assert _typ(analysis.facts[analysis.root]) == TYPE_VECTOR
    with pytest.raises(CompileError, match="cond must be Bool"):
        _analyze("a if flag else b", bindings={"flag": TYPE_FLOAT, "a": TYPE_FLOAT, "b": TYPE_FLOAT})
    with pytest.raises(CompileError, match="same type"):
        _analyze("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_FLOAT, "b": TYPE_VECTOR})
    assert _analyze("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_MATERIAL, "b": TYPE_MATERIAL}) is None


def test_vector_component_and_object_attribute_boundaries():
    analysis = _analyze("v.x", bindings={"v": TYPE_VECTOR})
    assert _typ(analysis.facts[analysis.root]) == TYPE_FLOAT
    assert analysis.facts[analysis.root].operation == "x"
    with pytest.raises(CompileError, match="can only be used on Vector"):
        _analyze("a.x", bindings={"a": TYPE_FLOAT})
    obj = _analyze("obj.geometry", bindings={"obj": TYPE_OBJECT})
    assert _typ(obj.facts[obj.root]) == TYPE_GEOMETRY


def test_only_calls_and_legacy_bindings_remain_planned_fallbacks():
    assert _analyze("f()", bindings={"a": TYPE_FLOAT}, backend_helpers={"f"}) is None
    assert _analyze("legacy[0]", legacy={"legacy"}) is None
    array = _analyze("[a]", bindings={"a": TYPE_FLOAT})
    assert isinstance(array.facts[array.root].result_shape, ArrayResultShape)
    with pytest.raises(CompileError, match="indexing is supported"):
        _analyze("a[0]", bindings={"a": TYPE_FLOAT})
