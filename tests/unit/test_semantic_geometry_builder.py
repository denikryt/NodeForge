"""Pure frontend tests for GeometryBuilder semantic state and IR desugaring."""

import ast
from pathlib import Path

import pytest

from NodeForge.builtin_call_semantics import INPUT_DECLARATION_BUILTIN_NAMES, IR_CAPABLE_BUILTIN_NAMES
from NodeForge.call_resolution import CallableEnvironment
from NodeForge.compile_time import CompileTimeSnapshot
from NodeForge.compiler_identities import BindingId
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.semantic_body import lower_basic_body
from NodeForge.semantic_geometry_builder import GeometryBuilderState, empty_geometry_program, join_binding_program
from NodeForge.semantic_ir import IRBindLeaves, IRCall, IRIf, IROutput, IRRepeat


def _callables():
    return CallableEnvironment(
        callable_builtins=frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES),
        local_functions={},
        imported_functions={},
    )


def _lower(source):
    return lower_basic_body(
        ast.parse(source).body,
        initial_runtime_bindings={},
        initial_compile_time=CompileTimeSnapshot({}),
        reserved_name_labels={},
        callable_environment=_callables(),
        owner_scope="scope",
    )


def test_builder_state_is_immutable_and_current_state_has_no_pending_values():
    state = GeometryBuilderState(BindingId("scope", 0))
    pending = state.with_pending(BindingId("scope", 1))
    assert state.pending_binding_ids == ()
    assert pending.pending_binding_ids == (BindingId("scope", 1),)
    assert pending.current().has_current is True
    with pytest.raises(ValueError):
        GeometryBuilderState(BindingId("scope", 0), (BindingId("scope", 1),), True)


def test_builder_accumulation_helpers_use_existing_empty_and_join_call_ir():
    empty = empty_geometry_program()
    assert isinstance(empty.operations[-1], IRCall)
    assert empty.operations[-1].target.name == "empty_geometry"

    one = join_binding_program((BindingId("scope", 1),))
    assert not any(isinstance(op, IRCall) for op in one.operations)

    three = join_binding_program(tuple(BindingId("scope", i) for i in (1, 2, 3)))
    calls = [op for op in three.operations if isinstance(op, IRCall)]
    assert len(calls) == 1
    assert calls[0].target.name == "join"
    assert len(calls[0].arguments) == 3


def test_builder_constructor_emits_no_body_statement_and_clears_auto_final_selection():
    result = _lower('builder = geometry_builder()')
    assert result.body is not None
    assert result.body.statements == ()
    assert result.clear_auto_final_output is True


def test_later_runtime_statement_supersedes_constructor_auto_final_clear():
    result = _lower('builder = geometry_builder()\nvalue = 1.0')
    assert result.clear_auto_final_output is False


def test_empty_extend_clears_prior_auto_final_selection_without_builder_runtime_ir():
    result = _lower(
        'builder = geometry_builder()\n'
        'value = 1.0\n'
        'builder.extend([])'
    )
    assert result.clear_auto_final_output is True


def test_straight_line_builder_is_semantic_owned_and_single_snapshot_avoids_join():
    result = _lower('builder = geometry_builder()\nbuilder.add(cube(1.0))\noutput("Geometry", builder.geometry)')
    assert result.body is not None
    joins = [
        op
        for statement in result.body.statements
        if isinstance(statement, IRBindLeaves)
        for op in statement.value.operations
        if isinstance(op, IRCall) and op.target.name == "join"
    ]
    assert joins == []
    assert isinstance(result.body.statements[-1], IROutput)


def test_three_pre_snapshot_adds_batch_into_one_join():
    result = _lower(
        'builder = geometry_builder()\n'
        'builder.add(cube(1.0))\n'
        'builder.add(cube(2.0))\n'
        'builder.add(cube(3.0))\n'
        'output("Geometry", builder.geometry)'
    )
    joins = [
        op
        for statement in result.body.statements
        if isinstance(statement, IRBindLeaves)
        for op in statement.value.operations
        if isinstance(op, IRCall) and op.target.name == "join"
    ]
    assert len(joins) == 1
    assert len(joins[0].arguments) == 3


def test_post_snapshot_add_is_sequential_join_on_stable_hidden_binding():
    result = _lower(
        'builder = geometry_builder()\n'
        'first = builder.geometry\n'
        'builder.add(cube(1.0))\n'
        'output("Geometry", builder.geometry)'
    )
    joins = [
        op
        for statement in result.body.statements
        if isinstance(statement, IRBindLeaves)
        for op in statement.value.operations
        if isinstance(op, IRCall) and op.target.name == "join"
    ]
    assert len(joins) == 1
    assert len(joins[0].arguments) == 2


