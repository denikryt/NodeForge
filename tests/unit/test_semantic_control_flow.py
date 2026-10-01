"""Pure coverage for Runtime Control-Flow IR migration structured runtime control-flow Semantic IR."""

import ast

import pytest

from NodeForge.compile_time import CompileTimeSnapshot

from NodeForge.builtin_call_semantics import INPUT_DECLARATION_BUILTIN_NAMES, IR_CAPABLE_BUILTIN_NAMES
from NodeForge.call_resolution import CallableEnvironment
from NodeForge.compiler_identities import BindingId, InputDeclarationId
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.runtime_bindings import RuntimeBindingSymbol
from NodeForge.semantic_body import lower_basic_body
from NodeForge.semantic_ir import IRBranchMerge, IRIf, IRInputDeclaration, IRRepeat


def _callables():
    return CallableEnvironment(
        callable_builtins=frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES),
        local_functions={},
        imported_functions={},
    )


def _lower(source, *, bindings=None, constants=None, geometry_mode=False):
    return lower_basic_body(
        ast.parse(source, mode="exec").body,
        initial_runtime_bindings=bindings or {},
        initial_compile_time=CompileTimeSnapshot(constants or {}),
        reserved_name_labels={},
        callable_environment=_callables(),
        owner_scope="scope",
        geometry_mode=geometry_mode,
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


@pytest.mark.parametrize("condition", ["True", "False"])
def test_literal_bool_ordinary_if_is_structured_runtime_ir(condition):
    result = _lower(
        f'x = 1.0\nif {condition}:\n    x = x + 1\nelse:\n    x = x + 100\noutput(x)'
    )
    branch = next(statement for statement in result.body.statements if isinstance(statement, IRIf))
    assert branch.condition.result.typ is NFType.BOOL
    assert branch.true_body.statements
    assert branch.false_body.statements
    assert branch.merges == (IRBranchMerge(BindingId("scope", 0), "x", NFType.FLOAT),)
    assert "x" not in result.final_compile_time.values


def test_literal_bool_ordinary_if_uses_runtime_missing_else_diagnostic():
    with pytest.raises(CompileError, match="runtime if currently requires an else branch"):
        _lower('x = 0.0\nif True:\n    x = 1.0\noutput(x)')


def test_compile_time_known_non_bool_ordinary_if_uses_runtime_bool_diagnostic():
    with pytest.raises(CompileError, match=r"select\(cond, true, false\): cond must be Bool"):
        _lower('x = 0.0\nif 1:\n    x = 1.0\nelse:\n    x = 2.0\noutput(x)')


def test_literal_condition_ordinary_if_keeps_common_change_requirement():
    with pytest.raises(CompileError, match="runtime if branches must assign at least one common variable"):
        _lower('x = 0.0\ny = 0.0\nif True:\n    x = 1.0\nelse:\n    y = 2.0\noutput(x)')


def test_literal_condition_ordinary_if_keeps_exact_branch_type_requirement():
    with pytest.raises(CompileError, match="runtime if branch values for x have different types"):
        _lower('x = 0.0\nif False:\n    x = 1.0\nelse:\n    x = True\noutput(x)')


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


def test_runtime_if_compile_time_branches_join_from_the_same_incoming_snapshot():
    result = _lower(
        'c = 1\nflag = input_bool("Flag")\n'
        'if flag:\n    c = 2\nelse:\n    c = 3\n'
        'x = c + 10\noutput(x)'
    )
    branch = result.body.statements[2]
    assert isinstance(branch, IRIf)
    assert [merge.source_name for merge in branch.merges] == ["c"]
    assert "c" not in result.final_compile_time.values
    assert "x" not in result.final_compile_time.values


def test_runtime_if_compile_time_join_recovers_equal_stable_scalar_values():
    result = _lower(
        'n = 1\nflag = input_bool("Flag")\n'
        'if flag:\n    n = 2\nelse:\n    n = 2\n'
        'output(n)'
    )
    assert result.final_compile_time.values["n"] == 2


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
    assert state.typ is NFType.INT
    assert state.publish_to_parent is True
    assert "x" not in result.final_compile_time.values


def test_repeat_body_can_read_non_carried_outer_binding():
    result = _lower(
        'factor = input_float("Factor")\nx = 0.0\n'
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
    assert inner.states[0].typ is NFType.INT
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



def test_repeat_int_state_keeps_one_exact_type_through_exit():
    result = _lower(
        'x = input_int("X", default=1)\n'
        'for i in repeat_range(2):\n    x = x + 1\n'
        'output(x)'
    )
    repeat = result.body.statements[1]
    assert isinstance(repeat, IRRepeat)
    assert repeat.states[0].typ is NFType.INT
    assert result.body.statements[2].value.result.typ is NFType.INT


def test_repeat_float_state_keeps_one_exact_type_through_exit():
    result = _lower(
        'x = input_float("X", default=1.0)\n'
        'for i in repeat_range(2):\n    x = x + 1.0\n'
        'output(x)'
    )
    repeat = result.body.statements[1]
    assert isinstance(repeat, IRRepeat)
    assert repeat.states[0].typ is NFType.FLOAT
    assert result.body.statements[2].value.result.typ is NFType.FLOAT


def test_repeat_exact_state_rejects_int_to_float_assignment():
    with pytest.raises(CompileError, match="repeat_range state 'x' changed type from INT to FLOAT"):
        _lower(
            'x = input_int("X", default=1)\n'
            'for i in repeat_range(2):\n    x = x / 2\n'
            'output(x)'
        )


def test_repeat_exact_state_rejects_float_to_int_assignment():
    with pytest.raises(CompileError, match="repeat_range state 'x' changed type from FLOAT to INT"):
        _lower(
            'x = input_float("X", default=1.0)\n'
            'for i in repeat_range(2):\n    x = 1\n'
            'output(x)'
        )


def test_repeat_exact_state_rejects_temporary_type_change_even_if_later_restored():
    with pytest.raises(CompileError, match="repeat_range state 'x' changed type from INT to FLOAT"):
        _lower(
            'x = input_int("X", default=1)\n'
            'for i in repeat_range(2):\n'
            '    x = x / 2\n'
            '    x = 1\n'
            'output(x)'
        )


def test_repeat_exact_state_rejects_augmented_type_change_before_later_restore():
    with pytest.raises(CompileError, match="repeat_range state 'x' changed type from INT to FLOAT"):
        _lower(
            'x = input_int("X", default=1)\n'
            'for i in repeat_range(2):\n'
            '    x /= 2\n'
            '    x = 1\n'
            'output(x)'
        )


def test_repeat_exact_state_rejects_array_rebinding_before_runtime_state_disappears():
    with pytest.raises(CompileError, match="repeat_range state 'x' changed type from INT to ARRAY"):
        _lower(
            'x = input_int("X", default=1)\n'
            'for i in repeat_range(2):\n'
            '    x = (1, 2)\n'
            'output(x)'
        )


def test_repeat_exact_state_rejects_tuple_result_rebinding_before_runtime_state_disappears():
    with pytest.raises(CompileError, match="repeat_range state 'x' changed type from INT to TUPLE"):
        _lower(
            'geo = input_geometry("Geometry")\n'
            'x = input_int("X", default=1)\n'
            'for i in repeat_range(2):\n'
            '    x = capture_attribute(geo, 1.0)\n'
            'output(x)'
        )


def test_repeat_exact_state_rejects_named_output_rebinding_before_runtime_state_disappears():
    with pytest.raises(CompileError, match="repeat_range state 'x' changed type from INT to NAMED_OUTPUTS"):
        _lower(
            'x = input_int("X", default=1)\n'
            'for i in repeat_range(2):\n'
            '    x = node("ShaderNodeSeparateXYZ", outputs={"X": Float, "Y": Float})\n'
            'output(x)'
        )


def test_repeat_exact_state_rejects_tuple_assignment_type_change_before_later_restore():
    with pytest.raises(CompileError, match="repeat_range state 'x' changed type from INT to FLOAT"):
        _lower(
            'geo = input_geometry("Geometry")\n'
            'x = input_int("X", default=1)\n'
            'for i in repeat_range(2):\n'
            '    geo, x = capture_attribute(geo, 1.0)\n'
            '    x = 1\n'
            'output(x)'
        )


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


def test_runtime_if_structural_array_append_has_direct_diagnostic():
    source = 'items = [position()]\nflag = input_bool("Flag")\nif flag:\n    items.append(position())\nelse:\n    items.append(position())'
    with pytest.raises(CompileError, match="Structural array append inside runtime control flow is not supported"):
        _lower(source)


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
    )
    assert result.body is not None
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


def test_repeat_local_int_branch_merge_uses_type_directed_numeric_semantics():
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
    assert branch.merges[0].typ is NFType.INT
    assert not hasattr(branch.merges[0], "false_coerce_to")
    assert not hasattr(branch.merges[0], "true_coerce_to")


def test_repeat_local_float_branch_merge_keeps_exact_float_type():
    result = _lower(
        'x = input_float("X", default=1.0)\nflag = input_bool("Flag")\n'
        'for i in repeat_range(2):\n'
        '    if flag:\n        x = x + 1.0\n'
        '    else:\n        x = x\n'
        'output(x)'
    )
    repeat = result.body.statements[2]
    branch = repeat.body.statements[0]
    assert isinstance(branch, IRIf)
    assert branch.merges[0].typ is NFType.FLOAT


def test_repeat_local_if_rejects_int_to_float_assignment_before_merge():
    with pytest.raises(CompileError, match="repeat_range state 'x' changed type from INT to FLOAT"):
        _lower(
            'x = input_int("X", default=1)\nflag = input_bool("Flag")\n'
            'for i in repeat_range(2):\n'
            '    if flag:\n        x = x + 1\n'
            '    else:\n        x = x / 2\n'
            'output(x)'
        )


def test_repeat_local_if_rejects_float_to_int_assignment_before_merge():
    with pytest.raises(CompileError, match="repeat_range state 'x' changed type from FLOAT to INT"):
        _lower(
            'x = input_float("X", default=1.0)\nflag = input_bool("Flag")\n'
            'for i in repeat_range(2):\n'
            '    if flag:\n        x = x + 1.0\n'
            '    else:\n        x = 1\n'
            'output(x)'
        )


def test_post_repeat_int_remains_int_for_downstream_arithmetic():
    from NodeForge.semantic_ir import IRAssign, IRBinary

    result = _lower(
        'x = input_int("X", default=1)\n'
        'for i in repeat_range(2):\n    x = x + 1\n'
        'y = x + 1\noutput(y)'
    )
    y_assign = next(
        statement
        for statement in result.body.statements
        if isinstance(statement, IRAssign) and statement.source_name == "y"
    )
    binary = next(op for op in y_assign.value.operations if isinstance(op, IRBinary))
    assert binary.result.typ is NFType.INT


def test_contextual_store_inside_runtime_if_no_longer_forces_whole_body_fallback():
    result = _lower(
        'x = 0\nflag = input_bool("Flag")\n'
        'if flag:\n    store("a", x)\n    x = x + 1\nelse:\n    x = x + 2',
        geometry_mode=True,
    )
    assert result.body is not None
    branch = next(stmt for stmt in result.body.statements if isinstance(stmt, IRIf))
    assert branch.merges and branch.merges[0].source_name == "x"


def test_runtime_if_result_exposes_only_authoritative_compile_time_exit():
    """Branch-local CT exits remain private to lower_runtime_if()."""
    from dataclasses import fields
    from NodeForge.semantic_control_flow import RuntimeIfResult

    assert [field.name for field in fields(RuntimeIfResult)] == [
        "statement",
        "true_state",
        "false_state",
        "merged_compile_time",
    ]


# Stage 38 — runtime-if ownership and Repeat-local merge regressions.

def test_stage38_repeat_if_merges_iteration_local_created_in_both_branches():
    """A same-identity runtime local on both Repeat branch exits receives one ordinary merge."""
    result = _lower(
        'height = 10.0\nflag = input_bool("Flag")\n'
        'for i in repeat_range(2):\n'
        '    if flag:\n        delta = 1.0\n'
        '    else:\n        delta = 2.0\n'
        '    height = height + delta\n'
        'output(height)'
    )
    repeat = next(statement for statement in result.body.statements if isinstance(statement, IRRepeat))
    branch = next(statement for statement in repeat.body.statements if isinstance(statement, IRIf))
    assert [state.source_name for state in repeat.states] == ["height"]
    assert [(merge.source_name, merge.typ) for merge in branch.merges] == [("delta", NFType.FLOAT)]


def test_stage38_repeat_if_one_branch_local_does_not_escape_convergence():
    """A Repeat-local runtime name missing from one exit is unavailable after the nested if."""
    with pytest.raises(CompileError, match="Unknown name: delta"):
        _lower(
            'height = 10.0\nflag = input_bool("Flag")\n'
            'for i in repeat_range(2):\n'
            '    if flag:\n        delta = 1.0\n'
            '    else:\n        height = height + 1.0\n'
            '    height = height + delta\n'
            'output(height)'
        )


def test_stage38_repeat_if_merges_only_changed_carried_state():
    """Unchanged carried state does not acquire a redundant nested Switch merge."""
    result = _lower(
        'height = 10.0\nwater = 1.0\nflag = input_bool("Flag")\n'
        'for i in repeat_range(2):\n'
        '    if flag:\n        water = water + 1.0\n'
        '    else:\n        water = water + 2.0\n'
        '    height = height + 1.0\n'
        'output(height)'
    )
    repeat = next(statement for statement in result.body.statements if isinstance(statement, IRRepeat))
    branch = next(statement for statement in repeat.body.statements if isinstance(statement, IRIf))
    assert [state.source_name for state in repeat.states] == ["water", "height"]
    assert [merge.source_name for merge in branch.merges] == ["water"]


def test_stage38_repeat_if_orders_changed_carried_before_locals_by_binding_id():
    """Repeat merge ordering is carried-state order, then local BindingId allocation order."""
    result = _lower(
        'state = 0\nflag = input_bool("Flag")\n'
        'for i in repeat_range(2):\n'
        '    z_local = i + 1\n'
        '    a_local = i + 2\n'
        '    if flag:\n'
        '        state = state + 1\n'
        '        z_local = z_local + 1\n'
        '        a_local = a_local + 1\n'
        '    else:\n'
        '        state = state + 2\n'
        '        z_local = z_local + 2\n'
        '        a_local = a_local + 2\n'
        '    state = state + z_local + a_local\n'
        'output(state)'
    )
    repeat = next(statement for statement in result.body.statements if isinstance(statement, IRRepeat))
    branch = next(statement for statement in repeat.body.statements if isinstance(statement, IRIf))
    assert [merge.source_name for merge in branch.merges] == ["state", "z_local", "a_local"]
    local_ids = [merge.binding_id.local_id for merge in branch.merges[1:]]
    assert local_ids == sorted(local_ids)


@pytest.mark.parametrize(
    "true_assignment,false_assignment",
    [
        ("t = [1, 2]", "t = 2"),
        ("t = 2", "t = [1, 2]"),
        ("t = [1, 2]", "t = [3, 4]"),
    ],
)
def test_stage38_top_level_if_invalidates_stale_runtime_after_structural_rebind(
    true_assignment, false_assignment
):
    """An incoming runtime BindingId cannot survive when either exit loses runtime ownership."""
    source = (
        't = 1\nx = 0\nflag = input_bool("Flag")\n'
        f'if flag:\n    {true_assignment}\n    x = x + 1\n'
        f'else:\n    {false_assignment}\n    x = x + 2\n'
        'output(t)'
    )
    with pytest.raises(CompileError, match="Unknown name: t"):
        _lower(source)


def test_stage38_top_level_if_preserves_incoming_runtime_when_both_exits_keep_identity():
    """An untouched incoming runtime BindingId remains available after another value merges."""
    result = _lower(
        't = 1\nx = 0\nflag = input_bool("Flag")\n'
        'if flag:\n    x = x + 1\nelse:\n    x = x + 2\n'
        'output(t)'
    )
    branch = next(statement for statement in result.body.statements if isinstance(statement, IRIf))
    assert [merge.source_name for merge in branch.merges] == ["x"]
    assert result.body.statements[-1].value.operations[0].binding_id == BindingId("scope", 0)


def test_stage38_top_level_if_one_branch_new_runtime_local_remains_unavailable():
    """A one-sided new runtime local is not synthesized or inherited at convergence."""
    with pytest.raises(CompileError, match="Unknown name: y"):
        _lower(
            'x = 0\nflag = input_bool("Flag")\n'
            'if flag:\n    y = 1\n    x = x + 1\n'
            'else:\n    x = x + 2\n'
            'output(y)'
        )


def test_stage38_repeat_if_invalidates_stale_iteration_local_runtime_after_structural_rebind():
    """Repeat-local stale runtime ownership is removed by the same convergence rule as top-level if."""
    with pytest.raises(CompileError, match="Unknown name: t"):
        _lower(
            'height = 0\nflag = input_bool("Flag")\n'
            'for i in repeat_range(2):\n'
            '    t = i + 1\n'
            '    if flag:\n        t = [1, 2]\n'
            '    else:\n        t = i + 2\n'
            '    height = height + t\n'
            'output(height)'
        )


def test_stage38_repeat_if_runtime_local_merge_does_not_escape_repeat_boundary():
    """An iteration-local merge is usable in the iteration but is not published as Repeat state."""
    with pytest.raises(CompileError, match="Unknown name: delta"):
        _lower(
            'height = 0.0\nflag = input_bool("Flag")\n'
            'for i in repeat_range(2):\n'
            '    if flag:\n        delta = 1.0\n'
            '    else:\n        delta = 2.0\n'
            '    height = height + delta\n'
            'output(delta)'
        )


def test_stage38_nested_if_invalidation_is_visible_to_outer_convergence():
    """Outer runtime convergence sees the already-invalidated ordinary runtime state from the inner if."""
    with pytest.raises(CompileError, match="Unknown name: t"):
        _lower(
            't = 1\nx = 0\nouter = input_bool("Outer")\ninner = input_bool("Inner")\n'
            'if outer:\n'
            '    if inner:\n        t = [1, 2]\n        x = x + 1\n'
            '    else:\n        t = 2\n        x = x + 2\n'
            '    x = x + 3\n'
            'else:\n    t = 3\n    x = x + 4\n'
            'output(t)'
        )


def test_stage38_branch_local_structural_values_remain_legal_when_only_runtime_result_escapes():
    """Structural branch locals stay branch-owned while a same-type runtime projection merges normally."""
    result = _lower(
        'flag = input_bool("Flag")\n'
        'if flag:\n    values = [1.0, 2.0]\n    t = values[0]\n'
        'else:\n    values = [3.0, 4.0]\n    t = values[0]\n'
        'output(t)'
    )
    branch = next(statement for statement in result.body.statements if isinstance(statement, IRIf))
    assert [(merge.source_name, merge.typ) for merge in branch.merges] == [("t", NFType.FLOAT)]


def test_stage38_repeat_if_merges_existing_local_changed_on_only_one_branch():
    """A pre-branch iteration local merges against its unchanged runtime value on the other exit."""
    result = _lower(
        'state = 0\nflag = input_bool("Flag")\n'
        'for i in repeat_range(2):\n'
        '    t = i + 1\n'
        '    if flag:\n        t = t + 1\n'
        '    else:\n        state = state + 1\n'
        '    state = state + t\n'
        'output(state)'
    )
    repeat = next(statement for statement in result.body.statements if isinstance(statement, IRRepeat))
    branch = next(statement for statement in repeat.body.statements if isinstance(statement, IRIf))
    assert [merge.source_name for merge in branch.merges] == ["state", "t"]


def test_stage38_top_level_if_invalidates_body_owned_array_rebind():
    """A runtime-dependent structural array exit removes the incoming ordinary runtime owner."""
    with pytest.raises(CompileError, match="Unknown name: t"):
        _lower(
            't = 1\nx = 0\nflag = input_bool("Flag")\n'
            'if flag:\n    t = [index(), index() + 1]\n    x = x + 1\n'
            'else:\n    t = 2\n    x = x + 2\n'
            'output(t)'
        )


def test_stage38_top_level_if_invalidates_fixed_structural_rebind():
    """A tuple/named-output structural exit removes the incoming ordinary runtime owner."""
    with pytest.raises(CompileError, match="Unknown name: t"):
        _lower(
            'geo = input_geometry("Geometry")\nt = 1.0\nx = 0.0\nflag = input_bool("Flag")\n'
            'if flag:\n    t = capture_attribute(geo, 1.0)\n    x = x + 1.0\n'
            'else:\n    t = 2.0\n    x = x + 2.0\n'
            'output(t)'
        )

def test_stage38_nested_if_runtime_merge_is_visible_to_outer_convergence_positive():
    """Outer runtime-if lowering consumes the already-merged ordinary runtime state from an inner if."""
    result = _lower(
        't = 0\nouter = input_bool("Outer")\ninner = input_bool("Inner")\n'
        'if outer:\n'
        '    if inner:\n        t = t + 1\n'
        '    else:\n        t = t + 2\n'
        'else:\n    t = t + 3\n'
        'output(t)'
    )
    outer = next(statement for statement in result.body.statements if isinstance(statement, IRIf))
    inner = next(statement for statement in outer.true_body.statements if isinstance(statement, IRIf))
    assert [merge.binding_id for merge in inner.merges] == [BindingId("scope", 0)]
    assert [merge.binding_id for merge in outer.merges] == [BindingId("scope", 0)]

def test_stage38_object_runtime_owner_survives_when_both_exits_keep_identity_positive():
    """Object runtime metadata remains usable when runtime-if exits retain the incoming BindingId."""
    result = _lower(
        'obj = input_object("Object")\nx = 0\nflag = input_bool("Flag")\n'
        'if flag:\n    x = x + 1\nelse:\n    x = x + 2\n'
        'output(obj.location)'
    )
    branch = next(statement for statement in result.body.statements if isinstance(statement, IRIf))
    assert [merge.source_name for merge in branch.merges] == ["x"]


def test_stage38_object_runtime_owner_metadata_is_not_usable_after_cross_category_rebind():
    """Invalidating an Object runtime owner also prevents stale object-state/property reuse."""
    with pytest.raises(CompileError, match="Unknown name: obj"):
        _lower(
            'obj = input_object("Object")\nx = 0\nflag = input_bool("Flag")\n'
            'if flag:\n    obj = [1, 2]\n    x = x + 1\n'
            'else:\n    x = x + 2\n'
            'output(obj.location)'
        )
