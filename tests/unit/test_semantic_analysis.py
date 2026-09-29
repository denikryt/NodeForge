"""Unit coverage for the semantic-analysis phase before Semantic IR emission."""

import ast
import dataclasses
from types import MappingProxyType

import pytest

from NodeForge.compile_time import CompileTimeSnapshot

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
from NodeForge.semantic_values import (
    ObjectInfoState,
    ObjectSemanticId,
    ObjectSemanticSnapshot,
    StructuralArrayId,
    StructuralArrayRef,
    StructuralArraySnapshot,
    StructuralArrayState,
    StructuralRuntimeLeaf,
)
from NodeForge.semantic_analysis import (
    ArrayResultShape, RuntimeBindingSymbol, RuntimeResultShape, SemanticEnvironment,
    analyze_expression, build_semantic_constant_snapshot,
)


pytestmark = pytest.mark.unit

def _empty_callable_environment():
    """Return an empty immutable callable namespace for non-call semantic tests."""
    return CallableEnvironment(frozenset(), {}, {}, {})



def _expr(source):
    """Parse one expression fixture."""
    return ast.parse(source, mode="eval").body


def _env(*, bindings=None, consts=None, labels=None, backend_helpers=(), builtins=(), systems=None, local_functions=None, imported_functions=None, object_registry=True, structural_arrays=None, **_ignored):
    """Construct the immutable semantic snapshot used by analyzer tests."""
    runtime_bindings = {
        name: RuntimeBindingSymbol(BindingId("test-owner", index), typ)
        for index, (name, typ) in enumerate((bindings or {}).items())
    }
    constants, const_eval_values = build_semantic_constant_snapshot(CompileTimeSnapshot(consts or {}))
    object_semantics = None
    if object_registry:
        object_ids = {}
        states = {}
        next_id = 0
        for symbol in runtime_bindings.values():
            if symbol.typ is TYPE_OBJECT:
                object_id = ObjectSemanticId(next_id)
                next_id += 1
                object_ids[symbol.binding_id] = object_id
                states[object_id] = ObjectInfoState()
        object_semantics = ObjectSemanticSnapshot(object_ids, states, next_id)
    return SemanticEnvironment(
        MappingProxyType(runtime_bindings),
        constants,
        const_eval_values,
        MappingProxyType(dict(labels or {})),
        callable_environment=CallableEnvironment(
            frozenset(builtins),
            local_functions or {},
            imported_functions or {},
            systems or {},
        ),
        structural_arrays=structural_arrays or StructuralArraySnapshot({}, {}),
        object_semantics=object_semantics,
    )

def _typ(fact):
    """Return the runtime type of one runtime-shaped fact."""
    assert isinstance(fact.result_shape, RuntimeResultShape)
    return fact.result_shape.typ


def _analyze(source, **kwargs):
    """Analyze one parsed expression with a compact semantic environment."""
    return analyze_expression(_expr(source), _env(**kwargs))


def test_join_named_structural_array_normalizes_to_ordered_runtime_operands():
    array_id = StructuralArrayId(0)
    snapshot = StructuralArraySnapshot(
        {"items": array_id},
        {
            array_id: StructuralArrayState(
                (
                    StructuralRuntimeLeaf(BindingId("test-owner", 7), TYPE_GEOMETRY),
                    StructuralRuntimeLeaf(BindingId("test-owner", 8), TYPE_GEOMETRY),
                )
            )
        },
    )
    analysis = _analyze(
        "join(items)",
        builtins={"join"},
        structural_arrays=snapshot,
    )
    root_fact = analysis.facts[analysis.root]
    assert [operand.typ for operand in root_fact.analyzed_call.runtime_operands] == [
        TYPE_GEOMETRY,
        TYPE_GEOMETRY,
    ]
    assert len(root_fact.call_operand_nodes) == 2
    assert all(isinstance(node, ast.Subscript) for node in root_fact.call_operand_nodes)


def test_join_empty_named_structural_array_preserves_zero_operand_call():
    array_id = StructuralArrayId(0)
    snapshot = StructuralArraySnapshot(
        {"items": array_id},
        {array_id: StructuralArrayState(())},
    )
    analysis = _analyze(
        "join(items)",
        builtins={"join"},
        structural_arrays=snapshot,
    )
    root_fact = analysis.facts[analysis.root]
    assert root_fact.analyzed_call.runtime_operands == ()
    assert root_fact.call_operand_nodes == ()