def test_builder_extend_validates_complete_flat_geometry_array():
    result = _lower('builder = geometry_builder()\nbuilder.extend([cube(1.0), cube(2.0)])\noutput("Geometry", builder.geometry)')
    assert result.body is not None
    with pytest.raises(CompileError, match="expects an array of Geometry values"):
        _lower('builder = geometry_builder()\nbuilder.extend([cube(1.0), 1.0])\noutput("Geometry", builder.geometry)')


def test_builder_only_repeat_uses_hidden_geometry_repeat_state():
    result = _lower(
        'builder = geometry_builder()\n'
        'for i in repeat_range(2):\n'
        '    builder.add(cube(1.0))\n'
        'output("Geometry", builder.geometry)'
    )
    repeat = next(statement for statement in result.body.statements if isinstance(statement, IRRepeat))
    assert len(repeat.states) == 1
    assert repeat.states[0].source_name == "builder"
    assert repeat.states[0].typ is NFType.GEOMETRY


def test_repeat_runtime_if_merges_builder_hidden_geometry_state():
    result = _lower(
        'builder = geometry_builder()\n'
        'flag = input_bool("Flag", default=True)\n'
        'for i in repeat_range(2):\n'
        '    if flag:\n'
        '        builder.add(cube(1.0))\n'
        '    else:\n'
        '        x = 0.0\n'
        'output("Geometry", builder.geometry)'
    )
    repeat = next(statement for statement in result.body.statements if isinstance(statement, IRRepeat))
    branch = next(statement for statement in repeat.body.statements if isinstance(statement, IRIf))
    assert any(merge.source_name == "builder" and merge.typ is NFType.GEOMETRY for merge in branch.merges)


def test_top_level_runtime_if_rejects_mutation_of_inherited_builder():
    with pytest.raises(CompileError, match="supported only inside repeat_range"):
        _lower(
            'builder = geometry_builder()\n'
            'flag = input_bool("Flag", default=True)\n'
            'if flag:\n'
            '    builder.add(cube(1.0))\n'
            '    value = 1.0\n'
            'else:\n'
            '    value = 2.0\n'
            'output(value)'
        )


def test_top_level_runtime_if_keeps_accepted_branch_local_builder_scope():
    result = _lower(
        'flag = input_bool("Flag", default=True)\n'
        'if flag:\n'
        '    result = geometry_builder()\n'
        '    result.add(cube(1.0))\n'
        '    geometry = result.geometry\n'
        'else:\n'
        '    geometry = cube(2.0)\n'
        'output("Geometry", geometry)'
    )
    branch = next(statement for statement in result.body.statements if isinstance(statement, IRIf))
    assert [merge.source_name for merge in branch.merges] == ["geometry"]


def test_top_level_runtime_if_accepts_false_only_branch_local_builder():
    result = _lower(
        'flag = input_bool("Flag", default=True)\n'
        'if flag:\n'
        '    value = 1.0\n'
        'else:\n'
        '    unused = geometry_builder()\n'
        '    unused.extend([cube(1.0), cube(2.0)])\n'
        '    snapshot = unused.geometry\n'
        '    value = 2.0\n'
        'output(value)'
    )
    assert result.body is not None
    branch = next(statement for statement in result.body.statements if isinstance(statement, IRIf))
    assert [merge.source_name for merge in branch.merges] == ["value"]


def test_new_branch_local_builder_does_not_escape_runtime_if():
    with pytest.raises(CompileError, match="Unknown name: local"):
        _lower(
            'flag = input_bool("Flag", default=True)\n'
            'if flag:\n'
            '    local = geometry_builder()\n'
            '    value = 1.0\n'
            'else:\n'
            '    value = 2.0\n'
            'output("Geometry", local.geometry)'
        )


def test_runtime_if_replacement_vs_inherited_mutation_preserves_legacy_rejection():
    with pytest.raises(CompileError, match="supported only inside repeat_range"):
        _lower(
            'builder = geometry_builder()\n'
            'flag = input_bool("Flag", default=True)\n'
            'if flag:\n'
            '    builder = geometry_builder()\n'
            '    value = 1.0\n'
            'else:\n'
            '    builder.add(cube(1.0))\n'
            '    value = 2.0\n'
            'output(value)'
        )


def test_runtime_if_inside_repeat_rejects_branch_local_constructor_before_builder_state_escape():
    with pytest.raises(CompileError, match=r"geometry_builder\(\) must be assigned to a simple name"):
        _lower(
            'value = input_float("Value", default=0.0)\n'
            'flag = input_bool("Flag", default=True)\n'
            'for i in repeat_range(2):\n'
            '    if flag:\n'
            '        local = geometry_builder()\n'
            '        value = value + 1.0\n'
            '    else:\n'
            '        value = value\n'
            'output(value)'
        )


