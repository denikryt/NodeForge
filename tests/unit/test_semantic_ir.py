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
from NodeForge.semantic_lowering import try_lower_expression


pytestmark = pytest.mark.unit


def _expr(source):
    """Parse one expression fixture."""
    return ast.parse(source, mode="eval").body


def _lower(source, *, bindings=None, consts=None, labels=None):
    """Lower one source expression with a small pure semantic environment."""
    return try_lower_expression(
        _expr(source),
        runtime_binding_types=bindings or {},
        consts=consts or {},
        reserved_name_labels=labels or {},
    )


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


def test_unknown_and_unrepresented_constant_names_are_unsupported():
    assert _lower("unknown") is None
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


def test_exact_semantic_ir_migration_markers_are_present_at_source_decisions():
    root = Path(__file__).resolve().parents[2]
    semantic = (root / "semantic_lowering.py").read_text(encoding="utf-8")
    dispatcher = (root / "expression_compiler.py").read_text(encoding="utf-8")
    backend = (root / "blender_ir_lowering.py").read_text(encoding="utf-8")
    combined = semantic + dispatcher + backend
    assert combined.count("SEMANTIC_IR_MIGRATION") == 2
    assert combined.count("SEMANTIC_IR_VALUE_MIGRATION") == 4
    assert "Lower each comparison pair independently to preserve" not in backend

    old_object = '''# SEMANTIC_IR_MIGRATION: TYPE_OBJECT attribute semantics still belong to the
            # existing ObjectValue.resolve_property() path. The target architecture is for
            # Object property access to be represented and validated in Semantic IR. Remove
            # this fallback when TYPE_OBJECT ast.Attribute lowering is migrated end-to-end
            # and ObjectValue.resolve_property() is no longer the semantic owner.'''
    old_fallback = '''# SEMANTIC_IR_MIGRATION: Expressions outside the current IR slice continue on
    # the existing AST-to-Blender path while migration is incremental. The target
    # architecture is for migrated expression families to lower through Semantic IR
    # before Blender materialization. Remove this fallback only for an expression
    # family after that family is covered end-to-end by IR and its duplicated AST
    # lowering branch is removed in the same planned change set.'''
    new_binding_snapshot = '''# SEMANTIC_IR_VALUE_MIGRATION: comp.vars still stores legacy socket-bound Value
    # objects while statement and call migration is incomplete. Export only name ->
    # type into semantic lowering so Semantic IR stays backend-independent. Remove
    # this bridge when runtime bindings use an explicitly owned compiler value
    # reference and semantic typing no longer reads backend Value objects.'''
    new_binding_backend = '''# SEMANTIC_IR_VALUE_MIGRATION: Runtime bindings outside the migrated IR boundary
        # are still owned by comp.vars as socket-bound Value objects. Resolve an IR
        # binding to that backend value only here, at Blender lowering. Remove this
        # bridge when compiler runtime bindings use an explicitly owned compiler value
        # reference and backend sockets come only from the materialization environment.'''
    new_result = '''# SEMANTIC_IR_VALUE_MIGRATION: compile_expr() and downstream compiler consumers
    # still expect a socket-bound Value result. Return the materialized backend value
    # for the IR result until the compiler-facing expression contract uses an
    # explicitly owned compiler runtime value/reference model. Remove this bridge
    # when explicit Blender materialization happens only at a backend boundary.
    # Program-local IRValue handles alone are not compiler-wide identity.'''
    new_compare = '''# SEMANTIC_IR_VALUE_MIGRATION: Re-lower the shared middle source expression for
            # each comparison pair so this behavior-preserving stage emits distinct IR values
            # and preserves the current duplicated Geometry Nodes topology. Remove this rule
            # only in a dedicated topology-changing plan that defines IR value reuse and
            # updates the comparison-chain Blender regression contract.'''
    assert old_object in semantic
    assert old_fallback in dispatcher
    assert new_binding_snapshot in dispatcher
    assert new_binding_backend in backend
    assert new_result in backend
    assert new_compare in semantic


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
