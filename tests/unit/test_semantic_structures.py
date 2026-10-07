"""Compiler-owned structural, Object, and Bundle semantic contracts."""

import ast
from pathlib import Path
from types import MappingProxyType

import pytest

from NodeForge.compiler_identities import BindingId
from NodeForge.nf_types import NFType
from NodeForge.semantic.ir import (
    IRArray,
    IRBindLeaves,
    IRBinding,
    IRDiscardExpression,
    IRLeafBinding,
    IRNamedOutputs,
    IRProgram,
    IRTuple,
    IRValue,
)
from NodeForge.semantic.values import (
    ObjectInfoState,
    ObjectSemanticId,
    ObjectSemanticSnapshot,
    ArrayResultShape,
    RuntimeResultShape,
    StructuralArrayId,
    StructuralArrayRef,
    StructuralArraySnapshot,
    StructuralArrayState,
    StructuralRuntimeLeaf,
    StructuralBindingKind,
    StructuralBindingSymbol,
    StructuralLeafBinding,
)

pytestmark = pytest.mark.unit


def test_runtime_result_shape_enforces_bidirectional_object_identity():
    object_id = ObjectSemanticId(0)
    assert RuntimeResultShape(NFType.OBJECT, object_id).object_id == object_id
    with pytest.raises(ValueError, match="Object result requires object_id"):
        RuntimeResultShape(NFType.OBJECT)
    with pytest.raises(ValueError, match="object_id is only valid for Object"):
        RuntimeResultShape(NFType.FLOAT, object_id)


def test_structural_result_shape_recovers_object_identity_only_from_binding_table():
    object_binding = BindingId("owner", 0)
    float_binding = BindingId("owner", 1)
    object_id = ObjectSemanticId(3)
    symbol = StructuralBindingSymbol(
        StructuralBindingKind.TUPLE,
        (
            StructuralLeafBinding(("index", 0), object_binding, NFType.OBJECT),
            StructuralLeafBinding(("index", 1), float_binding, NFType.FLOAT),
        ),
    )
    shape = symbol.result_shape({object_binding: object_id})
    assert shape.items[0].object_id == object_id
    assert shape.items[1].object_id is None
    with pytest.raises(Exception, match="Object structural leaf has no ObjectSemanticId"):
        symbol.result_shape({})
    with pytest.raises(Exception, match="non-Object structural leaf has ObjectSemanticId"):
        symbol.result_shape({object_binding: object_id, float_binding: ObjectSemanticId(4)})



def test_structural_leaves_are_type_generic_for_bundle_and_independent_object_provenance():
    first_object = BindingId("owner", 0)
    bundle = BindingId("owner", 1)
    second_object = BindingId("owner", 2)
    first_id = ObjectSemanticId(5)
    second_id = ObjectSemanticId(6)
    symbol = StructuralBindingSymbol(
        StructuralBindingKind.NAMED_OUTPUTS,
        (
            StructuralLeafBinding(("name", "First"), first_object, NFType.OBJECT),
            StructuralLeafBinding(("name", "Bundle"), bundle, NFType.BUNDLE),
            StructuralLeafBinding(("name", "Second"), second_object, NFType.OBJECT),
        ),
    )
    shape = symbol.result_shape({first_object: first_id, second_object: second_id})
    assert [name for name, _ in shape.items] == ["First", "Bundle", "Second"]
    assert shape.items[0][1].object_id == first_id
    assert shape.items[1][1].typ is NFType.BUNDLE and shape.items[1][1].object_id is None
    assert shape.items[2][1].object_id == second_id

def test_object_snapshot_allows_unreferenced_states_but_never_missing_referenced_state():
    binding = BindingId("owner", 0)
    used = ObjectSemanticId(1)
    stale = ObjectSemanticId(2)
    snapshot = ObjectSemanticSnapshot(
        {binding: used},
        {used: ObjectInfoState(), stale: ObjectInfoState(as_instance=False)},
        3,
    )
    assert stale in snapshot.states
    with pytest.raises(ValueError, match="missing ObjectInfoState"):
        ObjectSemanticSnapshot({binding: used}, {stale: ObjectInfoState()}, 3)