def test_environment_mapping_contract_is_structurally_immutable():
    env = _env(bindings={"a": TYPE_FLOAT}, consts={"k": 1, "v": object()}, labels={"f": "DSL builtin"})
    with pytest.raises(TypeError):
        env.runtime_bindings["b"] = RuntimeBindingSymbol(BindingId("test-owner", 1), TYPE_FLOAT)
    with pytest.raises(TypeError):
        env.constants["x"] = object()
    with pytest.raises(TypeError):
        env.reserved_name_labels["x"] = "DSL builtin"
    with pytest.raises(dataclasses.FrozenInstanceError):
        env.helper_namespace = "Other"



def test_analysis_fact_table_is_immutable_and_ast_associated_only():
    analysis = _analyze("a + 1", bindings={"a": TYPE_FLOAT})
    assert analysis.root in analysis.facts
    with pytest.raises(TypeError):
        analysis.facts[analysis.root] = analysis.facts[analysis.root]


def test_reached_name_resolution_precedence_is_exact():
    analysis = _analyze(
        "x",
        bindings={"x": TYPE_VECTOR},
        consts={"x": 1},
        labels={"x": "DSL builtin"},
    )
    assert _typ(analysis.facts[analysis.root]) == TYPE_VECTOR
    assert analysis.facts[analysis.root].resolved_name.kind == "runtime_binding"
    assert analysis.facts[analysis.root].resolved_name.binding_id == BindingId("test-owner", 0)

    constant = _analyze("x", consts={"x": 1})
    assert _typ(constant.facts[constant.root]) == TYPE_INT
    pi = _analyze("pi")
    assert _typ(pi.facts[pi.root]) == TYPE_FLOAT


def test_semantic_constant_resolution_uses_detached_records():
    cases = [(True, TYPE_BOOL), (7, TYPE_INT), (1.5, TYPE_FLOAT), ("s", TYPE_STRING)]
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

    constants, const_eval_values = build_semantic_constant_snapshot(CompileTimeSnapshot({
        "cyclic": cyclic,
        "opaque": opaque,
    }))

    assert constants["cyclic"].kind == "unsupported"
    assert constants["opaque"].kind == "unsupported"
    assert isinstance(const_eval_values["cyclic"], list)
    assert const_eval_values["cyclic"] is not cyclic
    assert const_eval_values["cyclic"][1] is const_eval_values["cyclic"]
    assert const_eval_values["opaque"] is not opaque

    environment = SemanticEnvironment(
        MappingProxyType({}),
        constants,
        const_eval_values,
        MappingProxyType({}),
        callable_environment=_empty_callable_environment(),
    )
    analysis = analyze_expression(_expr("1"), environment)
    assert _typ(analysis.facts[analysis.root]) == TYPE_INT



def test_detached_constant_snapshot_preserves_cycles_aliasing_and_const_eval_semantics():
    """Detached const-eval storage must preserve the supported Python container graph."""
    xs = [1]
    xs.append(xs)
    shared = [2, 3]
    original = {"xs": xs, "left": shared, "right": shared}
    _, detached = build_semantic_constant_snapshot(CompileTimeSnapshot(original))

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
    _, detached = build_semantic_constant_snapshot(CompileTimeSnapshot({"opaque": opaque, "items": [opaque]}))
    assert detached["opaque"] is not opaque
    assert detached["items"][0] is detached["opaque"]


def test_detached_constant_snapshot_preserves_distinct_opaque_identities():
    """Distinct unsupported leaves must not collapse to one frontend placeholder."""
    x = object()
    y = object()
    _, detached = build_semantic_constant_snapshot(CompileTimeSnapshot({"x": x, "x_alias": x, "y": y}))

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


def test_unknown_call_short_circuits_before_unknown_right():
    with pytest.raises(CompileError, match=r"Unsupported function: legacy_call"):
        _analyze("legacy_call() + unknown_name")
    with pytest.raises(CompileError, match="Unknown name: unknown_name"):
        _analyze("a + unknown_name", bindings={"a": TYPE_FLOAT})


