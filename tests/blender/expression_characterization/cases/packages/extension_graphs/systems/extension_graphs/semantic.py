"""Resolve the result signature at each invocation, not at extension declaration."""

from NodeForge.nf_types import NFType
from .interface import Part, ShapeState


def _result_types_mixed(arguments):
    """Select Float and Vector leaves for each mixed() call."""
    return {"scalar": NFType.FLOAT, "vector": NFType.VECTOR}


def _result_types_choose(arguments):
    """Select a singleton named result whose type follows the compile-time form."""
    return {"value": NFType.VECTOR if arguments["form"].value == "VECTOR" else NFType.FLOAT}


def make_part(value) -> Part:
    """Package a runtime value in a semantic record."""
    return Part(value)


def consume_part(part) -> Part:
    """Forward a record as semantic state to the physical backend."""
    return part



def _result_types_shape(arguments):
    """Resolve explicit shape without physical evaluation."""
    form = arguments["form"].value
    if form == "VECTOR":
        return NFType.VECTOR
    if form == "ONE":
        return {"value": NFType.FLOAT}
    if form == "MULTI":
        return {"scaled": NFType.FLOAT, "original": NFType.FLOAT}
    return NFType.FLOAT


def _result_types_shape_state(arguments):
    """Use the same schema for semantic-state execution."""
    return _result_types_shape(arguments)


def shape_state(value, form="DIRECT") -> ShapeState:
    """Carry the runtime leaf and the selected form into BACKEND."""
    return ShapeState(value, form)


def _result_types_structured(arguments):
    """Use detached labels and the typed leaves without executing backend code."""
    assert all(leaf.typ is NFType.FLOAT for leaf in arguments['items'].values())
    labels = arguments['labels']
    return {labels[0].value: NFType.FLOAT} if labels else NFType.FLOAT
