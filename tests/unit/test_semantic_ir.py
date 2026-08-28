"""Pure semantic-expression IR contracts and migration-boundary regressions."""

import ast
import dataclasses
import sys

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
    IRBoolOp,
    IRCompareChain,
    IRConditional,
    IRLiteral,
    IRUnary,
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


def test_semantic_modules_are_blender_independent_and_ir_is_immutable():
    assert "bpy" not in sys.modules
    ir = _lower("a + 1", bindings={"a": TYPE_FLOAT})
    assert isinstance(ir, IRBinary)
    assert dataclasses.is_dataclass(ir)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ir.typ = TYPE_INT

    def contains_ast(value):
        if isinstance(value, ast.AST):
            return True
        if dataclasses.is_dataclass(value):
            return any(contains_ast(getattr(value, field.name)) for field in dataclasses.fields(value))
        if isinstance(value, tuple):
            return any(contains_ast(item) for item in value)
        return False

    assert not contains_ast(ir)


@pytest.mark.parametrize(
    ("source", "value", "typ"),
    [
        ("1", 1, TYPE_FLOAT),
        ("1.5", 1.5, TYPE_FLOAT),
        ("True", True, TYPE_BOOL),
        ('"name"', "name", TYPE_STRING),
    ],
)
def test_literal_typing(source, value, typ):
    ir = _lower(source)
    assert ir == IRLiteral(value, typ)


def test_runtime_binding_precedes_compile_time_constant():
    ir = _lower("a", bindings={"a": TYPE_FLOAT}, consts={"a": 1})
    assert ir == IRBinding("a", TYPE_FLOAT)


def test_supported_scalar_const_and_allowed_const_lower_to_literals():
    assert _lower("a", consts={"a": 7}) == IRLiteral(7, TYPE_FLOAT)
    pi = _lower("pi")
    assert isinstance(pi, IRLiteral)
    assert pi.typ == TYPE_FLOAT


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
    ir = _lower(source, bindings=bindings)
    assert isinstance(ir, IRBinary)
    assert ir.typ == typ


def test_invalid_owned_binary_operation_is_compile_error():
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and FLOAT"):
        _lower("flag + a", bindings={"flag": TYPE_BOOL, "a": TYPE_FLOAT})


@pytest.mark.parametrize("typ", [TYPE_FLOAT, TYPE_MATERIAL, TYPE_OBJECT, TYPE_GEOMETRY, TYPE_BUNDLE, TYPE_STRING])
def test_unary_plus_is_identity_semantics_for_any_ir_owned_runtime_value(typ):
    ir = _lower("+a", bindings={"a": typ})
    assert isinstance(ir, IRUnary)
    assert ir.op == "+"
    assert ir.typ == typ
    assert ir.operand == IRBinding("a", typ)


def test_unary_minus_and_not_types():
    assert _lower("-a", bindings={"a": TYPE_FLOAT}).typ == TYPE_FLOAT
    assert _lower("-v", bindings={"v": TYPE_VECTOR}).typ == TYPE_VECTOR
    assert _lower("not flag", bindings={"flag": TYPE_BOOL}).typ == TYPE_BOOL
    with pytest.raises(CompileError, match="not expects Bool"):
        _lower("not a", bindings={"a": TYPE_FLOAT})


def test_boolop_validates_pairs_before_later_operands():
    with pytest.raises(CompileError, match="Boolean operations expect Bool values"):
        _lower("True and 1 and (True + 1)")


def test_boolop_error_precedes_unsupported_later_call():
    with pytest.raises(CompileError, match="Boolean operations expect Bool values"):
        _lower("True and 1 and legacy_call()")

def test_comparison_chain_types_and_contracts():
    ir = _lower("a < b <= c", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT})
    assert isinstance(ir, IRCompareChain)
    assert ir.ops == ("LESS_THAN", "LESS_EQUAL")
    assert ir.typ == TYPE_BOOL
    assert isinstance(_lower("a == b", bindings={"a": TYPE_BOOL, "b": TYPE_BOOL}), IRCompareChain)
    assert isinstance(_lower("a == b", bindings={"a": TYPE_VECTOR, "b": TYPE_VECTOR}), IRCompareChain)
    with pytest.raises(CompileError, match="Comparison inputs must both be numeric"):
        _lower("a == b", bindings={"a": TYPE_BOOL, "b": TYPE_VECTOR})


@pytest.mark.parametrize("typ", [TYPE_FLOAT, TYPE_INT, TYPE_VECTOR, TYPE_BOOL, TYPE_GEOMETRY, TYPE_STRING, TYPE_BUNDLE])
def test_conditional_supported_backend_types_are_owned(typ):
    ir = _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": typ, "b": typ})
    assert isinstance(ir, IRConditional)
    assert ir.typ == typ


def test_conditional_validation_and_backend_ownership_order():
    with pytest.raises(CompileError, match="cond must be Bool"):
        _lower("a if cond else b", bindings={"cond": TYPE_FLOAT, "a": TYPE_FLOAT, "b": TYPE_FLOAT})
    with pytest.raises(CompileError, match="same type"):
        _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_FLOAT, "b": TYPE_VECTOR})
    assert _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_MATERIAL, "b": TYPE_MATERIAL}) is None
    assert _lower("a if flag else b", bindings={"flag": TYPE_BOOL, "a": TYPE_OBJECT, "b": TYPE_OBJECT}) is None
    with pytest.raises(CompileError, match="cond must be Bool"):
        _lower("a if cond else b", bindings={"cond": TYPE_FLOAT, "a": TYPE_MATERIAL, "b": TYPE_MATERIAL})