def test_existing_builder_replacement_directly_inside_repeat_preserves_legacy_rejection():
    with pytest.raises(CompileError, match="Cannot assign over geometry_builder binding"):
        _lower(
            'builder = geometry_builder()\n'
            'value = input_float("Value", default=0.0)\n'
            'for i in repeat_range(2):\n'
            '    builder = geometry_builder()\n'
            '    value = value + 1.0\n'
            'output(value)'
        )


def test_top_level_runtime_if_preserves_legacy_rejection_for_two_branch_builder_identity_merge():
    with pytest.raises(CompileError, match="geometry_builder cannot escape script scope"):
        _lower(
            'flag = input_bool("Flag", default=True)\n'
            'if flag:\n'
            '    result = geometry_builder()\n'
            '    value = 1.0\n'
            'else:\n'
            '    result = geometry_builder()\n'
            '    value = 2.0\n'
            'output(value)'
        )


def test_one_sided_builder_replacement_restores_parent_identity_after_runtime_if():
    result = _lower(
        'builder = geometry_builder()\n'
        'builder.add(cube(0.5))\n'
        'before = builder.geometry\n'
        'flag = input_bool("Flag", default=True)\n'
        'if flag:\n'
        '    builder = geometry_builder()\n'
        '    builder.add(cube(1.0))\n'
        '    value = 1.0\n'
        'else:\n'
        '    value = 2.0\n'
        'after = builder.geometry\n'
        'output("Geometry", after)'
    )
    assert result.body is not None
    branch = next(statement for statement in result.body.statements if isinstance(statement, IRIf))
    assert [merge.source_name for merge in branch.merges] == ["value"]


def test_builder_constructor_inside_repeat_preserves_legacy_rejection_contract():
    with pytest.raises(CompileError, match=r"geometry_builder\(\) must be assigned to a simple name"):
        _lower(
            'value = input_float("Value", default=0.0)\n'
            'for i in repeat_range(2):\n'
            '    value = value + 1.0\n'
            '    unused = geometry_builder()\n'
            'output(value)'
        )


def test_flat_tuple_target_builder_loop_uses_frontend_unrolling():
    result = _lower(
        'builder = geometry_builder()\n'
        'for x, y in [[1.0, 2.0]]:\n'
        '    builder.add(cube(x))\n'
        'output("Geometry", builder.geometry)'
    )
    assert result.body is not None


def test_semantic_builder_module_has_no_backend_dependencies():
    import io
    import tokenize

    root = Path(__file__).resolve().parents[2]
    source = (root / "semantic_geometry_builder.py").read_text(encoding="utf-8")
    code_only = "".join(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in {tokenize.COMMENT, tokenize.STRING}
    )
    for forbidden in (
        "importbpy",
        "from.values",
        "from.nodes",
        "from.geometryimport",
        "from.runtime",
        "from.compilerimport",
        "from.statement_compiler",
        "from.expression_compiler",
        "from.blender_ir_lowering",
    ):
        assert forbidden not in code_only

    semantic_body_source = (root / "semantic_body.py").read_text(encoding="utf-8")
    assert "from .geometry_builder import" not in semantic_body_source
    assert "GeometryBuilder(" not in semantic_body_source


def _top_level_join_calls(result):
    """Return top-level builder Join calls emitted through IRBindLeaves statements."""
    return [
        op
        for statement in result.body.statements
        if isinstance(statement, IRBindLeaves)
        for op in statement.value.operations
        if isinstance(op, IRCall) and op.target.name == "join"
    ]


def test_repeated_snapshot_read_materializes_pending_builder_only_once():
    result = _lower(
        'builder = geometry_builder()\n'
        'builder.add(cube(1.0))\n'
        'builder.add(cube(2.0))\n'
        'first = builder.geometry\n'
        'second = builder.geometry\n'
        'output("Geometry", second)'
    )
    joins = _top_level_join_calls(result)
    assert len(joins) == 1
    assert len(joins[0].arguments) == 2


def test_builder_add_of_its_snapshot_uses_one_post_snapshot_join():
    result = _lower(
        'builder = geometry_builder()\n'
        'builder.add(cube(1.0))\n'
        'builder.add(builder.geometry)\n'
        'output("Geometry", builder.geometry)'
    )
    joins = _top_level_join_calls(result)
    assert len(joins) == 1
    assert len(joins[0].arguments) == 2


def test_builder_extend_after_snapshot_uses_sequential_joins():
    result = _lower(
        'builder = geometry_builder()\n'
        'builder.add(cube(1.0))\n'
        'snapshot = builder.geometry\n'
        'builder.extend([cube(2.0), cube(3.0)])\n'
        'output("Geometry", builder.geometry)'
    )
    joins = _top_level_join_calls(result)
    assert len(joins) == 2
    assert all(len(call.arguments) == 2 for call in joins)