def test_binary_validation_and_normalization_match_current_matrix():
    cases = [
        ("a + b", TYPE_FLOAT, TYPE_FLOAT, "ADD", TYPE_FLOAT),
        ("a + b", TYPE_INT, TYPE_INT, "ADD", TYPE_INT),
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
    with pytest.raises(CompileError, match=r"select\(\) result type is not supported"):
        _analyze("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_MATERIAL, "b": TYPE_MATERIAL})


def test_vector_component_and_object_attribute_boundaries():
    analysis = _analyze("v.x", bindings={"v": TYPE_VECTOR})
    assert _typ(analysis.facts[analysis.root]) == TYPE_FLOAT
    assert analysis.facts[analysis.root].operation == "x"
    with pytest.raises(CompileError, match="can only be used on Vector"):
        _analyze("a.x", bindings={"a": TYPE_FLOAT})
    obj = _analyze("obj.geometry", bindings={"obj": TYPE_OBJECT})
    assert _typ(obj.facts[obj.root]) == TYPE_GEOMETRY



def test_object_expression_requires_body_owned_semantic_state_only_when_reached():
    """Object use without its persistent registry is a controlled internal error, not a retry signal."""
    arithmetic = _analyze("1.0 + 2.0", bindings={"obj": TYPE_OBJECT}, object_registry=False)
    assert arithmetic.facts[arithmetic.root].result_shape == RuntimeResultShape(TYPE_FLOAT)
    with pytest.raises(CompileError, match="requires body-owned Object semantic state"):
        _analyze("obj.geometry", bindings={"obj": TYPE_OBJECT}, object_registry=False)

def test_unknown_calls_and_unknown_names_are_direct_errors():
    with pytest.raises(CompileError, match=r"Unsupported function: f"):
        _analyze("f()", bindings={"a": TYPE_FLOAT})
    with pytest.raises(CompileError, match="Unknown name: legacy"):
        _analyze("legacy[0]")
    array = _analyze("[a]", bindings={"a": TYPE_FLOAT})
    assert isinstance(array.facts[array.root].result_shape, ArrayResultShape)
    with pytest.raises(CompileError, match="indexing is supported"):
        _analyze("a[0]", bindings={"a": TYPE_FLOAT})


@pytest.mark.parametrize("source, node_name", [('{"a": 1}', "Dict"), ("[i for i in [1]]", "ListComp")])
def test_unknown_expression_syntax_is_a_direct_semantic_error(source, node_name):
    with pytest.raises(CompileError, match=rf"Unsupported expression element: {node_name}"):
        _analyze(source)


def test_unknown_attribute_is_not_routed_to_legacy_expression_compilation():
    with pytest.raises(CompileError, match=r"Only \.x, \.y and \.z vector attributes are supported"):
        _analyze("a.w", bindings={"a": TYPE_FLOAT})


def test_snapshot_backed_structural_array_analysis_preserves_index_and_nested_identity():
    """Stored arrays reconstruct shapes and carry nested StructuralArrayId through projection facts."""
    first_binding = BindingId("test-owner", 0)
    second_binding = BindingId("test-owner", 1)
    inner_id = StructuralArrayId(0)
    outer_id = StructuralArrayId(1)
    snapshot = StructuralArraySnapshot(
        {"outer": outer_id},
        {
            inner_id: StructuralArrayState((StructuralRuntimeLeaf(first_binding, TYPE_FLOAT),)),
            outer_id: StructuralArrayState((
                StructuralArrayRef(inner_id),
                StructuralRuntimeLeaf(second_binding, TYPE_VECTOR),
            )),
        },
    )
    env = SemanticEnvironment(
        MappingProxyType({
            "first": RuntimeBindingSymbol(first_binding, TYPE_FLOAT),
            "second": RuntimeBindingSymbol(second_binding, TYPE_VECTOR),
        }),
        MappingProxyType({}),
        MappingProxyType({}),
        MappingProxyType({}),
        callable_environment=_empty_callable_environment(),
        structural_arrays=snapshot,
        object_semantics=ObjectSemanticSnapshot({}, {}, 0),
    )

    outer = analyze_expression(_expr("outer"), env)
    assert isinstance(outer.facts[outer.root].result_shape, ArrayResultShape)
    assert outer.facts[outer.root].array_id == outer_id

    nested = analyze_expression(_expr("outer[0]"), env)
    assert nested.facts[nested.root].array_id == inner_id
    assert isinstance(nested.facts[nested.root].result_shape, ArrayResultShape)

    leaf = analyze_expression(_expr("outer[0][0]"), env)
    assert leaf.facts[leaf.root].result_shape == RuntimeResultShape(TYPE_FLOAT)
    assert leaf.facts[leaf.root].array_id is None

    negative = analyze_expression(_expr("outer[-1]"), env)
    assert negative.facts[negative.root].result_shape == RuntimeResultShape(TYPE_VECTOR)

    with pytest.raises(CompileError, match="array index out of range"):
        analyze_expression(_expr("outer[5]"), env)


def test_structural_array_provenance_survives_identity_expression_result_not_only_resolved_name():
    """Array identity is attached to the common expression fact so aliasing survives projection plus unary plus."""
    binding = BindingId("test-owner", 0)
    inner_id = StructuralArrayId(0)
    outer_id = StructuralArrayId(1)
    snapshot = StructuralArraySnapshot(
        {"outer": outer_id},
        {
            inner_id: StructuralArrayState((StructuralRuntimeLeaf(binding, TYPE_FLOAT),)),
            outer_id: StructuralArrayState((StructuralArrayRef(inner_id),)),
        },
    )
    env = SemanticEnvironment(
        MappingProxyType({"x": RuntimeBindingSymbol(binding, TYPE_FLOAT)}),
        MappingProxyType({}),
        MappingProxyType({}),
        MappingProxyType({}),
        callable_environment=_empty_callable_environment(),
        structural_arrays=snapshot,
        object_semantics=ObjectSemanticSnapshot({}, {}, 0),
    )
    analysis = analyze_expression(_expr("+outer[0]"), env)
    assert analysis.facts[analysis.root].array_id == inner_id


def test_compile_time_required_projection_probes_distinguish_unavailable_from_hard_errors():
    """Static projection contracts contextualize only genuine CTFE unavailability."""
    named_bindings = {"key": TYPE_STRING}
    with pytest.raises(CompileError, match="raw node output lookup requires a compile-time string key"):
        _analyze(
            'node("ShaderNodeSeparateXYZ", outputs={"X": Float})[key]',
            bindings=named_bindings,
            builtins={"node"},
        )
    with pytest.raises(CompileError, match="not expects Bool"):
        _analyze(
            'node("ShaderNodeSeparateXYZ", outputs={"X": Float})[not 1]',
            builtins={"node"},
        )

    tuple_bindings = {"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT, "idx": TYPE_INT}
    with pytest.raises(CompileError, match="tuple result indexing requires a compile-time integer index"):
        _analyze(
            "capture_attribute(geo, value)[idx]",
            bindings=tuple_bindings,
            builtins={"capture_attribute"},
        )
    with pytest.raises(CompileError, match="not expects Bool"):
        _analyze(
            "capture_attribute(geo, value)[not 1]",
            bindings={"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT},
            builtins={"capture_attribute"},
        )

    with pytest.raises(CompileError, match="array/vector indexing currently requires a compile-time integer index"):
        _analyze("v[idx]", bindings={"v": TYPE_VECTOR, "idx": TYPE_INT})
    with pytest.raises(CompileError, match="not expects Bool"):
        _analyze("v[not 1]", bindings={"v": TYPE_VECTOR})


@pytest.mark.parametrize("index", ["True", "1.5"])
def test_array_vector_index_requires_exact_compile_time_int(index):
    """Python Bool/subclass or float coercion cannot satisfy the language index contract."""
    with pytest.raises(CompileError, match="array/vector indexing currently requires a compile-time integer index"):
        _analyze(f"v[{index}]", bindings={"v": TYPE_VECTOR})


def test_object_info_static_options_distinguish_unavailable_from_hard_errors():
    """Object.info maps runtime dependence to its static diagnostic without swallowing CTFE errors."""
    with pytest.raises(CompileError, match="transform_space must be 'ORIGINAL' or 'RELATIVE'"):
        _analyze(
            "obj.info(transform_space=space)",
            bindings={"obj": TYPE_OBJECT, "space": TYPE_STRING},
        )
    with pytest.raises(CompileError, match="not expects Bool"):
        _analyze("obj.info(transform_space=not 1)", bindings={"obj": TYPE_OBJECT})

    with pytest.raises(CompileError, match="as_instance must be a compile-time Bool"):
        _analyze(
            "obj.info(as_instance=flag)",
            bindings={"obj": TYPE_OBJECT, "flag": TYPE_BOOL},
        )
    with pytest.raises(CompileError, match="not expects Bool"):
        _analyze("obj.info(as_instance=not 1)", bindings={"obj": TYPE_OBJECT})
