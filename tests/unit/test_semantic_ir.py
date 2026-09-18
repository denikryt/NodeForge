"""Pure value-based Semantic IR contracts and migration-boundary regressions."""

import ast
import dataclasses
import sys
from pathlib import Path
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
from NodeForge.group_context import GROUP_CONTEXT_SPECS, GroupContextSlot
from NodeForge.compiler_identities import BindingId, CallSiteId, local_function_id
from NodeForge.semantic_ir import (
    IRArray,
    IRBinary,
    IRCall,
    IRCallArgument,
    IRCallableKind,
    IRCallableTarget,
    IRNamedOutputs,
    IRRawNodeOutputMode,
    IRTuple,
    IRFunctionMaterialization,
    IRFunctionMaterializationMode,
    IRBinding,
    IRBoolBinary,
    IRCompare,
    IRConditional,
    IRContextRead,
    IRContextWrite,
    IRDiscardExpression,
    IRLiteral,
    IRObjectProperty,
    IRProgram,
    IRBody,
    IRBranchMerge,
    IRIf,
    IRRepeat,
    IRRepeatState,
    IRUnary,
    IRValue,
    IRVectorComponent,
    IRVectorLiteral,
)
from NodeForge.builtin_call_semantics import INPUT_DECLARATION_BUILTIN_NAMES, IR_CAPABLE_BUILTIN_NAMES
from NodeForge.call_resolution import CallableEnvironment, ContextReadCallResult, ProjectedCallResult
from NodeForge.semantic_analysis import (
    RuntimeBindingSymbol,
    SemanticConstant,
    SemanticEnvironment,
    analyze_expression,
    build_semantic_constant_snapshot,
)
from NodeForge.semantic_values import (
    ArrayResultShape,
    ObjectInfoState,
    ObjectSemanticId,
    ObjectSemanticSnapshot,
    RuntimeResultShape,
    StructuralArrayId,
    StructuralArrayRef,
    StructuralArraySnapshot,
    StructuralArrayState,
    StructuralRuntimeLeaf,
)
from NodeForge.semantic_lowering import lower_analyzed_expression


pytestmark = pytest.mark.unit

def _empty_callable_environment():
    """Return an empty immutable callable namespace for non-call semantic tests."""
    return CallableEnvironment(frozenset(), {}, {}, frozenset(), {})


_TEST_BINDING_LOCAL_IDS = {}


def _test_binding_id(name):
    """Return one stable test binding-slot identity across independent programs."""
    local_id = _TEST_BINDING_LOCAL_IDS.setdefault(name, len(_TEST_BINDING_LOCAL_IDS))
    return BindingId("test-owner", local_id)



def _expr(source):
    """Parse one expression fixture."""
    return ast.parse(source, mode="eval").body


def _environment(*, bindings=None, consts=None, labels=None, legacy_names=(), backend_helpers=(), builtins=None, systems=None, local_functions=None, imported_functions=None, object_registry=True, structural_arrays=None):
    """Build one immutable semantic environment for pure IR tests.

    Object-focused frontend tests opt into the same persistent registry that
    ``lower_basic_body()`` owns.  Tests for legacy expression fallback disable
    it explicitly.
    """
    from types import MappingProxyType

    semantic_constants, const_eval_values = build_semantic_constant_snapshot(CompileTimeSnapshot(consts or {}))
    runtime_bindings = {
        name: RuntimeBindingSymbol(_test_binding_id(name), typ)
        for name, typ in (bindings or {}).items()
    }
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
        frozenset(legacy_names),
        semantic_constants,
        const_eval_values,
        MappingProxyType(dict(labels or {})),
        callable_environment=CallableEnvironment(
            frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES) if builtins is None else frozenset(builtins),
            systems or {}, local_functions or {}, frozenset(backend_helpers), imported_functions or {},
        ),
        structural_arrays=structural_arrays or StructuralArraySnapshot({}, {}),
        object_semantics=object_semantics,
    )


def _lower(source, *, bindings=None, consts=None, labels=None, legacy_names=(), backend_helpers=(), builtins=None, systems=None, local_functions=None, imported_functions=None, object_registry=True):
    """Analyze and lower one source expression through the pure frontend phases."""
    expr = _expr(source)
    analysis = analyze_expression(
        expr,
        _environment(bindings=bindings, consts=consts, labels=labels, legacy_names=legacy_names, backend_helpers=backend_helpers, builtins=builtins, systems=systems, local_functions=local_functions, imported_functions=imported_functions, object_registry=object_registry),
    )
    return None if analysis is None else lower_analyzed_expression(expr, analysis)


def _operations(program, operation_type):
    """Return operations of one concrete IR record type."""
    return [operation for operation in program.operations if isinstance(operation, operation_type)]


def _producer_map(program):
    """Map program-local result IDs to the operations that produce them."""
    return {operation.result.id: operation for operation in program.operations}


def test_function_materialization_ir_is_immutable_hashable_and_blender_independent():
    """Reusable-call materialization semantics stay typed and backend-independent."""
    function_id = local_function_id("owner", "helper", "x:FLOAT")
    call_site = CallSiteId("owner", function_id, 0)
    shared = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    unique = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.UNIQUE, call_site)

    assert shared == IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    assert len({unique, IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.UNIQUE, call_site)}) == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        shared.call_site = call_site

    field_names = {field.name for field in dataclasses.fields(IRFunctionMaterialization)}
    assert field_names == {"callee", "mode", "call_site"}


def test_function_materialization_ir_enforces_shared_unique_invariants():
    """Shared calls are callsite-less and unique calls target their exact callee."""
    first = local_function_id("owner", "first", "x:FLOAT")
    second = local_function_id("owner", "second", "x:FLOAT")
    first_site = CallSiteId("owner", first, 0)

    assert IRFunctionMaterialization(first, IRFunctionMaterializationMode.SHARED).call_site is None
    assert IRFunctionMaterialization(first, IRFunctionMaterializationMode.UNIQUE, first_site).call_site == first_site
    with pytest.raises(ValueError):
        IRFunctionMaterialization(first, IRFunctionMaterializationMode.SHARED, first_site)
    with pytest.raises(ValueError):
        IRFunctionMaterialization(first, IRFunctionMaterializationMode.UNIQUE)
    with pytest.raises(ValueError):
        IRFunctionMaterialization(second, IRFunctionMaterializationMode.UNIQUE, first_site)
    with pytest.raises(TypeError):
        IRFunctionMaterialization("first", IRFunctionMaterializationMode.SHARED)