def test_vector_component_and_object_attribute_ownership():
    ir = _lower("v.x", bindings={"v": TYPE_VECTOR})
    assert ir == IRVectorComponent(IRBinding("v", TYPE_VECTOR), "x", TYPE_FLOAT)
    with pytest.raises(CompileError, match="only be used on Vector"):
        _lower("a.x", bindings={"a": TYPE_FLOAT})
    assert _lower("obj.location", bindings={"obj": TYPE_OBJECT}) is None
    assert _lower("obj.x", bindings={"obj": TYPE_OBJECT}) is None


def test_mixed_binop_stops_at_first_unsupported_child():
    assert _lower("legacy_call() + (True + 1)") is None


def test_mixed_binop_propagates_earlier_compile_error():
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and FLOAT"):
        _lower("(True + 1) + legacy_call()")

def test_ifexp_child_error_precedes_parent_condition_validation():
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and FLOAT"):
        _lower("(True + 1) if 1 else 2")


def test_compare_stops_at_first_unsupported_pair_boundary():
    assert _lower("1 < legacy_call() < (True + 1)") is None


@pytest.mark.parametrize("source", ["foo(a)", "obj.info()", "[a, b]", "(a, b)", "a[0]"])
def test_explicit_first_slice_boundaries_are_unsupported(source):
    assert _lower(source, bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "obj": TYPE_OBJECT}) is None


def test_backend_binding_reuses_runtime_value_and_rejects_invariant_drift():
    from NodeForge.blender_ir_lowering import lower_expression
    from NodeForge.values import Value

    socket = object()
    value = Value(socket, TYPE_FLOAT)
    comp = type("Comp", (), {"vars": {"a": value}, "group": None})()
    assert lower_expression(comp, IRBinding("a", TYPE_FLOAT)) is value

    with pytest.raises(CompileError, match="changed type"):
        lower_expression(comp, IRBinding("a", TYPE_VECTOR))
    with pytest.raises(CompileError, match="no longer a runtime Value"):
        lower_expression(comp, IRBinding("missing", TYPE_FLOAT))


def test_exact_semantic_ir_migration_markers_are_present_at_source_decisions():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    semantic = (root / "semantic_lowering.py").read_text(encoding="utf-8")
    dispatcher = (root / "expression_compiler.py").read_text(encoding="utf-8")
    backend = (root / "blender_ir_lowering.py").read_text(encoding="utf-8")
    combined = semantic + dispatcher + backend
    assert combined.count("SEMANTIC_IR_MIGRATION") == 3

    marker1 = '''# SEMANTIC_IR_MIGRATION: TYPE_OBJECT attribute semantics still belong to the
            # existing ObjectValue.resolve_property() path. The target architecture is for
            # Object property access to be represented and validated in Semantic IR. Remove
            # this fallback when TYPE_OBJECT ast.Attribute lowering is migrated end-to-end
            # and ObjectValue.resolve_property() is no longer the semantic owner.
            if base.typ == TYPE_OBJECT:'''
    marker2 = '''# SEMANTIC_IR_MIGRATION: Expressions outside the current IR slice continue on
    # the existing AST-to-Blender path while migration is incremental. The target
    # architecture is for migrated expression families to lower through Semantic IR
    # before Blender materialization. Remove this fallback only for an expression
    # family after that family is covered end-to-end by IR and its duplicated AST
    # lowering branch is removed in the same planned change set.
    if ir is None:'''
    marker3 = '''# SEMANTIC_IR_MIGRATION: Lower each comparison pair independently to preserve
        # the current Geometry Nodes topology, including repeated materialization of a
        # shared middle operand. The target architecture may define an explicit IR
        # value-reuse/materialization policy. Change or remove this compatibility rule
        # only in a dedicated topology-changing plan with updated Blender regressions.
        for i, op in enumerate(ir.ops):'''
    assert marker1 in semantic
    assert marker2 in dispatcher
    assert marker3 in backend


def test_backend_unary_lowering_policy_without_blender(monkeypatch):
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

    plus = backend.lower_expression(comp, _lower("+a", bindings={"a": TYPE_FLOAT}), depth=1)
    assert plus is comp.vars["a"]
    assert calls == []

    backend.lower_expression(comp, _lower("-a", bindings={"a": TYPE_FLOAT}), depth=1)
    backend.lower_expression(comp, _lower("-v", bindings={"v": TYPE_VECTOR}), depth=1)
    backend.lower_expression(comp, _lower("not flag", bindings={"flag": TYPE_BOOL}), depth=1)
    assert ("value", 0.0, 240, -130) in calls
    assert ("math", "SUBTRACT", 240, -90) in calls
    assert ("value", -1.0, 240, -130) in calls
    assert ("vector_math", "SCALE", 240, -90) in calls
    assert ("boolean_math", "NOT", 240, -90) in calls


def test_backend_comparison_chain_relowers_middle_operand_without_blender(monkeypatch):
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

    ir = _lower("a < b * 2 < c", bindings={"a": TYPE_FLOAT, "b": TYPE_FLOAT, "c": TYPE_FLOAT})
    result = backend.lower_expression(comp, ir, depth=1)
    assert result.typ == TYPE_BOOL
    assert calls.count(("math", "MULTIPLY", 480, -180)) == 2
    assert calls.count(("compare", "LESS_THAN", 240, -90)) == 2
    assert calls.count(("boolean_math", "AND", 240, -90)) == 1
