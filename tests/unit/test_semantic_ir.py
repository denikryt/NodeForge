"""Pure value-based Semantic IR contracts and migration-boundary regressions."""

import ast
import dataclasses
import sys
from pathlib import Path

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
from NodeForge.semantic_ir import (
    IRBinary,
    IRBinding,
    IRBoolBinary,
    IRCompare,
    IRConditional,
    IRLiteral,
    IRProgram,
    IRUnary,
    IRValue,
    IRVectorComponent,
)
from NodeForge.semantic_analysis import SemanticEnvironment, analyze_expression
from NodeForge.semantic_lowering import lower_analyzed_expression


pytestmark = pytest.mark.unit


def _expr(source):
    """Parse one expression fixture."""
    return ast.parse(source, mode="eval").body


def _environment(*, bindings=None, consts=None, labels=None, legacy_names=()):
    """Build one immutable semantic environment for pure IR tests."""
    from types import MappingProxyType

    scalar_constants = {}
    unsupported_constants = set()
    for name, value in (consts or {}).items():
        if isinstance(value, bool):
            scalar_constants[name] = (TYPE_BOOL, value)
        elif isinstance(value, (int, float)):
            scalar_constants[name] = (TYPE_FLOAT, value)
        elif isinstance(value, str):
            scalar_constants[name] = (TYPE_STRING, value)
        else:
            unsupported_constants.add(name)
    return SemanticEnvironment(
        MappingProxyType(dict(bindings or {})),
        frozenset(legacy_names),
        MappingProxyType(scalar_constants),
        frozenset(unsupported_constants),
        MappingProxyType(dict(labels or {})),
    )


def _lower(source, *, bindings=None, consts=None, labels=None, legacy_names=()):
    """Analyze and lower one source expression through the pure frontend stages."""
    expr = _expr(source)
    analysis = analyze_expression(
        expr,
        _environment(bindings=bindings, consts=consts, labels=labels, legacy_names=legacy_names),
    )
    return None if analysis is None else lower_analyzed_expression(expr, analysis)


def _operations(program, operation_type):
    """Return operations of one concrete IR record type."""
    return [operation for operation in program.operations if isinstance(operation, operation_type)]


def _producer_map(program):
    """Map program-local result IDs to the operations that produce them."""
    return {operation.result.id: operation for operation in program.operations}


def test_semantic_modules_are_blender_independent_and_ir_is_immutable():
    assert "bpy" not in sys.modules
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
    assert first.result == IRValue(0, TYPE_FLOAT)
    assert second.result == IRValue(0, TYPE_FLOAT)
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
    [("1", 1, TYPE_FLOAT), ("1.5", 1.5, TYPE_FLOAT), ("True", True, TYPE_BOOL), ('"name"', "name", TYPE_STRING)],
)
def test_literal_typing(source, value, typ):
    program = _lower(source)
    operation = program.operations[0]
    assert isinstance(operation, IRLiteral)
    assert operation.value == value
    assert operation.result.typ == typ
    assert program.result == operation.result


def test_runtime_binding_precedes_compile_time_constant():
    program = _lower("a", bindings={"a": TYPE_FLOAT}, consts={"a": 1})
    assert isinstance(program.operations[0], IRBinding)
    assert program.operations[0].name == "a"
    assert program.result.typ == TYPE_FLOAT


def test_supported_scalar_const_and_allowed_const_lower_to_literals():
    assert isinstance(_lower("a", consts={"a": 7}).operations[0], IRLiteral)
    pi = _lower("pi")
    assert isinstance(pi.operations[0], IRLiteral)
    assert pi.result.typ == TYPE_FLOAT


def test_reached_unknown_name_is_error_and_unrepresented_constant_is_unsupported():
    with pytest.raises(CompileError, match="Unknown name: unknown"):
        _lower("unknown")
    assert _lower("v", consts={"v": (1, 2, 3)}) is None


def test_type_token_and_reserved_value_diagnostics_are_preserved():
    with pytest.raises(CompileError, match="Type token Float"):
        _lower("Float")
    with pytest.raises(CompileError, match="registered as DSL builtin"):
        _lower("foo", labels={"foo": "DSL builtin"})


@pytest.mark.parametrize(
    ("source", "bindings", "typ"),
    [
        ("a + b", {"a": TYPE_FLOAT, "b": TYPE_FLOAT}, TYPE_FLOAT),
        ("a + b", {"a": TYPE_INT, "b": TYPE_INT}, TYPE_FLOAT),
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
def test_unary_plus_is_explicit_identity_operation_for_any_owned_runtime_value(typ):
    program = _lower("+a", bindings={"a": typ})
    unary = program.operations[-1]
    assert isinstance(unary, IRUnary)
    assert unary.op == "+"
    assert unary.result.typ == typ
    assert unary.operand == program.operations[0].result


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
        _lower("True and 1 and legacy_call()")


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
    assert _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_MATERIAL, "b": TYPE_MATERIAL}) is None
    assert _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_OBJECT, "b": TYPE_OBJECT}) is None
    with pytest.raises(CompileError, match="cond must be Bool"):
        _lower("a if cond else b", bindings={"cond": TYPE_FLOAT, "a": TYPE_MATERIAL, "b": TYPE_MATERIAL})


