"""Pure Python contracts for deferred constant payloads and identity."""

import pytest

from NodeForge.blender.values import ConstantPayload, DeferredConstant, Value
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType


def test_deferred_payload_canonicalizes_numeric_and_vector_data():
    """Canonicalization preserves NFType and makes composites immutable."""
    scalar = DeferredConstant(NFType.FLOAT, 1.25)
    vector = DeferredConstant(NFType.VECTOR, (1, 2.5, -3))
    assert scalar.value == 1.25 and scalar.typ is NFType.FLOAT
    assert vector.value == (1.0, 2.5, -3.0)
    assert isinstance(vector.value, tuple)
    assert scalar._materialized is None and vector._owner_group is None
    assert vector is not DeferredConstant(NFType.VECTOR, vector.value)


@pytest.mark.parametrize("typ,value", [
    (NFType.BOOL, 1),
    (NFType.INT, 1.5),
    (NFType.VECTOR, (0.0, 1.0)),
    (NFType.COLOR, (-1.0, 0.0, 0.0, 1.0)),
    (NFType.ROTATION, (0.0, False, 0.0)),
    (NFType.GEOMETRY, "not a geometry"),
])
def test_invalid_deferred_payload_is_rejected_at_creation(typ, value):
    """Invalid typed values never reach a Blender node or a downstream consumer."""
    with pytest.raises(CompileError):
        ConstantPayload(typ, value)


def test_deferred_group_owner_is_pinned_and_never_crosses_trees():
    """Deferred constants may be shared within one tree but never reused across owners."""
    constant = DeferredConstant(NFType.INT, 7)
    tree_a, tree_b = object(), object()
    constant.bind_group(tree_a)
    constant.bind_group(tree_a)
    with pytest.raises(CompileError, match="cross physical node-tree"):
        constant.bind_group(tree_b)