def test_semantic_modules_are_blender_independent_and_ir_is_immutable():
    import NodeForge.semantic_analysis as semantic_analysis_module
    import NodeForge.semantic_body as semantic_body_module
    import NodeForge.semantic_control_flow as semantic_control_flow_module
    import NodeForge.semantic_ir as semantic_ir_module
    import NodeForge.semantic_lowering as semantic_lowering_module

    semantic_modules = (
        semantic_analysis_module,
        semantic_body_module,
        semantic_control_flow_module,
        semantic_ir_module,
        semantic_lowering_module,
    )
    assert all("bpy" not in module.__dict__ for module in semantic_modules)
    program = _lower("a + 1", bindings={"a": TYPE_FLOAT})
    assert isinstance(program, IRProgram)
    assert dataclasses.is_dataclass(program)
    assert dataclasses.is_dataclass(program.result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        program.result.typ = TYPE_INT

    def contains_ast(value):
        if isinstance(value, ast.AST):
            return True
        if dataclasses.is_dataclass(value):
            return any(contains_ast(getattr(value, field.name)) for field in dataclasses.fields(value))
        if isinstance(value, tuple):
            return any(contains_ast(item) for item in value)
        return False

    assert not contains_ast(program)


def test_ir_value_is_program_local_shape_without_backend_state():
    value = IRValue(0, TYPE_FLOAT)
    assert value.id == 0
    assert value.typ == TYPE_FLOAT
    assert tuple(field.name for field in dataclasses.fields(value)) == ("id", "typ")
    assert not hasattr(value, "socket")

    first = _lower("1")
    second = _lower("2")
    assert first.result == IRValue(0, TYPE_INT)
    assert second.result == IRValue(0, TYPE_INT)
    assert first is not second


def test_value_ids_are_deterministic_unique_and_dependencies_are_ordered():
    program = _lower("a * 2 + 1", bindings={"a": TYPE_FLOAT})
    assert [operation.result.id for operation in program.operations] == [0, 1, 2, 3, 4]
    produced = set()
    for operation in program.operations:
        for field in dataclasses.fields(operation):
            field_value = getattr(operation, field.name)
            if isinstance(field_value, IRValue) and field.name != "result":
                assert field_value.id in produced
        produced.add(operation.result.id)
    assert program.result.id in produced


def test_arithmetic_is_explicit_ordered_value_program_with_relative_depths():
    program = _lower("a * 2 + 1", bindings={"a": TYPE_FLOAT})
    assert [type(operation) for operation in program.operations] == [
        IRBinding,
        IRLiteral,
        IRBinary,
        IRLiteral,
        IRBinary,
    ]
    assert [operation.depth for operation in program.operations] == [2, 2, 1, 1, 0]
    multiply = program.operations[2]
    add = program.operations[4]
    assert multiply.op == "MULTIPLY"
    assert multiply.left == program.operations[0].result
    assert multiply.right == program.operations[1].result
    assert add.op == "ADD"
    assert add.left == multiply.result
    assert add.right == program.operations[3].result
    assert program.result == add.result


@pytest.mark.parametrize(
    ("source", "value", "typ"),
    [("1", 1, TYPE_INT), ("1.5", 1.5, TYPE_FLOAT), ("True", True, TYPE_BOOL), ('"name"', "name", TYPE_STRING)],
)
def test_literal_typing(source, value, typ):
    program = _lower(source)
    operation = program.operations[0]
    assert isinstance(operation, IRLiteral)
    assert operation.value == value
    assert operation.result.typ == typ
    assert program.result == operation.result


def test_ir_binding_shape_uses_canonical_slot_identity_only():
    program = _lower("a + a", bindings={"a": TYPE_FLOAT})
    bindings = _operations(program, IRBinding)
    assert tuple(field.name for field in dataclasses.fields(IRBinding)) == ("result", "depth", "binding_id")
    assert len(bindings) == 2
    assert bindings[0].binding_id == bindings[1].binding_id == _test_binding_id("a")
    assert bindings[0].result != bindings[1].result
    assert not hasattr(bindings[0], "name")
    assert not hasattr(bindings[0], "socket")


def test_distinct_source_bindings_receive_distinct_canonical_slots():
    program = _lower("a + b", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT})
    bindings = _operations(program, IRBinding)
    assert bindings[0].binding_id != bindings[1].binding_id


def test_runtime_binding_precedes_compile_time_constant():
    program = _lower("a", bindings={"a": TYPE_FLOAT}, consts={"a": 1})
    assert isinstance(program.operations[0], IRBinding)
    assert program.operations[0].binding_id == _test_binding_id("a")
    assert program.result.typ == TYPE_FLOAT


def test_supported_scalar_const_and_allowed_const_lower_to_literals():
    assert isinstance(_lower("a", consts={"a": 7}).operations[0], IRLiteral)
    pi = _lower("pi")
    assert isinstance(pi.operations[0], IRLiteral)
    assert pi.result.typ == TYPE_FLOAT


def test_reached_unknown_name_is_error_and_non_scalar_constants_are_owned():
    with pytest.raises(CompileError, match="Unknown name: unknown"):
        _lower("unknown")
    vector = _lower("v", consts={"v": (1, 2, 3)})
    assert isinstance(vector.operations[0], IRVectorLiteral)
    assert vector.result.typ == TYPE_VECTOR
    with pytest.raises(CompileError, match="Unsupported compile-time value in runtime expression"):
        _lower("bad", consts={"bad": object()})


def test_type_token_and_reserved_value_diagnostics_are_preserved():
    with pytest.raises(CompileError, match="Type token Float"):
        _lower("Float")
    with pytest.raises(CompileError, match="registered as DSL builtin"):
        _lower("foo", labels={"foo": "DSL builtin"})


@pytest.mark.parametrize(
    ("source", "bindings", "typ"),
    [
        ("a + b", {"a": TYPE_FLOAT, "b": TYPE_FLOAT}, TYPE_FLOAT),
        ("a + b", {"a": TYPE_INT, "b": TYPE_INT}, TYPE_INT),
        ("a + b", {"a": TYPE_VECTOR, "b": TYPE_VECTOR}, TYPE_VECTOR),
        ("v * a", {"v": TYPE_VECTOR, "a": TYPE_FLOAT}, TYPE_VECTOR),
        ("a * v", {"v": TYPE_VECTOR, "a": TYPE_FLOAT}, TYPE_VECTOR),
        ("v / a", {"v": TYPE_VECTOR, "a": TYPE_FLOAT}, TYPE_VECTOR),
    ],
)
def test_binary_result_types_match_current_backend(source, bindings, typ):
    program = _lower(source, bindings=bindings)
    operation = _operations(program, IRBinary)[-1]
    assert operation.result.typ == typ


def test_invalid_owned_binary_operation_is_compile_error():
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and FLOAT"):
        _lower("flag + a", bindings={"flag": TYPE_BOOL, "a": TYPE_FLOAT})


@pytest.mark.parametrize("typ", [TYPE_FLOAT, TYPE_MATERIAL, TYPE_OBJECT, TYPE_GEOMETRY, TYPE_BUNDLE, TYPE_STRING])
def test_unary_plus_is_structural_identity_for_owned_runtime_values(typ):
    program = _lower("+a", bindings={"a": typ})
    assert len(program.operations) == 1
    assert isinstance(program.operations[0], IRBinding)
    assert program.result is program.operations[0].result
    assert not any(isinstance(operation, IRUnary) and operation.op == "+" for operation in program.operations)


def test_unary_minus_and_not_types():
    assert _lower("-a", bindings={"a": TYPE_FLOAT}).result.typ == TYPE_FLOAT
    assert _lower("-v", bindings={"v": TYPE_VECTOR}).result.typ == TYPE_VECTOR
    assert _lower("not flag", bindings={"flag": TYPE_BOOL}).result.typ == TYPE_BOOL
    with pytest.raises(CompileError, match="not expects Bool"):
        _lower("not a", bindings={"a": TYPE_FLOAT})