def _tuple_program():
    first = IRValue(0, NFType.FLOAT)
    second = IRValue(1, NFType.VECTOR)
    return IRProgram(
        (
            IRBinding(first, 0, BindingId("source", 0)),
            IRBinding(second, 0, BindingId("source", 1)),
        ),
        IRTuple((first, second)),
    )


def test_ir_bind_leaves_requires_exact_typed_unique_result_leaves():
    program = _tuple_program()
    first, second = program.result.items
    valid = IRBindLeaves(
        program,
        (
            IRLeafBinding(first, BindingId("dest", 0), NFType.FLOAT),
            IRLeafBinding(second, BindingId("dest", 1), NFType.VECTOR),
        ),
    )
    assert len(valid.bindings) == 2
    with pytest.raises(TypeError, match="destination must be a BindingId"):
        IRLeafBinding(first, object(), NFType.FLOAT)
    with pytest.raises(TypeError, match="type must equal source type"):
        IRLeafBinding(first, BindingId("dest", 2), NFType.INT)
    alien = IRValue(99, NFType.FLOAT)
    with pytest.raises(ValueError, match="exact runtime leaf"):
        IRBindLeaves(program, (IRLeafBinding(alien, BindingId("dest", 2), NFType.FLOAT),))
    with pytest.raises(ValueError, match="destinations must be unique"):
        IRBindLeaves(
            program,
            (
                IRLeafBinding(first, BindingId("dest", 0), NFType.FLOAT),
                IRLeafBinding(second, BindingId("dest", 0), NFType.VECTOR),
            ),
        )
    with pytest.raises(ValueError, match="source leaves must be unique"):
        IRBindLeaves(
            program,
            (
                IRLeafBinding(first, BindingId("dest", 0), NFType.FLOAT),
                IRLeafBinding(first, BindingId("dest", 1), NFType.FLOAT),
            ),
        )


def test_ir_bind_leaves_recursively_accepts_irarray_results_in_source_order():
    """Array leaves participate in the existing exact leaf-binding contract recursively."""
    first = IRValue(0, NFType.FLOAT)
    second = IRValue(1, NFType.VECTOR)
    third = IRValue(2, NFType.INT)
    program = IRProgram((), IRArray((first, IRArray((second, third)))))
    bindings = (
        IRLeafBinding(first, BindingId("dest", 0), NFType.FLOAT),
        IRLeafBinding(second, BindingId("dest", 1), NFType.VECTOR),
        IRLeafBinding(third, BindingId("dest", 2), NFType.INT),
    )
    record = IRBindLeaves(program, bindings)
    assert record.bindings == bindings

    with pytest.raises(ValueError, match="exact runtime leaf"):
        IRBindLeaves(
            program,
            (IRLeafBinding(IRValue(9, NFType.FLOAT), BindingId("dest", 3), NFType.FLOAT),),
        )


def test_ir_discard_expression_accepts_structural_program_and_is_detached():
    discard = IRDiscardExpression(_tuple_program())
    assert isinstance(discard.value.result, IRTuple)
    assert not any(isinstance(value, ast.AST) for value in discard.value.operations)


def test_semantic_modules_are_blender_independent_and_no_aggregate_nftypes_exist():
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "semantic/values.py",
        "semantic/ir.py",
        "semantic/analysis.py",
        "semantic/lowering.py",
        "semantic/body.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "import bpy" not in source
        assert "from bpy" not in source
    assert not hasattr(NFType, "TUPLE")
    assert not hasattr(NFType, "NODE_RESULT")
    assert not hasattr(NFType, "NAMED_OUTPUTS")


def test_ir_structural_records_do_not_embed_legacy_backend_containers_or_mutable_fields():
    import NodeForge.blender.values as values

    assert all(
        field.name not in {"tuple_value", "node_result"}
        for field in __import__("dataclasses").fields(IRBindLeaves)
    )
    assert not hasattr(values, "NodeResult")
    assert not hasattr(values, "TupleValue")
    assert IRNamedOutputs is not IRTuple


