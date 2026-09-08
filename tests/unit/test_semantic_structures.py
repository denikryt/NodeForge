"""Stage-18 compiler-owned structural and Object semantic contracts."""

import ast
from pathlib import Path
from types import MappingProxyType

import pytest

from NodeForge.compiler_identities import BindingId
from NodeForge.nf_types import NFType
from NodeForge.semantic_ir import (
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
from NodeForge.semantic_values import (
    ObjectInfoState,
    ObjectSemanticId,
    ObjectSemanticSnapshot,
    RuntimeResultShape,
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


def test_ir_bind_leaves_rejects_irarray_results():
    value = IRValue(0, NFType.FLOAT)
    program = IRProgram((), IRArray((value,)))

    with pytest.raises(TypeError, match="does not accept IRArray"):
        IRBindLeaves(
            program,
            (IRLeafBinding(value, BindingId("dest", 0), NFType.FLOAT),),
        )


def test_ir_discard_expression_accepts_structural_program_and_is_detached():
    discard = IRDiscardExpression(_tuple_program())
    assert isinstance(discard.value.result, IRTuple)
    assert not any(isinstance(value, ast.AST) for value in discard.value.operations)


def test_semantic_modules_are_blender_independent_and_no_aggregate_nftypes_exist():
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "semantic_values.py",
        "semantic_ir.py",
        "semantic_analysis.py",
        "semantic_lowering.py",
        "semantic_body.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "import bpy" not in source
        assert "from bpy" not in source
    assert not hasattr(NFType, "TUPLE")
    assert not hasattr(NFType, "NODE_RESULT")
    assert not hasattr(NFType, "NAMED_OUTPUTS")


def test_ir_structural_records_do_not_embed_backend_containers_or_mutable_fields():
    from NodeForge.values import NodeResult, TupleValue

    assert all(field.name not in {"tuple_value", "node_result"} for field in __import__("dataclasses").fields(IRBindLeaves))
    assert NodeResult is not IRNamedOutputs
    assert TupleValue is not IRTuple