def test_builder_extend_accepts_body_owned_array_alias():
    result = _lower(
        'parts = [cube(1.0), cube(2.0)]\n'
        'alias = parts\n'
        'builder = geometry_builder()\n'
        'builder.extend(alias)\n'
        'output("Geometry", builder.geometry)'
    )
    assert result.body is not None
    joins = _top_level_join_calls(result)
    assert len(joins) == 1
    assert len(joins[0].arguments) == 2


def test_repeat_state_order_tracks_first_builder_and_runtime_mutation():
    builder_first = _lower(
        'builder = geometry_builder()\n'
        'value = 0.0\n'
        'for i in repeat_range(2):\n'
        '    builder.add(cube(1.0))\n'
        '    value = value + 1.0\n'
        'output(value)'
    )
    repeat = next(statement for statement in builder_first.body.statements if isinstance(statement, IRRepeat))
    assert [state.source_name for state in repeat.states] == ["builder", "value"]

    runtime_first = _lower(
        'builder = geometry_builder()\n'
        'value = 0.0\n'
        'for i in repeat_range(2):\n'
        '    value = value + 1.0\n'
        '    builder.add(cube(1.0))\n'
        'output(value)'
    )
    repeat = next(statement for statement in runtime_first.body.statements if isinstance(statement, IRRepeat))
    assert [state.source_name for state in repeat.states] == ["value", "builder"]


def test_nested_repeat_reuses_same_hidden_builder_binding_identity():
    result = _lower(
        'builder = geometry_builder()\n'
        'for i in repeat_range(2):\n'
        '    builder.add(cube(1.0))\n'
        '    for j in repeat_range(2):\n'
        '        builder.add(cube(0.5))\n'
        'output("Geometry", builder.geometry)'
    )
    outer = next(statement for statement in result.body.statements if isinstance(statement, IRRepeat))
    inner = next(statement for statement in outer.body.statements if isinstance(statement, IRRepeat))
    outer_builder = next(state for state in outer.states if state.source_name == "builder")
    inner_builder = next(state for state in inner.states if state.source_name == "builder")
    assert inner_builder.binding_id == outer_builder.binding_id


def test_read_only_builder_inside_repeat_is_not_repeat_state():
    result = _lower(
        'builder = geometry_builder()\n'
        'builder.add(cube(1.0))\n'
        'value = 0.0\n'
        'for i in repeat_range(2):\n'
        '    snapshot = builder.geometry\n'
        '    value = value + 1.0\n'
        'output("Geometry", builder.geometry)'
    )
    repeat = next(statement for statement in result.body.statements if isinstance(statement, IRRepeat))
    assert [state.source_name for state in repeat.states] == ["value"]


def test_branch_local_builder_runtime_if_identity_assignment_remains_merge_eligible():
    """Preserve the coordinator-approved legacy case where one branch assigns ``value = value``."""
    result = _lower(
        'value = input_float("Value", default=0.0)\n'
        'flag = input_bool("Flag", default=True)\n'
        'if flag:\n'
        '    value = value + 1\n'
        '    unused = geometry_builder()\n'
        'else:\n'
        '    value = value\n'
        'output("Value", value)'
    )
    assert result.body is not None
    runtime_ifs = [statement for statement in result.body.statements if isinstance(statement, IRIf)]
    assert len(runtime_ifs) == 1
    assert [(merge.source_name, merge.typ) for merge in runtime_ifs[0].merges] == [("value", NFType.FLOAT)]


def test_identity_assignments_without_branch_local_builder_keep_existing_runtime_if_rejection():
    """The compatibility merge rule must not redefine identity assignment globally."""
    with pytest.raises(CompileError, match="runtime if branches must assign at least one common variable"):
        _lower(
            'value = input_float("Value", default=0.0)\n'
            'flag = input_bool("Flag", default=True)\n'
            'if flag:\n'
            '    value = value\n'
            'else:\n'
            '    value = value\n'
            'output("Value", value)'
        )


def test_runtime_to_one_sided_builder_rebind_does_not_leave_stale_runtime_value():
    """A branch-local builder owner cannot leave the incoming ordinary runtime binding visible."""
    with pytest.raises(CompileError, match="Unknown name: t"):
        _lower(
            't = 1.0\nx = 0.0\nflag = input_bool("Flag")\n'
            'if flag:\n    t = geometry_builder()\n    x = x + 1.0\n'
            'else:\n    t = 2.0\n    x = x + 2.0\n'
            'output(t)'
        )
