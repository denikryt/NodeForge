"""Pure coverage for Runtime Control-Flow IR migration structured runtime control-flow Semantic IR."""

import ast

import pytest

from NodeForge.compile_time import CompileTimeSnapshot

from NodeForge.builtin_call_semantics import IR_CAPABLE_BUILTIN_NAMES, STATEFUL_FALLBACK_BUILTIN_NAMES
from NodeForge.call_resolution import CallableEnvironment
from NodeForge.compiler_identities import BindingId, InputDeclarationId
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.runtime_bindings import RuntimeBindingSymbol
from NodeForge.semantic_body import BODY_UNSUPPORTED, lower_basic_body
from NodeForge.semantic_ir import IRBranchMerge, IRIf, IRInputDeclaration, IRRepeat


def _callables():
    return CallableEnvironment(
        callable_builtins=frozenset(IR_CAPABLE_BUILTIN_NAMES | STATEFUL_FALLBACK_BUILTIN_NAMES),
        system_constructors={},
        local_functions={},
        backend_helper_names=frozenset(),
        imported_functions={},
    )


def _lower(source, *, bindings=None, constants=None, legacy=()):
    return lower_basic_body(
        ast.parse(source, mode="exec").body,
        initial_runtime_bindings=bindings or {},
        initial_compile_time=CompileTimeSnapshot(constants or {}),
        legacy_binding_names=frozenset(legacy),
        reserved_name_labels={},
        callable_environment=_callables(),
        owner_scope="scope",
    )


def test_runtime_if_is_structured_ir_with_exact_common_merge():
    result = _lower(
        'x = 1.0\nflag = input_bool("Flag")\n'
        'if flag:\n    x = x + 1\nelse:\n    x = x + 2\n'
        'output(x)'
    )
    branch = result.body.statements[2]
    assert isinstance(branch, IRIf)
    assert branch.condition.result.typ is NFType.BOOL
    assert branch.merges == (IRBranchMerge(BindingId("scope", 0), "x", NFType.FLOAT),)


def test_constant_if_is_folded_without_irif():
    result = _lower('x = 1.0\nif True:\n    x = x + 1\nelse:\n    x = x + 100\noutput(x)')
    assert not any(isinstance(statement, IRIf) for statement in result.body.statements)


def test_runtime_if_identity_assignment_does_not_count_as_change():
    with pytest.raises(CompileError, match="runtime if branches must assign at least one common variable"):
        _lower('x = 1.0\nflag = input_bool("Flag")\nif flag:\n    x = +x\nelse:\n    x = x\noutput(x)')


def test_runtime_if_new_name_in_both_branches_uses_one_reserved_binding_id():
    result = _lower('flag = input_bool("Flag")\nif flag:\n    y = 1.0\nelse:\n    y = 2.0\noutput(y)')
    branch = result.body.statements[1]
    assert isinstance(branch, IRIf)
    assert len(branch.merges) == 1
    assert branch.merges[0].source_name == "y"
    true_assign = branch.true_body.statements[0]
    false_assign = branch.false_body.statements[0]
    assert true_assign.binding_id == false_assign.binding_id == branch.merges[0].binding_id


def test_runtime_if_input_declaration_ordinals_are_root_owned_and_non_rewinding():
    result = _lower(
        'flag = input_bool("Flag")\n'
        'if flag:\n    x = input_float("A")\nelse:\n    x = input_float("B")\n'
        'output(x)'
    )
    branch = result.body.statements[1]
    true_decl = branch.true_body.statements[0]
    false_decl = branch.false_body.statements[0]
    assert isinstance(true_decl, IRInputDeclaration)
    assert isinstance(false_decl, IRInputDeclaration)
    assert true_decl.declaration_id == InputDeclarationId("scope", "x", 0)
    assert false_decl.declaration_id == InputDeclarationId("scope", "x", 1)


def test_runtime_if_object_identity_watermark_is_shared_and_non_rewinding(monkeypatch):
    """Branch-local Object maps share one monotonic root identity watermark."""
    import NodeForge.semantic_body as semantic_body

    original = semantic_body.analyze_expression
    snapshots = []

    def recording_analyze_expression(expr, environment):
        before = environment.object_semantics.next_object_id
        analysis = original(expr, environment)
        after = None if analysis is None else analysis.object_semantics.next_object_id
        snapshots.append((before, after, ast.unparse(expr)))
        return analysis

    monkeypatch.setattr(semantic_body, "analyze_expression", recording_analyze_expression)
    result = _lower(
        'x = input_float("X")\nflag = input_bool("Flag")\n'
        'if flag:\n'
        '    node("GeometryNodeObjectInfo", output="Object", typ=Object).info(as_instance=False)\n'
        '    x = x + 1\n'
        'else:\n'
        '    node("GeometryNodeObjectInfo", output="Object", typ=Object).info(as_instance=False)\n'
        '    x = x + 2\n'
        'output(x)'
    )
    assert isinstance(result.body.statements[2], IRIf)
    object_allocations = [entry for entry in snapshots if "GeometryNodeObjectInfo" in entry[2]]
    assert [(before, after) for before, after, _ in object_allocations] == [(0, 1), (1, 2)]


