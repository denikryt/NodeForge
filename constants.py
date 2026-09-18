"""Shared constants and Blender socket type names for NodeForge compiler."""

import ast
import math
from types import MappingProxyType

from .nf_types import NFType

# CANONICAL_NFTYPE_TYPE_ALIAS_MIGRATION: TYPE_* names temporarily preserve existing
# NodeForge-owned imports while their values are canonical NFType members. Do not use
# these aliases to accept or serialize raw type strings. Remove the alias block after
# all NodeForge-owned runtime-type references use NFType directly and migration tests
# confirm no supported public contract depends on TYPE_* symbol names.
TYPE_FLOAT = NFType.FLOAT
TYPE_VECTOR = NFType.VECTOR
TYPE_BOOL = NFType.BOOL
TYPE_GEOMETRY = NFType.GEOMETRY
TYPE_INT = NFType.INT
TYPE_MATERIAL = NFType.MATERIAL
TYPE_OBJECT = NFType.OBJECT
TYPE_STRING = NFType.STRING
TYPE_BUNDLE = NFType.BUNDLE
TYPE_ROTATION = NFType.ROTATION

OBJECT_PROPERTY_TYPES = MappingProxyType({
    "geometry": TYPE_GEOMETRY,
    "location": TYPE_VECTOR,
    "rotation": TYPE_VECTOR,
    "scale": TYPE_VECTOR,
})

TYPE_TOKEN_NAMES = {
    "Float": TYPE_FLOAT,
    "Int": TYPE_INT,
    "Bool": TYPE_BOOL,
    "Vector": TYPE_VECTOR,
    "Geometry": TYPE_GEOMETRY,
    "Material": TYPE_MATERIAL,
    "Object": TYPE_OBJECT,
    "String": TYPE_STRING,
    "Bundle": TYPE_BUNDLE,
}

_ALLOWED_CONSTS = {"pi": math.pi, "tau": math.tau, "e": math.e}

_BIN_OPS = {
    ast.Add: "ADD",
    ast.Sub: "SUBTRACT",
    ast.Mult: "MULTIPLY",
    ast.Div: "DIVIDE",
    ast.FloorDiv: "FLOOR_DIVIDE",
    ast.Pow: "POWER",
    ast.Mod: "MODULO",
}
_COMPARE_OPS = {ast.Lt: "LESS_THAN", ast.LtE: "LESS_EQUAL", ast.Gt: "GREATER_THAN", ast.GtE: "GREATER_EQUAL", ast.Eq: "EQUAL", ast.NotEq: "NOT_EQUAL"}
_BOOLEAN_OPS = {ast.And: "AND", ast.Or: "OR"}
_VECTOR_MATH_FLOAT_OUTPUT = {"length": "LENGTH", "distance": "DISTANCE", "dot": "DOT_PRODUCT"}
_VECTOR_MATH_VECTOR_OUTPUT_1 = {"normalize": "NORMALIZE"}
_VECTOR_MATH_VECTOR_OUTPUT_2 = {"cross": "CROSS_PRODUCT", "reflect": "REFLECT", "project": "PROJECT"}
__all__ = [name for name in globals() if name.startswith('_') or name.startswith('TYPE_')] + ['OBJECT_PROPERTY_TYPES']
