"""Persistent package semantic body-state tests for Extension API v2."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from NodeForge.builtin_call_semantics import INPUT_DECLARATION_BUILTIN_NAMES, IR_CAPABLE_BUILTIN_NAMES
from NodeForge.call_resolution import CallableEnvironment
from NodeForge.compile_time import CompileTimeSnapshot
from NodeForge.compiler_identities import BindingId
from NodeForge.consteval import _preprocess_compile_time
from NodeForge.errors import CompileError
from NodeForge.extension_contracts import ExtensionTypeId, TypeSpec
from NodeForge.extension_semantics import ExtensionDependencySource, ExtensionSemanticPayload
from NodeForge.extension_values import ExtensionDependencySlot, ExtensionValue
from NodeForge.nf_types import NFType
from NodeForge.runtime_bindings import RuntimeBindingSymbol
from NodeForge.semantic_analysis import build_semantic_environment
from NodeForge.semantic_body import lower_basic_body
from NodeForge.semantic_ir import (
    IRBindLeaves, IRCall, IRFinalExpression, IRIf, IRLeafBinding, IRProgram, IRRepeat, IRValue,
)
from NodeForge.semantic_values import StructuralRuntimeLeaf
from NodeForge.tests.unit.test_extension_semantic_values import _registry

pytestmark = pytest.mark.unit


def _callables(by_name):
    return CallableEnvironment(
        callable_builtins=frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES),
        local_functions={},
        imported_functions={},
        extension_system_callables=by_name,
    )


def _lower(tmp_path: Path, source: str, *, bindings=None, preprocess=False):
    _session, registry, by_name = _registry(tmp_path / "owner")
    stmts = ast.parse(source, mode="exec").body
    kwargs = {}
    if preprocess:
        preprocessed = _preprocess_compile_time(stmts)
        stmts = list(preprocessed.statements)
        initial_compile_time = preprocessed.initial_compile_time
        kwargs["compile_time_effects_before"] = preprocessed.effects_before
        kwargs["trailing_compile_time_effects"] = preprocessed.trailing_effects
    else:
        initial_compile_time = CompileTimeSnapshot({})
    result = lower_basic_body(
        stmts,
        initial_runtime_bindings=bindings or {},
        initial_compile_time=initial_compile_time,
        legacy_binding_names=frozenset(),
        reserved_name_labels={},
        callable_environment=_callables(by_name),
        owner_scope="scope",
        extension_registry=registry,
        **kwargs,
    )
    return result


def _binding(name: str, local_id: int, typ: NFType):
    return name, RuntimeBindingSymbol(BindingId("scope", local_id), typ)


def test_extension_binding_environment_requires_bindingid_only_dependencies():
    """The extension-binding ownership boundary accepts persistent payloads and rejects transient ones."""
    type_id = ExtensionTypeId(("system", "test"), "Part")
    spec = TypeSpec("RECORD", record_type=type_id)
    persistent_source = ExtensionDependencySource(BindingId("scope", 3), NFType.FLOAT)
    payload = ExtensionSemanticPayload(
        spec,
        ExtensionValue(type_id, (ExtensionDependencySlot(0, NFType.FLOAT),)),
        (persistent_source,),
    )
    environment = build_semantic_environment(
        runtime_bindings={},
        legacy_binding_names=frozenset(),
        compile_time=CompileTimeSnapshot({}),
        reserved_name_labels={},
        callable_environment=_callables({}),
        extension_bindings={"part": payload},
    )
    assert environment.extension_bindings["part"] is payload

    transient = ExtensionDependencySource(ast.Name(id="x", ctx=ast.Load()), NFType.FLOAT)
    transient_payload = ExtensionSemanticPayload(
        spec,
        ExtensionValue(type_id, (ExtensionDependencySlot(0, NFType.FLOAT),)),
        (transient,),
    )
    with pytest.raises(ValueError, match="BindingId-backed"):
        build_semantic_environment(
            runtime_bindings={},
            legacy_binding_names=frozenset(),
            compile_time=CompileTimeSnapshot({}),
            reserved_name_labels={},
            callable_environment=_callables({}),
            extension_bindings={"part": transient_payload},
        )


def test_assignment_snapshots_runtime_dependency_into_fresh_hidden_binding(tmp_path):
    """Persistent semantic capture snapshots the current runtime value instead of aliasing its source slot."""
    result = _lower(
        tmp_path,
        "part = make(x)\nconsume(part)\n",
        bindings=dict([_binding("x", 0, NFType.INT)]),
    )
    snapshot, consumer = result.body.statements
    assert isinstance(snapshot, IRBindLeaves)
    assert snapshot.value.operations[0].binding_id == BindingId("scope", 0)
    hidden = snapshot.bindings[0].destination
    assert hidden != BindingId("scope", 0)
    assert isinstance(consumer, IRFinalExpression)
    call = next(op for op in consumer.value.operations if isinstance(op, IRCall))
    assert call.arguments[0].value.typ is NFType.INT
    binding_ops = [op for op in consumer.value.operations if op.__class__.__name__ == "IRBinding"]
    assert [op.binding_id for op in binding_ops] == [hidden]


def test_static_semantic_assignment_needs_no_runtime_snapshot(tmp_path):
    """A semantic value with no live RuntimeRef persists without executable binding IR."""
    result = _lower(tmp_path, "part = make_static(1.0)\nconsume(part)\n")
    assert len(result.body.statements) == 1
    assert isinstance(result.body.statements[0], IRFinalExpression)


def test_runtime_name_rebind_does_not_change_persisted_capture(tmp_path):
    """Rebinding the source name after capture cannot retarget an existing semantic value."""
    result = _lower(
        tmp_path,
        "part = make(x)\nx = 7\nconsume(part)\n",
        bindings=dict([_binding("x", 0, NFType.INT)]),
    )
    snapshot = result.body.statements[0]
    hidden = snapshot.bindings[0].destination
    consumer = result.body.statements[-1]
    call = next(op for op in consumer.value.operations if isinstance(op, IRCall))
    binding_ops = [op for op in consumer.value.operations if op.__class__.__name__ == "IRBinding"]
    assert [op.binding_id for op in binding_ops] == [hidden]
    assert hidden != BindingId("scope", 0)
    assert call.arguments[0].value.typ is NFType.INT


def test_compile_time_rebind_removes_old_extension_ownership(tmp_path):
    """A later source-level CT bind cannot leave an older semantic value authoritative."""
    with pytest.raises(CompileError, match="expects a package-defined semantic record"):
        _lower(
            tmp_path,
            "part = make(x)\npart = [1, 2]\nconsume(part)\n",
            bindings=dict([_binding("x", 0, NFType.INT)]),
            preprocess=True,
        )


def test_compile_time_rebind_drops_ownership_without_retroactive_snapshot_dce(tmp_path):
    """CT overwrite leaves earlier emitted snapshot IR physically intact but unreachable from the rebound source name."""
    result = _lower(
        tmp_path,
        "part = make(x)\npart = [1, 2]\ny = 1\n",
        bindings=dict([_binding("x", 0, NFType.INT)]),
        preprocess=True,
    )
    snapshots = [statement for statement in result.body.statements if isinstance(statement, IRBindLeaves)]
    assert len(snapshots) == 1
    assert result.final_compile_time.values["part"] == [1, 2]
    # The overwrite itself allocates no new persistent snapshot; Stage 34 does not perform retroactive IR DCE.
    assert len(snapshots[0].bindings) == 1


def test_runtime_if_equal_semantic_state_joins_only_with_ordinary_runtime_merge(tmp_path):
    """Equal package semantic exits survive only after the normal runtime-if admissibility gate succeeds."""
    result = _lower(
        tmp_path,
        "if cond:\n    y = 1.0\n    part = make_static(1.0)\nelse:\n    y = 2.0\n    part = make_static(1.0)\nconsume(part)\n",
        bindings=dict([_binding("cond", 0, NFType.BOOL)]),
    )
    assert isinstance(result.body.statements[0], IRIf)
    assert isinstance(result.body.statements[-1], IRFinalExpression)


def test_runtime_if_extension_only_branches_keep_existing_admissibility_error(tmp_path):
    """Stage 34 does not create zero-runtime-merge IRIf for semantic-only branch state."""
    with pytest.raises(CompileError, match="assign at least one common variable"):
        _lower(
            tmp_path,
            "if cond:\n    part = make_static(1.0)\nelse:\n    part = make_static(1.0)\nconsume(part)\n",
            bindings=dict([_binding("cond", 0, NFType.BOOL)]),
        )


def test_runtime_if_discards_dead_dependency_identity_before_extension_join(tmp_path):
    """Different discarded runtime arguments do not make equal semantic branch values diverge."""
    result = _lower(
        tmp_path,
        "if cond:\n    y = 1.0\n    part = ignore(x)\nelse:\n    y = 2.0\n    part = ignore(z)\nconsume(part)\n",
        bindings=dict([
            _binding("cond", 0, NFType.BOOL),
            _binding("x", 1, NFType.INT),
            _binding("z", 2, NFType.INT),
        ]),
    )
    assert isinstance(result.body.statements[0], IRIf)
    assert isinstance(result.body.statements[-1], IRFinalExpression)


def test_runtime_if_equal_persistent_dependencies_join_independent_of_import_order(tmp_path):
    """Canonical first-use ordering makes equal branch values join despite opposite dependency acquisition order."""
    result = _lower(
        tmp_path,
        "left = make(x)\nright = make(z)\nif cond:\n    y = 1.0\n    pair = pair_ab(left, right)\nelse:\n    y = 2.0\n    pair = pair_ba(right, left)\nconsume_pair(pair)\n",
        bindings=dict([
            _binding("cond", 0, NFType.BOOL),
            _binding("x", 1, NFType.INT),
            _binding("z", 2, NFType.INT),
        ]),
    )
    snapshots = [statement for statement in result.body.statements if isinstance(statement, IRBindLeaves)]
    assert len(snapshots) == 2
    assert isinstance(result.body.statements[2], IRIf)
    assert isinstance(result.body.statements[-1], IRFinalExpression)



def test_runtime_if_one_sided_new_semantic_name_does_not_escape(tmp_path):
    """A branch-local semantic name is dropped at an otherwise-valid runtime join."""
    with pytest.raises(CompileError, match="Unknown name: part"):
        _lower(
            tmp_path,
            "if cond:\n    y = 1.0\n    part = make_static(1.0)\nelse:\n    y = 2.0\nconsume(part)\n",
            bindings=dict([_binding("cond", 0, NFType.BOOL)]),
        )


@pytest.mark.parametrize(
    "true_value,false_value",
    [("1.0", "1.0"), ("2.0", "2.0")],
)
def test_runtime_if_equal_semantic_join_is_branch_order_independent(tmp_path, true_value, false_value):
    """Equivalent persistent semantic exits compile identically regardless of branch source order."""
    source_a = (
        f"if cond:\n    y = 1.0\n    part = make_static({true_value})\n"
        f"else:\n    y = 2.0\n    part = make_static({false_value})\nconsume(part)\n"
    )
    source_b = (
        f"if cond:\n    y = 2.0\n    part = make_static({false_value})\n"
        f"else:\n    y = 1.0\n    part = make_static({true_value})\nconsume(part)\n"
    )
    first = _lower(tmp_path / "a", source_a, bindings=dict([_binding("cond", 0, NFType.BOOL)]))
    second = _lower(tmp_path / "b", source_b, bindings=dict([_binding("cond", 0, NFType.BOOL)]))
    assert isinstance(first.body.statements[0], IRIf)
    assert isinstance(second.body.statements[0], IRIf)
    assert isinstance(first.body.statements[-1], IRFinalExpression)
    assert isinstance(second.body.statements[-1], IRFinalExpression)


def test_caller_side_star_rejects_structural_array_binding(tmp_path):
    """NodeForge structural arrays are not silently reinterpreted as package semantic LIST values."""
    with pytest.raises(CompileError, match="package semantic LIST"):
        _lower(
            tmp_path,
            "items = []\nitems.append(x)\ny = consume_star(*items)\n",
            bindings=dict([_binding("x", 0, NFType.INT)]),
        )

def test_runtime_if_preserves_unchanged_incoming_extension_binding(tmp_path):
    """An incoming package semantic owner survives when neither runtime branch changes its category or value."""
    result = _lower(
        tmp_path,
        "part = make(x)\nif cond:\n    y = 1\nelse:\n    y = 2\nconsume(part)\n",
        bindings=dict([
            _binding("cond", 0, NFType.BOOL),
            _binding("x", 1, NFType.INT),
        ]),
    )
    assert isinstance(result.body.statements[1], IRIf)
    assert isinstance(result.body.statements[-1], IRFinalExpression)


def test_runtime_if_rejects_incoming_extension_binding_category_change_on_one_exit(tmp_path):
    """Changing an incoming package semantic owner on only one runtime exit remains invalid."""
    with pytest.raises(
        CompileError,
        match="runtime if branches disagree on package semantic ownership for 'part'",
    ):
        _lower(
            tmp_path,
            "part = make(x)\nif cond:\n    y = 1\n    part = x + 1\nelse:\n    y = 2\nconsume(part)\n",
            bindings=dict([
                _binding("cond", 0, NFType.BOOL),
                _binding("x", 1, NFType.INT),
            ]),
        )


def test_runtime_if_rejects_incoming_extension_binding_replaced_by_runtime_on_both_exits(tmp_path):
    """An incoming package semantic owner cannot disappear through a two-sided runtime category transition."""
    sources = (
        "part = make(x)\nif cond:\n    part = x + 1\nelse:\n    part = x + 2\n",
        "part = make(x)\nif cond:\n    part = x + 2\nelse:\n    part = x + 1\n",
    )
    messages = []
    for index, source in enumerate(sources):
        with pytest.raises(CompileError) as exc_info:
            _lower(
                tmp_path / str(index),
                source,
                bindings=dict([
                    _binding("cond", 0, NFType.BOOL),
                    _binding("x", 1, NFType.INT),
                ]),
            )
        messages.append(str(exc_info.value))

    assert messages == [
        "runtime if branches disagree on package semantic ownership for 'part'",
        "runtime if branches disagree on package semantic ownership for 'part'",
    ]


def test_runtime_if_divergent_semantic_state_is_rejected(tmp_path):
    """Different canonical semantic values cannot be selected by an implicit whole-record Switch."""
    with pytest.raises(CompileError, match="different package semantic values"):
        _lower(
            tmp_path,
            "if cond:\n    y = 1.0\n    part = make_static(1.0)\nelse:\n    y = 2.0\n    part = make_static(2.0)\nconsume(part)\n",
            bindings=dict([_binding("cond", 0, NFType.BOOL)]),
        )


def test_repeat_rejects_mutating_existing_extension_binding(tmp_path):
    """An outer extension binding is never classified as Repeat carried state."""
    with pytest.raises(CompileError, match="cannot carry or rebind package semantic value"):
        _lower(
            tmp_path,
            "part = make(x)\nfor i in repeat_range(n):\n    part = make(x)\nconsume(part)\n",
            bindings=dict([
                _binding("x", 0, NFType.INT),
                _binding("n", 1, NFType.INT),
            ]),
        )


def test_repeat_rejects_runtime_carried_state_becoming_extension_semantic(tmp_path):
    """A carried runtime slot cannot change ownership category to package semantic state."""
    with pytest.raises(CompileError, match="cannot change from runtime state to package semantic value"):
        _lower(
            tmp_path,
            "for i in repeat_range(n):\n    x = make(x)\n",
            bindings=dict([
                _binding("x", 0, NFType.INT),
                _binding("n", 1, NFType.INT),
            ]),
        )



def test_literal_semantic_list_persists_and_star_expands_with_existing_hidden_dependencies(tmp_path):
    """A literal list of sibling semantic records persists through generic Stage-34 state and star expansion."""
    result = _lower(
        tmp_path,
        "a = make_alpha(x)\nb = make_beta(2.0)\nparts = [a, b]\nconsume_star(*parts)\n",
        bindings=dict([_binding("x", 0, NFType.INT)]),
    )
    snapshots = [statement for statement in result.body.statements if isinstance(statement, IRBindLeaves)]
    assert len(snapshots) == 1
    hidden = snapshots[0].bindings[0].destination
    consumer = result.body.statements[-1]
    assert isinstance(consumer, IRFinalExpression)
    call = next(op for op in consumer.value.operations if isinstance(op, IRCall))
    assert [op.binding_id for op in consumer.value.operations if op.__class__.__name__ == "IRBinding"] == [hidden]
    assert len(call.arguments) == 1
    parts = call.extension_state.storage[0]
    assert [item.type_id.name for item in parts] == ["AlphaPart", "BetaPart"]
    assert parts[0].storage[0].operand_index == 0
    assert parts[1].storage[0] == 2.0

def test_persistent_semantic_list_star_uses_hidden_snapshots_in_element_order(tmp_path):
    """Stored semantic LIST expands into compact child payloads backed by its hidden snapshots."""
    result = _lower(
        tmp_path,
        "parts = make_parts(make(x), make(y))\nconsume_star(*parts)\n",
        bindings=dict([
            _binding("x", 0, NFType.INT),
            _binding("y", 1, NFType.INT),
        ]),
    )
    snapshot, consumer = result.body.statements
    assert isinstance(snapshot, IRBindLeaves)
    hidden = tuple(binding.destination for binding in snapshot.bindings)
    assert len(hidden) == 2
    assert set(hidden).isdisjoint({BindingId("scope", 0), BindingId("scope", 1)})
    assert isinstance(consumer, IRFinalExpression)
    call = next(op for op in consumer.value.operations if isinstance(op, IRCall))
    binding_ops = [op for op in consumer.value.operations if op.__class__.__name__ == "IRBinding"]
    assert [op.binding_id for op in binding_ops] == list(hidden)
    assert len(call.arguments) == 2
    parts = call.extension_state.storage[0]
    assert [item.storage[0].operand_index for item in parts] == [0, 1]


def test_persistent_semantic_list_does_not_gain_structural_array_append(tmp_path):
    """Package semantic LIST remains extension state rather than mutable StructuralArrayState."""
    with pytest.raises(CompileError):
        _lower(
            tmp_path,
            "parts = make_parts(make(x), make(y))\nparts.append(make(x))\n",
            bindings=dict([
                _binding("x", 0, NFType.INT),
                _binding("y", 1, NFType.INT),
            ]),
        )


def test_reusing_persistent_dependency_does_not_allocate_second_snapshot(tmp_path):
    """Semantic composition over an already persistent dependency reuses its hidden BindingId."""
    result = _lower(
        tmp_path,
        "part = make(x)\nparts = make_parts(part, part)\nconsume_star(*parts)\n",
        bindings=dict([_binding("x", 0, NFType.INT)]),
    )
    snapshots = [statement for statement in result.body.statements if isinstance(statement, IRBindLeaves)]
    assert len(snapshots) == 1
    assert len(snapshots[0].bindings) == 1
    hidden = snapshots[0].bindings[0].destination
    consumer = result.body.statements[-1]
    binding_ops = [op for op in consumer.value.operations if op.__class__.__name__ == "IRBinding"]
    assert [op.binding_id for op in binding_ops] == [hidden]


def test_compile_time_for_nested_rebind_removes_old_extension_ownership(tmp_path):
    """A CT bind replayed inside CompileTimeForEffect invalidates an older extension binding."""
    with pytest.raises(CompileError, match="expects a package-defined semantic record"):
        _lower(
            tmp_path,
            "part = make(x)\nfor i in [0]:\n    part = [1, 2]\nconsume(part)\n",
            bindings=dict([_binding("x", 0, NFType.INT)]),
            preprocess=True,
        )


def test_compile_time_for_target_shadows_and_restores_extension_binding(tmp_path):
    """A compile-time loop target may shadow a semantic name without destroying its outer binding."""
    result = _lower(
        tmp_path,
        "part = make(x)\nfor part in [1]:\n    tmp = part\nconsume(part)\n",
        bindings=dict([_binding("x", 0, NFType.INT)]),
        preprocess=True,
    )
    assert isinstance(result.body.statements[0], IRBindLeaves)
    assert isinstance(result.body.statements[-1], IRFinalExpression)


def test_compile_time_append_does_not_invalidate_unrelated_extension_binding(tmp_path):
    """Compile-time container mutation is not treated as a source-level semantic rebind."""
    result = _lower(
        tmp_path,
        "part = make(x)\nitems = []\nitems.append(1)\nconsume(part)\n",
        bindings=dict([_binding("x", 0, NFType.INT)]),
        preprocess=True,
    )
    assert isinstance(result.body.statements[0], IRBindLeaves)
    assert isinstance(result.body.statements[-1], IRFinalExpression)


def test_empty_and_nested_empty_semantic_lists_persist_without_runtime_snapshot(tmp_path):
    """Semantic LIST TypeSpec survives body storage even when cardinality cannot reveal its element type."""
    empty = _lower(tmp_path / "empty", "parts = make_empty_parts()\nconsume_many(parts)\n")
    nested = _lower(tmp_path / "nested", "parts = make_nested_empty()\nconsume_nested(parts)\n")
    assert len(empty.body.statements) == 1
    assert isinstance(empty.body.statements[0], IRFinalExpression)
    assert len(nested.body.statements) == 1
    assert isinstance(nested.body.statements[0], IRFinalExpression)


def test_repeat_can_read_unchanged_outer_extension_binding(tmp_path):
    """Repeat bodies may consume outer semantic state while carrying only ordinary runtime state."""
    result = _lower(
        tmp_path,
        "part = make(x)\nfor i in repeat_range(n):\n    y = y + 1.0\n    z = consume(part)\nconsume(part)\n",
        bindings=dict([
            _binding("x", 0, NFType.INT),
            _binding("n", 1, NFType.INT),
            _binding("y", 2, NFType.FLOAT),
        ]),
    )
    assert isinstance(result.body.statements[0], IRBindLeaves)
    assert any(isinstance(statement, IRRepeat) for statement in result.body.statements)
    assert isinstance(result.body.statements[-1], IRFinalExpression)


def test_repeat_loop_local_extension_value_does_not_escape(tmp_path):
    """An iteration-local semantic temporary is usable inside Repeat but absent after the zone."""
    source = (
        "for i in repeat_range(n):\n"
        "    y = y + 1.0\n"
        "    part = make(x)\n"
        "    z = consume(part)\n"
        "consume(part)\n"
    )
    with pytest.raises(CompileError, match="Unknown name: part"):
        _lower(
            tmp_path,
            source,
            bindings=dict([
                _binding("x", 0, NFType.INT),
                _binding("n", 1, NFType.INT),
                _binding("y", 2, NFType.FLOAT),
            ]),
        )


def test_persistence_uses_exact_carrier_result_leaves(tmp_path):
    """Hidden snapshots bind exact carrier-result leaves and do not broaden IRBindLeaves semantics."""
    from NodeForge.semantic_ir import IRTuple

    result = _lower(
        tmp_path,
        "parts = make_parts(make(x), make(y))\nconsume_star(*parts)\n",
        bindings=dict([
            _binding("x", 0, NFType.INT),
            _binding("y", 1, NFType.INT),
        ]),
    )
    snapshot = result.body.statements[0]
    assert isinstance(snapshot, IRBindLeaves)
    assert isinstance(snapshot.value.result, IRTuple)
    assert tuple(binding.source for binding in snapshot.bindings) == snapshot.value.result.items



def test_persistence_cannot_bind_nonresult_intermediate_leaf():
    """Stage-34 snapshotting remains subject to the existing exact IRBindLeaves result-leaf boundary."""
    from NodeForge.semantic_ir import IRLeafBinding, IRLiteral, IRProgram, IRValue

    intermediate = IRValue(0, NFType.FLOAT)
    result = IRValue(1, NFType.FLOAT)
    program = IRProgram(
        (
            IRLiteral(intermediate, 0, 1.0),
            IRLiteral(result, 0, 2.0),
        ),
        result,
    )
    with pytest.raises(ValueError, match="exact runtime leaf"):
        IRBindLeaves(
            program,
            (IRLeafBinding(intermediate, BindingId("scope", 9), NFType.FLOAT),),
        )

def test_persistent_wrapper_keeps_stage33_slot_storage_unchanged():
    """Persistence changes dependency identity only; detached slot storage remains the Stage-33 representation."""
    type_id = ExtensionTypeId(("system", "test"), "Part")
    spec = TypeSpec("RECORD", record_type=type_id)
    value = ExtensionValue(type_id, (ExtensionDependencySlot(0, NFType.FLOAT),))
    transient = ExtensionSemanticPayload(
        spec,
        value,
        (ExtensionDependencySource(ast.Name(id="x", ctx=ast.Load()), NFType.FLOAT),),
    )
    persistent = ExtensionSemanticPayload(
        spec,
        value,
        (ExtensionDependencySource(BindingId("scope", 7), NFType.FLOAT),),
    )
    assert persistent.value is transient.value
    assert persistent.value == transient.value
    assert not isinstance(persistent.value.storage[0], StructuralRuntimeLeaf)


def test_semantic_environment_rejects_duplicate_extension_and_runtime_ownership():
    """The expression boundary rejects a source name active in two body ownership categories."""
    type_id = ExtensionTypeId(("system", "test"), "Part")
    payload = ExtensionSemanticPayload(
        TypeSpec("RECORD", record_type=type_id),
        ExtensionValue(type_id, (1.0,)),
        (),
    )
    callables = CallableEnvironment(
        callable_builtins=frozenset(),
        local_functions={},
        imported_functions={},
        extension_system_callables={},
    )
    with pytest.raises(CompileError, match="multiple semantic binding domains"):
        build_semantic_environment(
            runtime_bindings={"part": RuntimeBindingSymbol(BindingId("scope", 0), NFType.FLOAT)},
            legacy_binding_names=frozenset(),
            compile_time=CompileTimeSnapshot({}),
            reserved_name_labels={},
            callable_environment=callables,
            extension_bindings={"part": payload},
        )


def test_runtime_extension_runtime_rebind_transition_keeps_one_active_owner(tmp_path):
    """A name may move runtime -> extension -> runtime without stale semantic ownership affecting resolution."""
    result = _lower(
        tmp_path,
        'x = make(x)\nx = input_int("Y", default=2)\nx = x + 1\n',
        bindings=dict([_binding("x", 0, NFType.INT)]),
    )
    assert any(statement.__class__.__name__ == "IRInputDeclaration" for statement in result.body.statements)
    assert result.body.statements[-1].__class__.__name__ == "IRAssign"
    assert result.body.statements[-1].source_name == "x"


def test_compile_time_loop_target_hides_extension_binding_inside_loop(tmp_path):
    """The lexical CT loop target shadows the outer extension binding while the loop body is replayed."""
    with pytest.raises(CompileError, match="expects a package-defined semantic record"):
        _lower(
            tmp_path,
            "part = make(x)\nfor part in [1]:\n    y = consume(part)\n",
            bindings=dict([_binding("x", 0, NFType.INT)]),
            preprocess=True,
        )


def test_semantic_list_is_not_a_compile_time_structural_iterable(tmp_path):
    """Package semantic LIST does not acquire NodeForge structural-array unrolling semantics."""
    with pytest.raises(CompileError):
        _lower(
            tmp_path,
            "parts = make_parts(make(x), make(y))\nfor part in parts:\n    z = consume(part)\n",
            bindings=dict([_binding("x", 0, NFType.INT), _binding("y", 1, NFType.INT)]),
        )


def test_repeated_persistent_reads_allocate_no_additional_snapshots(tmp_path):
    """Reading one persisted semantic value repeatedly reuses its hidden BindingId without new persistence work."""
    result = _lower(
        tmp_path,
        "part = make(x)\na = consume(part)\nb = consume(part)\n",
        bindings=dict([_binding("x", 0, NFType.INT)]),
    )
    snapshots = [statement for statement in result.body.statements if isinstance(statement, IRBindLeaves)]
    assert len(snapshots) == 1
    hidden = snapshots[0].bindings[0].destination
    consumer_bindings = []
    for statement in result.body.statements[1:]:
        program = getattr(statement, "value", None)
        if program is None:
            continue
        consumer_bindings.extend(
            op.binding_id for op in program.operations if op.__class__.__name__ == "IRBinding"
        )
    assert consumer_bindings.count(hidden) >= 2


def test_transient_semantic_composition_creates_no_body_persistence_binding(tmp_path):
    """Immediate Stage-33 composition remains free of hidden body snapshots."""
    result = _lower(
        tmp_path,
        "y = consume(make(x))\n",
        bindings=dict([_binding("x", 0, NFType.INT)]),
    )
    assert not any(isinstance(statement, IRBindLeaves) for statement in result.body.statements)
    assert any(statement.__class__.__name__ == "IRAssign" for statement in result.body.statements)


def test_stage34_core_adds_no_extension_specific_ir_or_lsystem_branch():
    """Persistent semantic state stays generic and reuses ordinary body/Call IR mechanisms."""
    root = Path(__file__).resolve().parents[2]
    ir_source = (root / "semantic_ir.py").read_text(encoding="utf-8")
    assert "ExtensionRuntimeLeaf" not in ir_source
    assert "IRExtension" not in ir_source
    for name in ("semantic_body.py", "semantic_analysis.py", "extension_semantics.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "nodeforge.lsystem" not in source.lower()
        assert "lsystem" not in source.lower()



def test_object_runtime_dependency_uses_ordinary_hidden_snapshot(tmp_path):
    """Object RuntimeRef persistence uses the same hidden BindingId carrier without extension Object state."""
    result = _lower(
        tmp_path,
        "part = make_object(obj)\n",
        bindings=dict([_binding("obj", 0, NFType.OBJECT)]),
    )
    assert len(result.body.statements) == 1
    snapshot = result.body.statements[0]
    assert isinstance(snapshot, IRBindLeaves)
    assert snapshot.bindings[0].typ is NFType.OBJECT
    assert snapshot.bindings[0].destination != BindingId("scope", 0)
    source = snapshot.value.operations[0]
    assert source.__class__.__name__ == "IRBinding"
    assert source.binding_id == BindingId("scope", 0)
    assert source.result.typ is NFType.OBJECT


def test_persistent_storage_contains_no_package_python_instances(tmp_path):
    """Persistent compiler storage keeps detached ExtensionValue data rather than package dataclass/session objects."""
    _session, registry, by_name = _registry(tmp_path / "owner-storage")
    env = build_semantic_environment(
        runtime_bindings={"x": RuntimeBindingSymbol(BindingId("scope", 0), NFType.INT)},
        legacy_binding_names=frozenset(),
        compile_time=CompileTimeSnapshot({}),
        reserved_name_labels={},
        callable_environment=_callables(by_name),
        extension_registry=registry,
    )
    from NodeForge.semantic_analysis import analyze_expression
    from NodeForge.extension_semantics import compact_extension_semantic_payload

    root = ast.parse("make(x)", mode="eval").body
    analysis = analyze_expression(root, env)
    payload = analysis.facts[root].semantic_payload
    assert payload is not None
    normalized = compact_extension_semantic_payload(
        payload.type_spec,
        payload.value,
        tuple(
            ExtensionDependencySource(BindingId("scope", 10 + index), dependency.typ)
            for index, dependency in enumerate(payload.dependencies)
        ),
    )
    assert isinstance(normalized.value, ExtensionValue)
    assert normalized.value.__class__.__module__.startswith("NodeForge.")
    assert all(dependency.is_persistent for dependency in normalized.dependencies)
    assert not hasattr(normalized, "session")
    assert not hasattr(normalized, "registry")

def test_persistence_does_not_reinvoke_semantic_callback(tmp_path, monkeypatch):
    """Body persistence emits dependency IR from the original analysis rather than re-running semantic code."""
    from NodeForge.extension_registry import ExtensionRegistry

    original = ExtensionRegistry.invoke_semantic
    calls = []

    def counted(self, callable_id, *args, **kwargs):
        calls.append(callable_id.name)
        return original(self, callable_id, *args, **kwargs)

    monkeypatch.setattr(ExtensionRegistry, "invoke_semantic", counted)
    _lower(
        tmp_path,
        "part = make(x)\nconsume(part)\n",
        bindings=dict([_binding("x", 0, NFType.INT)]),
    )
    assert calls.count("make") == 1
    assert calls.count("consume") == 1



def test_irbindleaves_rejects_non_result_intermediate_leaf():
    """Persistence cannot broaden IRBindLeaves to bind arbitrary program-local intermediates."""
    intermediate = IRValue(0, NFType.FLOAT)
    result = IRValue(1, NFType.FLOAT)
    program = IRProgram((), result)
    binding = IRLeafBinding(intermediate, BindingId("scope", 99), NFType.FLOAT)
    with pytest.raises(ValueError, match="exact runtime leaf"):
        IRBindLeaves(program, (binding,))


def test_persistent_payload_storage_contains_only_detached_compiler_values():
    """Persistent extension storage contains detached compiler data, never package/session objects."""
    type_id = ExtensionTypeId(("system", "test"), "Part")
    payload = ExtensionSemanticPayload(
        TypeSpec("RECORD", record_type=type_id),
        ExtensionValue(type_id, (ExtensionDependencySlot(0, NFType.FLOAT), "tag", (1, 2))),
        (ExtensionDependencySource(BindingId("scope", 4), NFType.FLOAT),),
    )
    def assert_detached(value):
        if isinstance(value, ExtensionDependencySlot):
            return
        if isinstance(value, ExtensionValue):
            assert_detached(value.storage)
            return
        if isinstance(value, tuple):
            for item in value:
                assert_detached(item)
            return
        assert type(value) in {bool, int, float, str, type(None)}

    assert_detached(payload.value)
    assert all(isinstance(dep.source, BindingId) for dep in payload.dependencies)


def test_semantic_star_rejects_runtime_value_and_semantic_record(tmp_path):
    """Caller-side star accepts only package semantic LIST payloads."""
    bindings = dict([_binding("x", 0, NFType.INT)])
    with pytest.raises(CompileError, match="caller-side \\* requires a package semantic LIST value"):
        _lower(tmp_path / "runtime", "consume_star(*x)\n", bindings=bindings)
    with pytest.raises(CompileError, match="caller-side \\* requires a package semantic LIST value"):
        _lower(tmp_path / "record", "part = make(x)\nconsume_star(*part)\n", bindings=bindings)


def test_semantic_star_rejects_structural_array(tmp_path):
    """NodeForge structural arrays are not implicitly package semantic LIST values."""
    with pytest.raises(CompileError, match="caller-side \\* requires a package semantic LIST value"):
        _lower(
            tmp_path,
            "items = []\nitems.append(x)\nconsume_star(*items)\n",
            bindings=dict([_binding("x", 0, NFType.INT)]),
        )