def test_boolop_emits_pairwise_operations_and_preserves_validation_order():
    program = _lower("a and b and c", bindings={"a": TYPE_BOOL, "b": TYPE_BOOL, "c": TYPE_BOOL})
    bool_ops = _operations(program, IRBoolBinary)
    assert len(bool_ops) == 2
    assert all(operation.op == "AND" for operation in bool_ops)
    assert all(operation.depth == 0 for operation in bool_ops)
    assert bool_ops[1].left == bool_ops[0].result
    assert program.result == bool_ops[1].result

    with pytest.raises(CompileError, match="Boolean operations expect Bool values"):
        _lower("True and 1 and (True + 1)")
    with pytest.raises(CompileError, match="Boolean operations expect Bool values"):
        _lower("True and 1 and legacy_call()", backend_helpers={"legacy_call"})


def test_comparison_chain_emits_explicit_compare_and_and_values():
    program = _lower("a < b <= c", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT})
    compares = _operations(program, IRCompare)
    bool_ops = _operations(program, IRBoolBinary)
    assert [operation.op for operation in compares] == ["LESS_THAN", "LESS_EQUAL"]
    assert len(bool_ops) == 1 and bool_ops[0].op == "AND"
    assert bool_ops[0].left == compares[0].result
    assert bool_ops[0].right == compares[1].result
    assert program.result == bool_ops[0].result

    assert _operations(_lower("a == b", bindings={"a": TYPE_BOOL, "b": TYPE_BOOL}), IRCompare)
    assert _operations(_lower("a == b", bindings={"a": TYPE_VECTOR, "b": TYPE_VECTOR}), IRCompare)
    with pytest.raises(CompileError, match="Comparison inputs must both be numeric"):
        _lower("a == b", bindings={"a": TYPE_BOOL, "b": TYPE_VECTOR})


def test_comparison_chain_duplicates_middle_expression_values_and_operations():
    program = _lower("a < b * 2 < c", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT})
    multiplies = [operation for operation in _operations(program, IRBinary) if operation.op == "MULTIPLY"]
    compares = _operations(program, IRCompare)
    bool_ops = _operations(program, IRBoolBinary)
    assert len(multiplies) == 2
    assert multiplies[0].result.id != multiplies[1].result.id
    assert multiplies[0].depth == multiplies[1].depth == 1
    assert len(compares) == 2 and all(operation.depth == 0 for operation in compares)
    assert len(bool_ops) == 1 and bool_ops[0].depth == 0 and bool_ops[0].op == "AND"


@pytest.mark.parametrize("typ", [TYPE_FLOAT, TYPE_INT, TYPE_VECTOR, TYPE_BOOL, TYPE_GEOMETRY, TYPE_STRING, TYPE_BUNDLE])
def test_conditional_supported_backend_types_are_owned(typ):
    program = _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": typ, "b": typ})
    conditional = program.operations[-1]
    assert isinstance(conditional, IRConditional)
    assert conditional.result.typ == typ


def test_conditional_validation_and_backend_ownership_order():
    with pytest.raises(CompileError, match="cond must be Bool"):
        _lower("a if cond else b", bindings={"cond": TYPE_FLOAT, "a": TYPE_FLOAT, "b": TYPE_FLOAT})
    with pytest.raises(CompileError, match="same type"):
        _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_FLOAT, "b": TYPE_VECTOR})
    with pytest.raises(CompileError, match=r"select\(\) result type is not supported"):
        _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_MATERIAL, "b": TYPE_MATERIAL})
    with pytest.raises(CompileError, match=r"select\(\) result type is not supported"):
        _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_OBJECT, "b": TYPE_OBJECT})
    with pytest.raises(CompileError, match="cond must be Bool"):
        _lower("a if cond else b", bindings={"cond": TYPE_FLOAT, "a": TYPE_MATERIAL, "b": TYPE_MATERIAL})


def test_vector_component_and_object_property_semantics_are_owned():
    program = _lower("v.x", bindings={"v": TYPE_VECTOR})
    component = program.operations[-1]
    assert isinstance(component, IRVectorComponent)
    assert component.value == program.operations[0].result
    assert component.result.typ == TYPE_FLOAT
    with pytest.raises(CompileError, match="only be used on Vector"):
        _lower("a.x", bindings={"a": TYPE_FLOAT})
    obj = _lower("obj.location", bindings={"obj": TYPE_OBJECT})
    prop = obj.operations[-1]
    assert isinstance(prop, IRObjectProperty)
    assert prop.property_name == "location"
    assert prop.result.typ == TYPE_VECTOR
    with pytest.raises(CompileError, match="Object values support only"):
        _lower("obj.x", bindings={"obj": TYPE_OBJECT})


def test_migration_and_permanent_diagnostic_precedence_is_preserved():
    with pytest.raises(
        CompileError,
        match=r"legacy_call\(\) is temporarily unavailable while Python extension callables are being migrated",
    ):
        _lower("legacy_call() + (True + 1)", backend_helpers={"legacy_call"})
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and INT"):
        _lower("(True + 1) + legacy_call()", backend_helpers={"legacy_call"})
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and INT"):
        _lower("(True + 1) if 1 else 2")
    with pytest.raises(
        CompileError,
        match=r"legacy_call\(\) is temporarily unavailable while Python extension callables are being migrated",
    ):
        _lower("1 < legacy_call() < (True + 1)", backend_helpers={"legacy_call"})


def test_unknown_calls_and_extension_migration_calls_have_distinct_diagnostics():
    with pytest.raises(CompileError, match="Unsupported function: foo"):
        _lower("foo()")
    with pytest.raises(
        CompileError,
        match=r"legacy_call\(\) is temporarily unavailable while Python extension callables are being migrated",
    ):
        _lower("legacy_call(a)", bindings={"a": TYPE_FLOAT}, backend_helpers={"legacy_call"})


def test_object_info_is_frontend_state_effect_without_backend_call_ir():
    expr = _expr('obj.info(transform_space="RELATIVE", as_instance=False)')
    analysis = analyze_expression(expr, _environment(bindings={"obj": TYPE_OBJECT}))
    program = lower_analyzed_expression(expr, analysis)

    assert not _operations(program, IRCall)
    assert len(program.operations) == 1
    assert isinstance(program.operations[0], IRBinding)
    assert program.result == program.operations[0].result
    state = analysis.object_semantics.states[analysis.facts[analysis.root].result_shape.object_id]
    assert state.transform_space == "RELATIVE"
    assert state.as_instance is False


def test_raw_named_outputs_remain_structural_for_one_or_many_members():
    one = _lower('node("ShaderNodeSeparateXYZ", outputs={"X": Float})')
    assert isinstance(one.result, IRNamedOutputs)
    assert tuple(name for name, _ in one.result.items) == ("X",)
    assert len(_operations(one, IRCall)) == 1

    attr = _lower('node("ShaderNodeSeparateXYZ", outputs={"X": Float}).X')
    sub = _lower('node("ShaderNodeSeparateXYZ", outputs={"X": Float})["X"]')
    assert not isinstance(attr.result, IRNamedOutputs)
    assert attr.result.typ == TYPE_FLOAT
    assert sub.result.typ == TYPE_FLOAT
    assert len(_operations(attr, IRCall)) == len(_operations(sub, IRCall)) == 1


def test_capture_attribute_call_result_is_structural_tuple_until_selected():
    tuple_program = _lower("capture_attribute(geo, value)", bindings={"geo": TYPE_GEOMETRY, "value": TYPE_VECTOR})
    assert isinstance(tuple_program.result, IRTuple)
    assert tuple(value.typ for value in tuple_program.result.items) == (TYPE_GEOMETRY, TYPE_VECTOR)
    selected = _lower("capture_attribute(geo, value)[1]", bindings={"geo": TYPE_GEOMETRY, "value": TYPE_VECTOR})
    assert selected.result.typ == TYPE_VECTOR