def test_vector_component_consumes_vector_value_and_object_attribute_falls_back():
    program = _lower("v.x", bindings={"v": TYPE_VECTOR})
    component = program.operations[-1]
    assert isinstance(component, IRVectorComponent)
    assert component.value == program.operations[0].result
    assert component.value.typ == TYPE_VECTOR
    assert component.result.typ == TYPE_FLOAT
    with pytest.raises(CompileError, match="only be used on Vector"):
        _lower("a.x", bindings={"a": TYPE_FLOAT})
    assert _lower("obj.location", bindings={"obj": TYPE_OBJECT}) is None
    assert _lower("obj.x", bindings={"obj": TYPE_OBJECT}) is None


def test_mixed_fallback_and_diagnostic_boundaries_are_preserved():
    assert _lower("legacy_call() + (True + 1)") is None
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and FLOAT"):
        _lower("(True + 1) + legacy_call()")
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and FLOAT"):
        _lower("(True + 1) if 1 else 2")
    assert _lower("1 < legacy_call() < (True + 1)") is None


@pytest.mark.parametrize("source", ["foo(a)", "obj.info()", "[a, b]", "(a, b)", "a[0]"])
def test_explicit_first_slice_boundaries_are_unsupported(source):
    assert _lower(source, bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "obj": TYPE_OBJECT}) is None


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
    facts[expr] = ExpressionFact(TYPE_BOOL, compare_operations=("GREATER_THAN",))
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
    facts[expr] = ExpressionFact(TYPE_BOOL, compare_operations=())
    malformed = ExpressionAnalysis(expr, MappingProxyType(facts))
    with pytest.raises(CompileError, match="comparison semantic operation count mismatch"):
        lower_analyzed_expression(expr, malformed)


def test_exact_semantic_migration_markers_are_present_at_source_decisions():
    root = Path(__file__).resolve().parents[2]
    analysis = (root / "semantic_analysis.py").read_text(encoding="utf-8")
    semantic = (root / "semantic_lowering.py").read_text(encoding="utf-8")
    dispatcher = (root / "expression_compiler.py").read_text(encoding="utf-8")
    backend = (root / "blender_ir_lowering.py").read_text(encoding="utf-8")

    assert analysis.count("SEMANTIC_ANALYSIS_MIGRATION") == 1
    assert semantic.count("SEMANTIC_RESOLUTION_MIGRATION") == 1
    assert semantic.count("SEMANTIC_IR_VALUE_MIGRATION") == 1
    assert dispatcher.count("SEMANTIC_IR_MIGRATION") == 1
    assert dispatcher.count("SEMANTIC_IR_VALUE_MIGRATION") == 1
    assert dispatcher.count("SEMANTIC_ANALYSIS_LEGACY_BINDING_MIGRATION") == 1
    assert dispatcher.count("SEMANTIC_ANALYSIS_CONSTANT_SNAPSHOT_MIGRATION") == 1
    assert backend.count("SEMANTIC_IR_VALUE_MIGRATION") == 2

    assert "TYPE_OBJECT attribute semantics still belong" not in semantic
    assert "_COMPARE_OPS" not in semantic
    assert "_validate_binary" not in semantic
    assert "_validate_compare" not in semantic
    assert "Lower each comparison pair independently to preserve" not in backend


def test_backend_binding_bridge_and_invariant_drift():
    from NodeForge.blender_ir_lowering import lower_expression
    from NodeForge.values import Value

    socket = object()
    value = Value(socket, TYPE_FLOAT)
    comp = type("Comp", (), {"vars": {"a": value}, "group": None})()
    program = _lower("a", bindings={"a": TYPE_FLOAT})
    assert lower_expression(comp, program) is value

    drifted = type("Comp", (), {"vars": {"a": Value(socket, TYPE_VECTOR)}, "group": None})()
    with pytest.raises(CompileError, match="changed type"):
        lower_expression(drifted, program)
    missing = type("Comp", (), {"vars": {}, "group": None})()
    with pytest.raises(CompileError, match="no longer a runtime Value"):
        lower_expression(missing, program)


