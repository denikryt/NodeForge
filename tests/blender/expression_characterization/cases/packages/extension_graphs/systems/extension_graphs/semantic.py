"""Resolve the result signature at each invocation, not at extension declaration."""

from NodeForge.nf_types import NFType
from .interface import Part


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