@pytest.mark.parametrize("name", ["builder", "raw_result", "tuple_result", "items"])
def test_legacy_binding_boundaries_remain_unsupported(name):
    source = f"{name}[0]" if name != "builder" else "builder.geometry"
    assert _lower(source, legacy_names={name}) is None


def test_ir_operations_store_no_ast_or_backend_objects():
    from NodeForge.values import Value

    program = _lower("(a + 1) if flag else v.x", bindings={"a": TYPE_FLOAT, "flag": TYPE_BOOL, "v": TYPE_VECTOR})
    forbidden = (ast.AST, Value)
    for operation in program.operations:
        for field in dataclasses.fields(operation):
            value = getattr(operation, field.name)
            assert not isinstance(value, forbidden)
            assert field.name not in {"socket", "node", "group", "compiler", "comp"}



def test_ir_emitter_consumes_analyzed_operation_facts_without_renormalizing_ast():
    from types import MappingProxyType
    from NodeForge.semantic_analysis import ExpressionAnalysis, ExpressionFact

    expr = _expr("a < b")
    analysis = analyze_expression(expr, _environment(bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT}))
    facts = dict(analysis.facts)
    facts[expr] = ExpressionFact(RuntimeResultShape(TYPE_BOOL), compare_operations=("GREATER_THAN",))
    rewritten = ExpressionAnalysis(expr, MappingProxyType(facts))
    program = lower_analyzed_expression(expr, rewritten)
    compare = _operations(program, IRCompare)[0]
    assert compare.op == "GREATER_THAN"


def test_ir_emitter_rejects_invalid_analysis_root_and_compare_fact_shape():
    from types import MappingProxyType
    from NodeForge.semantic_analysis import ExpressionAnalysis, ExpressionFact

    expr = _expr("a < b")
    other = _expr("a < b")
    analysis = analyze_expression(expr, _environment(bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT}))
    with pytest.raises(CompileError, match="invalid Semantic IR expression analysis"):
        lower_analyzed_expression(other, analysis)

    facts = dict(analysis.facts)
    facts[expr] = ExpressionFact(RuntimeResultShape(TYPE_BOOL), compare_operations=())
    malformed = ExpressionAnalysis(expr, MappingProxyType(facts))
    with pytest.raises(CompileError, match="comparison semantic operation count mismatch"):
        lower_analyzed_expression(expr, malformed)




def _literal_program(typ, value):
    """Build one single-literal program for structured control-flow invariant tests."""
    result = IRValue(0, typ)
    return IRProgram((IRLiteral(result, 0, value),), result)