def test_backend_executes_program_order_and_applies_nonzero_base_depth(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    calls = []
    comp = type("Comp", (), {"group": object(), "vars": {"a": Value(object(), TYPE_FLOAT)}})()

    def fake_value(group, value, x=0, y=0):
        calls.append(("value", value, x, y))
        return Value(object(), TYPE_FLOAT)

    def fake_math(group, operation, args, x=0, y=0):
        calls.append(("math", operation, x, y))
        return Value(object(), TYPE_FLOAT)

    monkeypatch.setattr(backend, "_value", fake_value)
    monkeypatch.setattr(backend, "_math", fake_math)

    result = backend.lower_expression(comp, _lower("a * 2", bindings={"a": TYPE_FLOAT}), base_depth=3)
    assert result.typ == TYPE_FLOAT
    assert calls == [("value", 2, 960, -360), ("math", "MULTIPLY", 720, -270)]


def test_backend_missing_operand_is_controlled_internal_error():
    from NodeForge import blender_ir_lowering as backend

    missing = IRValue(99, TYPE_FLOAT)
    result = IRValue(0, TYPE_FLOAT)
    program = IRProgram((IRUnary(result, 0, "+", missing),), result)
    comp = type("Comp", (), {"group": object(), "vars": {}})()
    with pytest.raises(CompileError, match="used before materialization"):
        backend.lower_expression(comp, program)


def test_backend_unary_identity_and_current_node_policy_without_blender(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    calls = []
    comp = type(
        "Comp",
        (),
        {
            "group": object(),
            "vars": {
                "a": Value(object(), TYPE_FLOAT),
                "v": Value(object(), TYPE_VECTOR),
                "flag": Value(object(), TYPE_BOOL),
            },
        },
    )()

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

    plus = backend.lower_expression(comp, _lower("+a", bindings={"a": TYPE_FLOAT}), base_depth=1)
    assert plus is comp.vars["a"]
    assert calls == []

    backend.lower_expression(comp, _lower("-a", bindings={"a": TYPE_FLOAT}), base_depth=1)
    backend.lower_expression(comp, _lower("-v", bindings={"v": TYPE_VECTOR}), base_depth=1)
    backend.lower_expression(comp, _lower("not flag", bindings={"flag": TYPE_BOOL}), base_depth=1)
    assert ("value", 0.0, 240, -130) in calls
    assert ("math", "SUBTRACT", 240, -90) in calls
    assert ("value", -1.0, 240, -130) in calls
    assert ("vector_math", "SCALE", 240, -90) in calls
    assert ("boolean_math", "NOT", 240, -90) in calls


def test_backend_comparison_chain_executes_explicit_duplicate_operations(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    calls = []
    comp = type(
        "Comp",
        (),
        {
            "group": object(),
            "vars": {
                "a": Value(object(), TYPE_FLOAT),
                "b": Value(object(), TYPE_FLOAT),
                "c": Value(object(), TYPE_FLOAT),
            },
        },
    )()

    def fake_value(group, value, x=0, y=0):
        calls.append(("value", value, x, y))
        return Value(object(), TYPE_FLOAT)

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
    monkeypatch.setattr(backend, "_math", fake_math)
    monkeypatch.setattr(backend, "_compare", fake_compare)
    monkeypatch.setattr(backend, "_boolean_math", fake_boolean_math)

    program = _lower("a < b * 2 < c", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT})
    result = backend.lower_expression(comp, program, base_depth=1)
    assert result.typ == TYPE_BOOL
    assert calls.count(("math", "MULTIPLY", 480, -180)) == 2
    assert calls.count(("compare", "LESS_THAN", 240, -90)) == 2
    assert calls.count(("boolean_math", "AND", 240, -90)) == 1


def test_backend_result_type_drift_is_controlled_internal_error(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    comp = type("Comp", (), {"group": object(), "vars": {"a": Value(object(), TYPE_FLOAT)}})()

    def fake_value(group, value, x=0, y=0):
        return Value(object(), TYPE_FLOAT)

    def fake_math(group, operation, args, x=0, y=0):
        return Value(object(), TYPE_VECTOR)

    monkeypatch.setattr(backend, "_value", fake_value)
    monkeypatch.setattr(backend, "_math", fake_math)

    with pytest.raises(CompileError, match="expected type FLOAT, got VECTOR"):
        backend.lower_expression(comp, _lower("a + 1", bindings={"a": TYPE_FLOAT}))


def test_backend_vector_conditional_component_and_boolean_primitives_without_blender(monkeypatch):
    from NodeForge import blender_ir_lowering as backend
    from NodeForge.values import Value

    calls = []
    comp = type(
        "Comp",
        (),
        {
            "group": object(),
            "vars": {
                "v": Value(object(), TYPE_VECTOR),
                "a": Value(object(), TYPE_FLOAT),
                "flag": Value(object(), TYPE_BOOL),
                "other": Value(object(), TYPE_FLOAT),
            },
        },
    )()

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

    assert backend.lower_expression(comp, _lower("v / a", bindings={"v": TYPE_VECTOR, "a": TYPE_FLOAT})).typ == TYPE_VECTOR
    assert ("math", "DIVIDE", 0, 0) in calls
    assert ("vector_math", "SCALE", 0, 0) in calls

    calls.clear()
    conditional = _lower(
        "a if flag else other",
        bindings={"a": TYPE_FLOAT, "flag": TYPE_BOOL, "other": TYPE_FLOAT},
    )
    assert backend.lower_expression(comp, conditional).typ == TYPE_FLOAT
    assert calls == [("switch", 0, 0)]

    calls.clear()
    assert backend.lower_expression(comp, _lower("v.z", bindings={"v": TYPE_VECTOR})).typ == TYPE_FLOAT
    assert calls == [("component", "z", 0, 0)]

    calls.clear()
    bool_program = _lower("flag and True", bindings={"flag": TYPE_BOOL})
    assert backend.lower_expression(comp, bool_program).typ == TYPE_BOOL
    assert calls[-1] == ("boolean_math", "AND", 0, 0)