def test_runtime_if_constants_preserve_legacy_true_then_false_threading():
    result = _lower(
        'c = 1\nflag = input_bool("Flag")\n'
        'if flag:\n    c = 2\nelse:\n    c = 3\n'
        'x = c + 10\noutput(x)'
    )
    branch = result.body.statements[2]
    assert isinstance(branch, IRIf)
    assert [merge.source_name for merge in branch.merges] == ["c"]
    assert result.final_compile_time.values["c"] == 3
    assert result.final_compile_time.values["x"] == 13


def test_runtime_if_runtime_assignment_removes_constant_through_normal_assignment_semantics():
    result = _lower(
        'c = 1\nx = input_float("X")\nflag = input_bool("Flag")\n'
        'if flag:\n    c = x + 10\n    x = x + 1\nelse:\n    x = x + 2\n'
        'output(x)'
    )
    assert "c" not in result.final_compile_time.values


def test_repeat_has_explicit_carried_state_and_int_count_literal():
    result = _lower('x = 0\nfor i in repeat_range(2):\n    x = x + 1\noutput(x)')
    repeat = result.body.statements[1]
    assert isinstance(repeat, IRRepeat)
    assert repeat.iterations.result.typ is NFType.INT
    assert len(repeat.states) == 1
    state = repeat.states[0]
    assert state.source_name == "x"
    assert state.publish_to_parent is True
    assert "x" not in result.final_compile_time.values


def test_repeat_body_can_read_non_carried_outer_binding():
    result = _lower(
        'factor = input_float("Factor")\nx = 0\n'
        'for i in repeat_range(2):\n    x = x + factor\n'
        'output(x)'
    )
    assert isinstance(result.body.statements[2], IRRepeat)


def test_nested_repeat_allows_sole_nonpublishing_enclosing_iteration_state():
    result = _lower(
        'x = 0\nfor i in repeat_range(2):\n'
        '    for j in repeat_range(1):\n        i = i + 10\n'
        '    x = x + i\noutput(x)'
    )
    outer = result.body.statements[1]
    assert isinstance(outer, IRRepeat)
    assert [(state.source_name, state.publish_to_parent) for state in outer.states] == [("x", True)]
    inner = outer.body.statements[0]
    assert isinstance(inner, IRRepeat)
    assert len(inner.states) == 1
    assert inner.states[0].binding_id == outer.iteration_binding_id
    assert inner.states[0].publish_to_parent is False
    # The statement after the inner Repeat still resolves the outer lexical Iteration BindingId.
    post_inner = outer.body.statements[1]
    binding_ops = [op for op in post_inner.value.operations if getattr(op, "binding_id", None) == outer.iteration_binding_id]
    assert binding_ops


def test_repeat_without_any_physical_state_preserves_existing_error():
    with pytest.raises(CompileError, match="repeat_range loop must update at least one existing variable or geometry_builder"):
        _lower('for i in repeat_range(2):\n    for j in repeat_range(1):\n        i = i + 10')


def test_repeat_runtime_if_uses_same_irif_and_repeat_merge_policy():
    result = _lower(
        'x = 0\nflag = input_bool("Flag")\n'
        'for i in repeat_range(2):\n'
        '    if flag:\n        x = x + 1\n'
        'output(x)'
    )
    repeat = result.body.statements[2]
    branch = repeat.body.statements[0]
    assert isinstance(branch, IRIf)
    assert branch.false_body.statements == ()
    assert [merge.source_name for merge in branch.merges] == ["x"]



def test_repeat_int_state_keeps_physical_input_type_separate_from_logical_output_type():
    result = _lower(
        'x = input_int("X", default=1)\n'
        'for i in repeat_range(2):\n    x = x + 1\n'
        'output(x)'
    )
    repeat = result.body.statements[1]
    assert isinstance(repeat, IRRepeat)
    assert repeat.states[0].input_type is NFType.INT
    assert repeat.states[0].output_type is NFType.FLOAT