def test_control_flow_ir_records_are_frozen_and_validate_local_structure_only():
    """Structured control-flow records reject malformed self-contained contracts."""
    cond = _literal_program(TYPE_BOOL, True)
    body = IRBody((IRDiscardExpression(_literal_program(TYPE_FLOAT, 1.0)),))
    merge = IRBranchMerge(BindingId("scope", 1), "x", TYPE_FLOAT)
    branch = IRIf(cond, body, body, (merge,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        branch.merges = ()
    with pytest.raises(TypeError, match="Bool"):
        IRIf(_literal_program(TYPE_FLOAT, 1.0), body, body, ())
    with pytest.raises(ValueError, match="BindingIds"):
        IRIf(cond, body, body, (merge, merge))
    with pytest.raises(ValueError, match="source names"):
        IRIf(cond, body, body, (merge, IRBranchMerge(BindingId("scope", 2), "x", TYPE_FLOAT)))
    assert not hasattr(branch, "merge_policy")


def test_repeat_ir_accepts_sole_nonpublishing_state_and_rejects_local_collisions():
    """Repeat eligibility depends on physical carried state, not exit publication policy."""
    state_id = BindingId("scope", 1)
    iteration_id = BindingId("scope", 2)
    state = IRRepeatState(state_id, "i", TYPE_INT, TYPE_FLOAT, 0, False)
    body = IRBody((IRDiscardExpression(_literal_program(TYPE_FLOAT, 1.0)),))
    repeat = IRRepeat(_literal_program(TYPE_INT, 2), iteration_id, "j", (state,), body)
    assert repeat.states == (state,)
    assert repeat.states[0].publish_to_parent is False
    with pytest.raises(TypeError, match="publish_to_parent"):
        IRRepeatState(state_id, "i", TYPE_INT, TYPE_FLOAT, 0, 0)
    with pytest.raises(TypeError, match="output type"):
        IRRepeatState(state_id, "i", TYPE_FLOAT, TYPE_INT, 0, True)
    with pytest.raises(ValueError, match="BindingId"):
        IRRepeat(_literal_program(TYPE_INT, 2), state_id, "j", (state,), body)
    with pytest.raises(TypeError, match="at least one"):
        IRRepeat(_literal_program(TYPE_INT, 2), iteration_id, "j", (), body)
    with pytest.raises(TypeError, match="Int"):
        IRRepeat(_literal_program(TYPE_FLOAT, 2.0), iteration_id, "j", (state,), body)

def _backend_context(backend, bindings=None, group=None):
    """Build an immutable explicit backend context for unit lowering tests."""
    from types import MappingProxyType

    return backend.BlenderIRLoweringContext(
        object() if group is None else group,
        MappingProxyType(dict(bindings or {})),
    )


def _backend_bindings(bindings):
    """Map source-ordered test bindings to the BindingIds emitted by _environment()."""
    return {
        _test_binding_id(name): value
        for name, value in bindings.items()
    }

def test_obsolete_structural_array_fallback_markers_are_retired():
    """Removed structural-array fallback categories do not reappear as production mechanisms."""
    root = Path(__file__).resolve().parents[2]
    obsolete = {
        "compiler.py": ("COMPILE_TIME_STATE_LEGACY_STRUCTURAL_COMPAT", "STRUCTURAL_ARRAYS_LEGACY_STRUCTURAL_COMPAT"),
        "semantic_body.py": (
            "CONTROL_FLOW_IR_BODY_REMAINING_FALLBACK",
            "CONTROL_FLOW_IR_NESTED_ATOMIC_FALLBACK",
        ),
        "consteval.py": ("STRUCTURAL_ARRAYS_GEOMETRY_BUILDER_LOOP_COMPAT",),
    }
    for relative, marker_names in obsolete.items():
        source = (root / relative).read_text(encoding="utf-8")
        for marker in marker_names:
            assert marker not in source, (relative, marker)


def test_blender_lowering_context_is_minimal_immutable_and_compiler_independent():
    from dataclasses import FrozenInstanceError, fields
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    value = Value(object(), TYPE_FLOAT)
    source_bindings = {_test_binding_id("a"): value}
    context = backend.BlenderIRLoweringContext(object(), source_bindings)
    assert tuple(field.name for field in fields(context)) == ("group", "runtime_bindings", "group_context_values")
    assert context.runtime_bindings[_test_binding_id("a")] is value
    source_bindings[_test_binding_id("a")] = Value(object(), TYPE_VECTOR)
    assert context.runtime_bindings[_test_binding_id("a")] is value
    with pytest.raises(TypeError):
        context.runtime_bindings[_test_binding_id("b")] = value
    with pytest.raises(FrozenInstanceError):
        context.group = object()

    source = (Path(__file__).resolve().parents[2] / "blender_ir_lowering.py").read_text(encoding="utf-8")
    assert "comp." not in source
    assert "from .compiler import" not in source
    assert "import compiler" not in source
    assert "expression_compiler" not in source
    assert "semantic_analysis" not in source
    assert "import ast" not in source


def test_backend_materialization_state_is_per_lowering_call():
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    first = Value(object(), TYPE_FLOAT)
    second = Value(object(), TYPE_FLOAT)
    program = _lower("a", bindings={"a": TYPE_FLOAT})
    assert backend.lower_expression(_backend_context(backend, {_test_binding_id("a"): first}), program) is first
    assert backend.lower_expression(_backend_context(backend, {_test_binding_id("a"): second}), program) is second


def test_backend_binding_bridge_and_invariant_drift():
    from NodeForge.blender_ir_lowering import lower_expression
    from NodeForge.values import Value

    socket = object()
    value = Value(socket, TYPE_FLOAT)
    from NodeForge import blender_ir_lowering as backend

    context = _backend_context(backend, {_test_binding_id("a"): value}, group=None)
    program = _lower("a", bindings={"a": TYPE_FLOAT})
    assert lower_expression(context, program) is value

    drifted = _backend_context(backend, {_test_binding_id("a"): Value(socket, TYPE_VECTOR)}, group=None)
    with pytest.raises(CompileError, match="changed type"):
        lower_expression(drifted, program)
    missing = _backend_context(backend, {}, group=None)
    with pytest.raises(CompileError, match="no longer a runtime Value"):
        lower_expression(missing, program)


def test_backend_executes_program_order_and_applies_nonzero_base_depth(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    calls = []
    context = _backend_context(backend, _backend_bindings({"a": Value(object(), TYPE_FLOAT)}))

    def fake_value(group, value, x=0, y=0):
        calls.append(("value", value, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_int_value(group, value, x=0, y=0):
        calls.append(("int_value", value, x, y))
        return Value(object(), TYPE_INT)

    def fake_math(group, operation, args, x=0, y=0):
        calls.append(("math", operation, x, y))
        return Value(object(), TYPE_FLOAT)

    monkeypatch.setattr(backend, "_value", fake_value)
    monkeypatch.setattr(backend, "_int_value", fake_int_value)
    monkeypatch.setattr(backend, "_math", fake_math)

    result = backend.lower_expression(context, _lower("a * 2", bindings={"a": TYPE_FLOAT}), base_depth=3)
    assert result.typ == TYPE_FLOAT
    assert calls == [("int_value", 2, 960, -360), ("math", "MULTIPLY", 720, -270)]


def test_backend_missing_operand_is_controlled_internal_error():
    from NodeForge import blender_ir_lowering as backend

    missing = IRValue(99, TYPE_FLOAT)
    result = IRValue(0, TYPE_FLOAT)
    program = IRProgram((IRUnary(result, 0, "+", missing),), result)
    context = _backend_context(backend)
    with pytest.raises(CompileError, match="used before materialization"):
        backend.lower_expression(context, program)


def test_backend_unary_identity_and_current_node_policy_without_blender(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    calls = []
    bindings = {
        "a": Value(object(), TYPE_FLOAT),
        "v": Value(object(), TYPE_VECTOR),
        "flag": Value(object(), TYPE_BOOL),
    }
    context = _backend_context(backend, _backend_bindings(bindings))

    def fake_value(group, value, x=0, y=0):
        calls.append(("value", value, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_math(group, operation, args, x=0, y=0):
        calls.append(("math", operation, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_vector_math(group, operation, args, out_type=TYPE_VECTOR, x=0, y=0):
        calls.append(("vector_math", operation, x, y))
        return Value(object(), out_type)

    def fake_boolean_math(group, operation, args, x=0, y=0):
        calls.append(("boolean_math", operation, x, y))
        return Value(object(), TYPE_BOOL)

    monkeypatch.setattr(backend, "_value", fake_value)
    monkeypatch.setattr(backend, "_math", fake_math)
    monkeypatch.setattr(backend, "_vector_math", fake_vector_math)
    monkeypatch.setattr(backend, "_boolean_math", fake_boolean_math)

    plus = backend.lower_expression(context, _lower("+a", bindings={"a": TYPE_FLOAT}), base_depth=1)
    assert plus is bindings["a"]
    assert calls == []

    backend.lower_expression(context, _lower("-a", bindings={"a": TYPE_FLOAT}), base_depth=1)
    backend.lower_expression(context, _lower("-v", bindings={"v": TYPE_VECTOR}), base_depth=1)
    backend.lower_expression(context, _lower("not flag", bindings={"flag": TYPE_BOOL}), base_depth=1)
    assert ("value", 0.0, 240, -130) in calls
    assert ("math", "SUBTRACT", 240, -90) in calls
    assert ("value", -1.0, 240, -130) in calls
    assert ("vector_math", "SCALE", 240, -90) in calls
    assert ("boolean_math", "NOT", 240, -90) in calls


def test_backend_comparison_chain_executes_explicit_duplicate_operations(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    calls = []
    context = _backend_context(backend, _backend_bindings({
        "a": Value(object(), TYPE_FLOAT),
        "b": Value(object(), TYPE_FLOAT),
        "c": Value(object(), TYPE_FLOAT),
    }))

    def fake_value(group, value, x=0, y=0):
        calls.append(("value", value, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_int_value(group, value, x=0, y=0):
        calls.append(("int_value", value, x, y))
        return Value(object(), TYPE_INT)

    def fake_math(group, operation, args, x=0, y=0):
        calls.append(("math", operation, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_compare(group, operation, left, right, x=0, y=0):
        calls.append(("compare", operation, x, y))
        return Value(object(), TYPE_BOOL)

    def fake_boolean_math(group, operation, args, x=0, y=0):
        calls.append(("boolean_math", operation, x, y))
        return Value(object(), TYPE_BOOL)

    monkeypatch.setattr(backend, "_value", fake_value)
    monkeypatch.setattr(backend, "_int_value", fake_int_value)
    monkeypatch.setattr(backend, "_math", fake_math)
    monkeypatch.setattr(backend, "_compare", fake_compare)
    monkeypatch.setattr(backend, "_boolean_math", fake_boolean_math)

    program = _lower("a < b * 2 < c", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT})
    result = backend.lower_expression(context, program, base_depth=1)
    assert result.typ == TYPE_BOOL
    assert calls.count(("math", "MULTIPLY", 480, -180)) == 2
    assert calls.count(("compare", "LESS_THAN", 240, -90)) == 2
    assert calls.count(("boolean_math", "AND", 240, -90)) == 1


def test_backend_result_type_drift_is_controlled_internal_error(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    context = _backend_context(backend, _backend_bindings({"a": Value(object(), TYPE_FLOAT)}))

    def fake_value(group, value, x=0, y=0):
        return Value(object(), TYPE_FLOAT)

    def fake_int_value(group, value, x=0, y=0):
        return Value(object(), TYPE_INT)

    def fake_math(group, operation, args, x=0, y=0):
        return Value(object(), TYPE_VECTOR)

    monkeypatch.setattr(backend, "_value", fake_value)
    monkeypatch.setattr(backend, "_int_value", fake_int_value)
    monkeypatch.setattr(backend, "_math", fake_math)

    with pytest.raises(CompileError, match="expected type FLOAT, got VECTOR"):
        backend.lower_expression(context, _lower("a + 1", bindings={"a": TYPE_FLOAT}))


def test_backend_vector_conditional_component_and_boolean_primitives_without_blender(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    calls = []
    context = _backend_context(backend, _backend_bindings({
        "v": Value(object(), TYPE_VECTOR),
        "a": Value(object(), TYPE_FLOAT),
        "flag": Value(object(), TYPE_BOOL),
        "other": Value(object(), TYPE_FLOAT),
    }))

    def fake_value(group, value, x=0, y=0):
        calls.append(("value", value, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_math(group, operation, args, x=0, y=0):
        calls.append(("math", operation, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_vector_math(group, operation, args, out_type=TYPE_VECTOR, x=0, y=0):
        calls.append(("vector_math", operation, x, y))
        return Value(object(), out_type)

    def fake_boolean_math(group, operation, args, x=0, y=0):
        calls.append(("boolean_math", operation, x, y))
        return Value(object(), TYPE_BOOL)

    def fake_compare(group, operation, left, right, x=0, y=0):
        calls.append(("compare", operation, x, y))
        return Value(object(), TYPE_BOOL)

    def fake_switch(group, condition, false_value, true_value, x=0, y=0):
        calls.append(("switch", x, y))
        return Value(object(), true_value.typ)

    def fake_separate_xyz(group, value, component, x=0, y=0):
        calls.append(("component", component, x, y))
        return Value(object(), TYPE_FLOAT)

    monkeypatch.setattr(backend, "_value", fake_value)
    monkeypatch.setattr(backend, "_math", fake_math)
    monkeypatch.setattr(backend, "_vector_math", fake_vector_math)
    monkeypatch.setattr(backend, "_boolean_math", fake_boolean_math)
    monkeypatch.setattr(backend, "_compare", fake_compare)
    monkeypatch.setattr(backend, "_switch", fake_switch)
    monkeypatch.setattr(backend, "_separate_xyz", fake_separate_xyz)

    assert backend.lower_expression(context, _lower("v / a", bindings={"v": TYPE_VECTOR, "a": TYPE_FLOAT})).typ == TYPE_VECTOR
    assert ("math", "DIVIDE", 0, 0) in calls
    assert ("vector_math", "SCALE", 0, 0) in calls

    calls.clear()
    conditional = _lower(
        "a if flag else other",
        bindings={"a": TYPE_FLOAT, "flag": TYPE_BOOL, "other": TYPE_FLOAT},
    )
    assert backend.lower_expression(context, conditional).typ == TYPE_FLOAT
    assert calls == [("switch", 0, 0)]

    calls.clear()
    assert backend.lower_expression(context, _lower("v.z", bindings={"v": TYPE_VECTOR})).typ == TYPE_FLOAT
    assert calls == [("component", "z", 0, 0)]

    calls.clear()
    bool_program = _lower("flag and True", bindings={"flag": TYPE_BOOL})
    assert backend.lower_expression(context, bool_program).typ == TYPE_BOOL
    assert calls[-1] == ("boolean_math", "AND", 0, 0)


def test_resolved_environment_does_not_enter_semantic_ir_materialization_records():
    """Session selection stays upstream of reusable-call Semantic IR values."""
    assert "resolved_environment" not in {
        field.name for field in dataclasses.fields(IRFunctionMaterialization)
    }


def test_complete_expression_result_shapes_and_arrays_are_immutable():
    runtime = RuntimeResultShape(TYPE_FLOAT)
    array = ArrayResultShape((runtime, ArrayResultShape(())))
    with pytest.raises(dataclasses.FrozenInstanceError):
        runtime.typ = TYPE_VECTOR
    with pytest.raises(dataclasses.FrozenInstanceError):
        array.items = ()
    program = _lower("[a, b]", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT})
    assert isinstance(program.result, IRArray)
    assert [type(op) for op in program.operations] == [IRBinding, IRBinding]
    assert [op.depth for op in program.operations] == [1, 1]
    tuple_program = _lower("(a, b)", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT})
    assert isinstance(tuple_program.result, IRArray)
    assert tuple_program.result == program.result


def test_array_subscript_is_structural_and_eager_with_negative_indexing():
    program = _lower("[[a, b], c][0][1]", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT})
    assert [type(op) for op in program.operations] == [IRBinding, IRBinding, IRBinding]
    assert program.result == program.operations[1].result
    negative = _lower("(a, b)[-1]", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT})
    assert negative.result == negative.operations[1].result
    with pytest.raises(CompileError, match="array index out of range"):
        _lower("[a, b][3]", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT})


def test_unary_plus_is_structural_identity_for_arrays_and_named_constant_arrays():
    program = _lower("(+[a, b])[0]", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT})
    assert program.result == program.operations[0].result
    assert len(program.operations) == 2
    assert not _operations(program, IRUnary)
    tuple_program = _lower("(+(a, b))[-1]", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT})
    assert tuple_program.result == tuple_program.operations[1].result
    const_program = _lower("+items", consts={"items": [1, [2, 3]]})
    assert isinstance(const_program.result, IRArray)
    assert all(op.depth == 1 for op in const_program.operations)


def test_semantic_constant_normalization_preserves_vector_coercion_and_nested_arrays():
    constants, detached = build_semantic_constant_snapshot(CompileTimeSnapshot({
        "flag": True,
        "number": 2,
        "text": "x",
        "vec": (1, 2, 3),
        "items": [1, [2, 3]],
    }))
    assert constants["flag"] == SemanticConstant("scalar", TYPE_BOOL, True)
    assert constants["number"] == SemanticConstant("scalar", TYPE_INT, 2)
    assert constants["text"] == SemanticConstant("scalar", TYPE_STRING, "x")
    assert constants["vec"].kind == "vector" and constants["vec"].value == (1.0, 2.0, 3.0)
    assert constants["items"].kind == "array"
    assert isinstance(detached["items"], list)
    assert isinstance(detached["items"][1], list)


def test_detached_const_eval_snapshot_preserves_list_tuple_semantics_and_ownership():
    original = {"xs": [1, 2], "nested": [[1], (2, [3])]}
    _, detached = build_semantic_constant_snapshot(CompileTimeSnapshot(original))
    assert isinstance(detached["xs"], list)
    assert isinstance(detached["nested"], list)
    assert isinstance(detached["nested"][1], tuple)
    assert isinstance(detached["nested"][1][1], list)
    original["nested"][0].append(9)
    assert detached["nested"][0] == [1]
    with pytest.raises(CompileError, match="array/vector indexing currently requires a compile-time integer index"):
        _lower("[a, b][xs == [1, 2]]", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT}, consts={"xs": [1, 2]})
    assert _lower("[a, b, c][len(xs + [3]) - 1]", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT}, consts={"xs": [1, 2]}).result.id == 2
    assert _lower("[a, b, c][len(xs + range(1)) - 1]", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT}, consts={"xs": [1, 2]}).result.id == 2

    cyclic = [1]
    cyclic.append(cyclic)
    from NodeForge.consteval import _const_eval

    _, cyclic_detached = build_semantic_constant_snapshot(CompileTimeSnapshot({"xs": cyclic}))
    assert _const_eval(_expr("xs[1] == xs"), cyclic_detached) is True
    with pytest.raises(CompileError, match="array/vector indexing currently requires a compile-time integer index"):
        _lower(
            "[a, b][xs[1] == xs]",
            bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT},
            consts={"xs": cyclic},
        )


def test_vector_subscript_normalizes_to_existing_component_ir():
    for source, component in [("v[0]", "x"), ("v[1]", "y"), ("v[2]", "z"), ("v[i]", "y")]:
        program = _lower(source, bindings={"v": TYPE_VECTOR}, consts={"i": 1})
        op = program.operations[-1]
        assert isinstance(op, IRVectorComponent)
        assert op.component == component
    with pytest.raises(CompileError, match="vector index must be 0, 1 or 2"):
        _lower("v[3]", bindings={"v": TYPE_VECTOR})


def test_object_properties_are_semantically_typed_and_calls_remain_fallback():
    expected = {
        "geometry": TYPE_GEOMETRY,
        "location": TYPE_VECTOR,
        "rotation": TYPE_VECTOR,
        "scale": TYPE_VECTOR,
    }
    for name, typ in expected.items():
        program = _lower(f"obj.{name}", bindings={"obj": TYPE_OBJECT})
        op = program.operations[-1]
        assert isinstance(op, IRObjectProperty)
        assert op.property_name == name
        assert op.result.typ == typ
    for source in ("obj.x", "obj.foo"):
        with pytest.raises(CompileError, match="Object values support only"):
            _lower(source, bindings={"obj": TYPE_OBJECT})
    info = _lower("obj.info()", bindings={"obj": TYPE_OBJECT})
    assert not _operations(info, IRCall)
    assert isinstance(info.operations[-1], IRBinding)


def test_ir_records_never_carry_mutable_lists_or_ast_backend_objects_after_completion():
    from NodeForge.values import Value

    program = _lower("[v[0], obj.location]", bindings={"v": TYPE_VECTOR, "obj": TYPE_OBJECT})

    def visit(value):
        assert not isinstance(value, (ast.AST, Value, list))
        if dataclasses.is_dataclass(value):
            for field in dataclasses.fields(value):
                visit(getattr(value, field.name))
        elif isinstance(value, tuple):
            for item in value:
                visit(item)

    visit(program)



def _raw_call(*, mode, results, outputs=None, output=None, typ=None, options=()):
    """Construct one hand-authored raw-node IR call for invariant tests."""
    base = (
        ("bl_idname", "ShaderNodeSeparateXYZ"),
        ("props", ()),
        ("inputs", ()),
        ("raw_output_mode", mode.value),
        ("output", output),
        ("typ", typ),
        ("outputs", outputs),
    )
    return IRCall(
        tuple(results),
        0,
        IRCallableTarget(IRCallableKind.BUILTIN, "node"),
        (),
        base + tuple(options),
        mode,
    )


def test_call_ir_rejects_noncanonical_types_ast_backend_and_mutable_payloads():
    """Call IR admits only NFType values and detached immutable option data."""
    from NodeForge.values import Value

    with pytest.raises(TypeError, match="typ must be an NFType"):
        IRValue(0, "FLOAT")
    with pytest.raises(TypeError, match="IRCallArgument.value"):
        IRCallArgument("x", _expr("x"))
    target = IRCallableTarget(IRCallableKind.BUILTIN, "position")
    result = IRValue(0, TYPE_VECTOR)
    for payload in (_expr("x"), Value(object(), TYPE_FLOAT), [], {}):
        with pytest.raises(TypeError, match="detached immutable IR data"):
            IRCall((result,), 0, target, (), (("payload", payload),))


def test_call_ir_rejects_invalid_structural_members_and_targets():
    """Structural call results and callable targets remain explicit compiler-owned records."""
    with pytest.raises(TypeError, match="IRTuple"):
        IRTuple((object(),))
    with pytest.raises(TypeError, match="members must be IRValue"):
        IRNamedOutputs((("X", object()),))
    with pytest.raises(ValueError, match="unique names"):
        IRNamedOutputs((("X", IRValue(0, TYPE_FLOAT)), ("X", IRValue(1, TYPE_FLOAT))))
    with pytest.raises(TypeError, match="kind must be an IRCallableKind"):
        IRCallableTarget("BUILTIN", "position")
    with pytest.raises(TypeError, match="IRCallableTarget"):
        IRCall((IRValue(0, TYPE_FLOAT),), 0, object(), ())


def test_raw_call_ir_enforces_syntax_mode_and_declared_result_cardinality():
    """Raw-node output protocol is source-mode driven even for one named output."""
    scalar = _raw_call(
        mode=IRRawNodeOutputMode.SINGLE_OUTPUT,
        results=(IRValue(0, TYPE_FLOAT),),
        output="Value",
        typ=TYPE_FLOAT,
    )
    assert scalar.raw_output_mode is IRRawNodeOutputMode.SINGLE_OUTPUT

    named_one = _raw_call(
        mode=IRRawNodeOutputMode.NAMED_OUTPUTS,
        results=(IRValue(0, TYPE_FLOAT),),
        outputs=(("X", TYPE_FLOAT),),
    )
    structural = IRNamedOutputs((("X", named_one.results[0]),))
    assert isinstance(structural, IRNamedOutputs)
    assert structural.get("X") is named_one.results[0]

    with pytest.raises(ValueError, match="result count"):
        _raw_call(
            mode=IRRawNodeOutputMode.NAMED_OUTPUTS,
            results=(IRValue(0, TYPE_FLOAT),),
            outputs=(("X", TYPE_FLOAT), ("Y", TYPE_FLOAT)),
        )
    with pytest.raises(ValueError, match="unique output names"):
        _raw_call(
            mode=IRRawNodeOutputMode.NAMED_OUTPUTS,
            results=(IRValue(0, TYPE_FLOAT), IRValue(1, TYPE_FLOAT)),
            outputs=(("X", TYPE_FLOAT), ("X", TYPE_FLOAT)),
        )
    with pytest.raises(ValueError, match="exactly one result"):
        _raw_call(
            mode=IRRawNodeOutputMode.SINGLE_OUTPUT,
            results=(IRValue(0, TYPE_FLOAT), IRValue(1, TYPE_FLOAT)),
            output="Value",
            typ=TYPE_FLOAT,
        )
    with pytest.raises(ValueError, match="requires named output metadata"):
        _raw_call(
            mode=IRRawNodeOutputMode.NAMED_OUTPUTS,
            results=(IRValue(0, TYPE_FLOAT),),
            outputs=None,
        )


def test_call_ir_all_analyzed_runtime_operand_and_result_types_are_nftype():
    """A representative migrated call preserves NFType identity through analysis and IR."""
    program = _lower("length(v) + 1.0", bindings={"v": TYPE_VECTOR})
    calls = _operations(program, IRCall)
    assert len(calls) == 1
    call = calls[0]
    assert all(isinstance(argument.value.typ, type(TYPE_FLOAT)) for argument in call.arguments)
    assert all(isinstance(result.typ, type(TYPE_FLOAT)) for result in call.results)


def test_raw_named_output_selection_preserves_attribute_and_string_subscript_diagnostics():
    """Structural raw outputs accept only declared attributes or compile-time non-empty string keys."""
    with pytest.raises(CompileError, match="Unknown raw node output 'Y'"):
        _lower('node("ShaderNodeSeparateXYZ", outputs={"X": Float}).Y')
    with pytest.raises(CompileError, match="Unknown raw node output 'Y'"):
        _lower('node("ShaderNodeSeparateXYZ", outputs={"X": Float})["Y"]')
    with pytest.raises(CompileError, match="compile-time string key"):
        _lower(
            'node("ShaderNodeSeparateXYZ", outputs={"X": Float})[key]',
            bindings={"key": TYPE_STRING},
        )
    with pytest.raises(CompileError, match="non-empty string key"):
        _lower('node("ShaderNodeSeparateXYZ", outputs={"X": Float})[1]')
    with pytest.raises(CompileError, match="non-empty string key"):
        _lower('node("ShaderNodeSeparateXYZ", outputs={"X": Float})[""]')


def test_mixed_core_calls_stay_on_ir_path_and_pending_callable_categories_fail_directly():
    """Semantic Call IR owns core calls while pending callable categories fail before legacy execution."""
    from types import SimpleNamespace

    core_cases = [
        ("length(v) + 1.0", {"v": TYPE_VECTOR}),
        ("cube(scale * 2.0)", {"scale": TYPE_FLOAT}),
        ("condition and (length(v) > 0.0)", {"condition": TYPE_BOOL, "v": TYPE_VECTOR}),
        ('node("ShaderNodeValue", output="Value", typ=Float) * gain', {"gain": TYPE_FLOAT}),
        ('node("ShaderNodeSeparateXYZ", outputs={"X": Float}).X', {}),
        ('node("ShaderNodeSeparateXYZ", outputs={"X": Float})["X"]', {}),
        ("capture_attribute(geo, value)[1]", {"geo": TYPE_GEOMETRY, "value": TYPE_FLOAT}),
        ("obj.info().location", {"obj": TYPE_OBJECT}),
    ]
    for source, bindings in core_cases:
        program = _lower(source, bindings=bindings)
        assert program is not None, source
        if source == "obj.info().location":
            assert isinstance(program.operations[-1], IRObjectProperty)
            assert not _operations(program, IRCall)
        else:
            assert _operations(program, IRCall), source

    record = SimpleNamespace(package_id="vendor.pkg", namespace="functions", name="imported_fn")
    binding = SimpleNamespace(namespace="functions", canonical_name="imported_fn", record=record)
    cases = [
        (
            "local_fn(x) + 1.0",
            {"bindings": {"x": TYPE_FLOAT}, "local_functions": {"local_fn": object()}},
            r"local_fn\(\) is temporarily unavailable while source-backed callable contracts are being migrated",
        ),
        (
            "imported_fn(x) + 1.0",
            {"bindings": {"x": TYPE_FLOAT}, "imported_functions": {"imported_fn": binding}},
            r"imported_fn\(\) is temporarily unavailable while imported callable contracts are being migrated",
        ),
        (
            "system_constructor() + 1.0",
            {"systems": {"system_constructor": object()}},
            r"system_constructor\(\) is temporarily unavailable while Python extension callables are being migrated",
        ),
        (
            "backend_helper(x) + 1.0",
            {"bindings": {"x": TYPE_FLOAT}, "backend_helpers": {"backend_helper"}},
            r"backend_helper\(\) is temporarily unavailable while Python extension callables are being migrated",
        ),
    ]
    for source, kwargs, diagnostic in cases:
        with pytest.raises(CompileError, match=diagnostic):
            _lower(source, **kwargs)


def test_grid_is_semantic_ir_capable_and_grid_uv_requires_available_context():
    """Grid writes explicit hidden UV context through the permanent semantic path."""
    grid = _lower("grid(4, 3)")
    assert grid is not None
    calls = _operations(grid, IRCall)
    assert len(calls) == 1
    assert tuple(value.typ for value in calls[0].results) == (TYPE_GEOMETRY, TYPE_VECTOR)
    writes = _operations(grid, IRContextWrite)
    assert len(writes) == 1 and writes[0].slot is GroupContextSlot.GRID_UV
    assert grid.result.typ is TYPE_GEOMETRY

    with pytest.raises(CompileError, match=r"grid_uv\(\) requires a preceding grid\(width, height\) call"):
        _lower("grid_uv()")


def test_stored_structural_array_lowers_recursively_to_irbinding_leaves():
    """Semantic lowering reconstructs stored arrays from BindingIds without backend values."""
    inner = StructuralArrayId(0)
    outer = StructuralArrayId(1)
    x_id = _test_binding_id("stored_x")
    y_id = _test_binding_id("stored_y")
    snapshot = StructuralArraySnapshot(
        {"items": outer},
        {
            inner: StructuralArrayState((StructuralRuntimeLeaf(y_id, TYPE_VECTOR),)),
            outer: StructuralArrayState((
                StructuralRuntimeLeaf(x_id, TYPE_FLOAT),
                StructuralArrayRef(inner),
            )),
        },
    )
    expr = _expr("items")
    environment = SemanticEnvironment(
        MappingProxyType({
            "stored_x": RuntimeBindingSymbol(x_id, TYPE_FLOAT),
            "stored_y": RuntimeBindingSymbol(y_id, TYPE_VECTOR),
        }),
        frozenset(),
        MappingProxyType({}),
        MappingProxyType({}),
        MappingProxyType({}),
        callable_environment=_empty_callable_environment(),
        structural_arrays=snapshot,
        object_semantics=ObjectSemanticSnapshot({}, {}, 0),
    )
    analysis = analyze_expression(expr, environment)
    program = lower_analyzed_expression(expr, analysis)
    assert isinstance(program.result, IRArray)
    assert isinstance(program.result.items[0], IRValue)
    assert isinstance(program.result.items[1], IRArray)
    bindings = [operation for operation in program.operations if isinstance(operation, IRBinding)]
    assert [operation.binding_id for operation in bindings] == [x_id, y_id]


def test_group_context_ir_and_call_contract_invariants():
    """Context slots and projected calls reject type/index drift before backend lowering."""
    assert GROUP_CONTEXT_SPECS[GroupContextSlot.CURRENT_GEOMETRY].typ is TYPE_GEOMETRY
    assert GROUP_CONTEXT_SPECS[GroupContextSlot.GRID_UV].typ is TYPE_VECTOR

    geometry = IRValue(0, TYPE_GEOMETRY)
    vector = IRValue(1, TYPE_VECTOR)
    assert IRContextRead(geometry, 0, GroupContextSlot.CURRENT_GEOMETRY).result is geometry
    assert IRContextWrite(0, GroupContextSlot.GRID_UV, vector).value is vector
    with pytest.raises(TypeError):
        IRContextRead(vector, 0, GroupContextSlot.CURRENT_GEOMETRY)
    with pytest.raises(TypeError):
        IRContextWrite(0, GroupContextSlot.CURRENT_GEOMETRY, vector)
    with pytest.raises(ValueError):
        IRContextRead(geometry, -1, GroupContextSlot.CURRENT_GEOMETRY)
    with pytest.raises(ValueError):
        IRContextWrite(-1, GroupContextSlot.GRID_UV, vector)

    projected = ProjectedCallResult(
        (TYPE_GEOMETRY, TYPE_VECTOR), 0, ((GroupContextSlot.GRID_UV, 1),)
    )
    assert projected.exposed_index == 0
    with pytest.raises(ValueError):
        ProjectedCallResult((TYPE_GEOMETRY,), 1)
    with pytest.raises(ValueError):
        ProjectedCallResult((TYPE_GEOMETRY,), 0, ((GroupContextSlot.CURRENT_GEOMETRY, 1),))
    with pytest.raises(TypeError):
        ProjectedCallResult((TYPE_GEOMETRY,), 0, ((GroupContextSlot.GRID_UV, 0),))
    assert ContextReadCallResult(GroupContextSlot.GRID_UV, TYPE_VECTOR).typ is TYPE_VECTOR
    with pytest.raises(TypeError):
        ContextReadCallResult(GroupContextSlot.GRID_UV, TYPE_FLOAT)