def test_structural_array_records_validate_identity_immutability_and_detached_snapshot():
    """Structural-array records are canonical, immutable, and detached from source mappings."""
    with pytest.raises(ValueError, match="non-negative"):
        StructuralArrayId(-1)
    with pytest.raises(TypeError, match="BindingId"):
        StructuralRuntimeLeaf(object(), NFType.FLOAT)
    with pytest.raises(TypeError, match="NFType"):
        StructuralRuntimeLeaf(BindingId("owner", 0), "FLOAT")

    array_id = StructuralArrayId(0)
    state = StructuralArrayState((StructuralRuntimeLeaf(BindingId("owner", 0), NFType.FLOAT),))
    source_bindings = {"items": array_id}
    source_states = {array_id: state}
    snapshot = StructuralArraySnapshot(source_bindings, source_states)
    source_bindings["other"] = array_id
    source_states[array_id] = StructuralArrayState(())
    assert set(snapshot.bindings) == {"items"}
    assert snapshot.states[array_id] is state
    with pytest.raises(TypeError):
        snapshot.bindings["x"] = array_id
    with pytest.raises(TypeError):
        snapshot.states[array_id] = StructuralArrayState(())


def test_structural_array_shape_reconstruction_preserves_nested_fixed_and_object_provenance():
    """Recursive heap reconstruction reuses fixed structures and Object identity by BindingId."""
    float_binding = BindingId("owner", 0)
    object_binding = BindingId("owner", 1)
    tuple_binding = BindingId("owner", 2)
    object_id = ObjectSemanticId(7)
    inner_id = StructuralArrayId(0)
    outer_id = StructuralArrayId(1)
    fixed = StructuralBindingSymbol(
        StructuralBindingKind.TUPLE,
        (StructuralLeafBinding(("index", 0), tuple_binding, NFType.BUNDLE),),
    )
    states = {
        inner_id: StructuralArrayState((StructuralRuntimeLeaf(object_binding, NFType.OBJECT),)),
        outer_id: StructuralArrayState((
            StructuralRuntimeLeaf(float_binding, NFType.FLOAT),
            StructuralArrayRef(inner_id),
            fixed,
        )),
    }
    snapshot = StructuralArraySnapshot({"outer": outer_id}, states)
    shape = snapshot.result_shape(outer_id, {object_binding: object_id})
    assert isinstance(shape, ArrayResultShape)
    assert shape.items[0] == RuntimeResultShape(NFType.FLOAT)
    assert shape.items[1].items[0] == RuntimeResultShape(NFType.OBJECT, object_id)
    assert shape.items[2].items[0] == RuntimeResultShape(NFType.BUNDLE)


def test_structural_array_shape_reconstruction_rejects_direct_and_indirect_cycles():
    """Recursive structural-array graphs fail with the controlled cycle diagnostic."""
    first = StructuralArrayId(0)
    second = StructuralArrayId(1)
    direct = StructuralArraySnapshot(
        {"a": first},
        {first: StructuralArrayState((StructuralArrayRef(first),))},
    )
    with pytest.raises(Exception, match="recursive structural arrays are not supported"):
        direct.result_shape(first, {})

    indirect = StructuralArraySnapshot(
        {"a": first},
        {
            first: StructuralArrayState((StructuralArrayRef(second),)),
            second: StructuralArrayState((StructuralArrayRef(first),)),
        },
    )
    with pytest.raises(Exception, match="recursive structural arrays are not supported"):
        indirect.result_shape(first, {})


def test_structural_array_dataclasses_do_not_embed_backend_or_ast_payload_fields():
    """Frontend array records expose only semantic identities, types, and immutable structure."""
    import dataclasses

    forbidden = {"value", "socket", "node", "ast", "ir_value", "backend"}
    for record_type in (StructuralArrayId, StructuralRuntimeLeaf, StructuralArrayRef, StructuralArrayState, StructuralArraySnapshot):
        assert forbidden.isdisjoint(field.name for field in dataclasses.fields(record_type))