def test_repeat_local_if_merge_order_follows_repeat_state_order():
    result = _lower(
        'z = 0\na = 0\nflag = input_bool("Flag")\n'
        'for i in repeat_range(2):\n'
        '    if flag:\n        z = z + 1\n        a = a + 1\n'
        '    else:\n        z = z + 2\n        a = a + 2\n'
        'output(z)'
    )
    repeat = result.body.statements[3]
    assert [state.source_name for state in repeat.states] == ["z", "a"]
    branch = repeat.body.statements[0]
    assert isinstance(branch, IRIf)
    assert [merge.source_name for merge in branch.merges] == ["z", "a"]


@pytest.mark.parametrize(
    "source",
    [
        'items = [1]\nflag = input_bool("Flag")\nif flag:\n    items.append(2)\nelse:\n    items.append(3)',
        'x = 0\nflag = input_bool("Flag")\nif flag:\n    store("a", x)\n    x = x + 1\nelse:\n    x = x + 2',
    ],
)
def test_remaining_nested_control_flow_categories_fallback_whole_body(source):
    assert _lower(source) is BODY_UNSUPPORTED


def test_ordinary_for_inside_repeat_range_keeps_controlled_repeat_body_diagnostic():
    """This structural-array and compile-time unrolling refactor must not broaden Repeat grammar while migrating ordinary structural loops."""
    with pytest.raises(
        CompileError,
        match="repeat_range body supports assignments, builder methods, if blocks, and nested repeat_range loops",
    ):
        _lower("x = 0\nfor i in repeat_range(2):\n    for j in [1, 2]:\n        x = x + j")


def test_repeat_pre_scan_does_not_consume_input_declaration_ordinals():
    result = _lower(
        'x = 0\nfor i in repeat_range(1):\n'
        '    if i > 0:\n        y = input_float("A")\n        x = x + 1\n'
        '    else:\n        y = input_float("B")\n        x = x + 2\n'
        'output(x)'
    )
    repeat = result.body.statements[1]
    branch = repeat.body.statements[0]
    true_decl = branch.true_body.statements[0]
    false_decl = branch.false_body.statements[0]
    assert true_decl.declaration_id == InputDeclarationId("scope", "y", 0)
    assert false_decl.declaration_id == InputDeclarationId("scope", "y", 1)


def test_repeat_local_constant_bool_stays_runtime_irif_like_legacy_repeat_engine():
    result = _lower(
        'x = 0\nfor i in repeat_range(2):\n'
        '    if True:\n        x = x + 1\n'
        'output(x)'
    )
    repeat = result.body.statements[1]
    assert isinstance(repeat, IRRepeat)
    assert isinstance(repeat.body.statements[0], IRIf)

def test_builder_repeat_is_owned_by_semantic_ir():
    result = _lower(
        'builder = geometry_builder()\n'
        'for i in repeat_range(2):\n    builder.add(cube(1))\n'
        'builder.geometry',
        legacy=(),
    )
    assert result is not BODY_UNSUPPORTED
    repeat = next(statement for statement in result.body.statements if isinstance(statement, IRRepeat))
    assert [state.source_name for state in repeat.states] == ["builder"]


def test_top_level_runtime_if_preserves_missing_else_and_common_change_errors():
    with pytest.raises(CompileError, match="runtime if currently requires an else branch"):
        _lower('x = 0\nflag = input_bool("Flag")\nif flag:\n    x = x + 1\noutput(x)')
    with pytest.raises(CompileError, match="runtime if branches must assign at least one common variable"):
        _lower(
            'x = 0\nflag = input_bool("Flag")\n'
            'if flag:\n    x = x + 1\nelse:\n    y = 2\n'
            'output(x)'
        )


def test_top_level_runtime_if_preserves_exact_type_merge_contract():
    with pytest.raises(CompileError, match="runtime if branch values for x have different types"):
        _lower(
            'x = 0\nflag = input_bool("Flag")\n'
            'if flag:\n    x = 1\nelse:\n    x = vector(1, 2, 3)\n'
            'output(x)'
        )


def test_runtime_if_non_bool_condition_preserves_controlled_error():
    with pytest.raises(CompileError, match=r"select\(cond, true, false\): cond must be Bool"):
        _lower('x = 0\nflag = input_float("Flag")\nif flag:\n    x = 1\nelse:\n    x = 2\noutput(x)')


def test_repeat_local_int_float_branch_merge_is_explicitly_promoted():
    result = _lower(
        'x = input_int("X", default=1)\nflag = input_bool("Flag")\n'
        'for i in repeat_range(2):\n'
        '    if flag:\n        x = x + 1\n'
        '    else:\n        x = x\n'
        'output(x)'
    )
    repeat = result.body.statements[2]
    branch = repeat.body.statements[0]
    assert isinstance(branch, IRIf)
    assert branch.merges[0].typ is NFType.FLOAT
    assert branch.merges[0].false_coerce_to is NFType.FLOAT
