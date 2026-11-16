"""Immutable Blender enum capabilities for operations over existing NFType values.

The mappings describe support in particular operations, rather than defining
canonical type identity or authorizing conversions between semantic types.
"""
from types import MappingProxyType

from .nf_types import NFType

SWITCH_INPUT_TYPES = MappingProxyType({
    NFType.FLOAT: "FLOAT",
    NFType.INT: "INT",
    NFType.VECTOR: "VECTOR",
    NFType.BOOL: "BOOLEAN",
    NFType.GEOMETRY: "GEOMETRY",
    NFType.STRING: "STRING",
    NFType.BUNDLE: "BUNDLE",
    NFType.COLOR: "RGBA",
})

REPEAT_ITEM_TYPES = MappingProxyType({
    NFType.GEOMETRY: "GEOMETRY",
    NFType.VECTOR: "VECTOR",
    NFType.FLOAT: "FLOAT",
    NFType.INT: "INT",
    NFType.BOOL: "BOOLEAN",
    NFType.BUNDLE: "BUNDLE",
    NFType.COLOR: "RGBA",
})

CAPTURE_ITEM_TYPES = MappingProxyType({
    NFType.FLOAT: "FLOAT",
    NFType.INT: "INT",
    NFType.BOOL: "BOOLEAN",
    NFType.VECTOR: "VECTOR",
    NFType.COLOR: "RGBA",
})

ATTRIBUTE_DATA_TYPES = MappingProxyType({
    NFType.FLOAT: "FLOAT",
    NFType.INT: "INT",
    NFType.VECTOR: "FLOAT_VECTOR",
    NFType.BOOL: "BOOLEAN",
    NFType.COLOR: "FLOAT_COLOR",
})

__all__ = [
    "SWITCH_INPUT_TYPES", "REPEAT_ITEM_TYPES", "CAPTURE_ITEM_TYPES",
    "ATTRIBUTE_DATA_TYPES",
]
