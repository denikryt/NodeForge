"""Shared constants and Blender socket type names for NodeForge compiler."""

import ast
import math

TYPE_FLOAT = "FLOAT"
TYPE_VECTOR = "VECTOR"
TYPE_BOOL = "BOOL"
TYPE_GEOMETRY = "GEOMETRY"
TYPE_INT = "INT"
TYPE_MATERIAL = "MATERIAL"
TYPE_OBJECT = "OBJECT"

TYPE_TOKEN_NAMES = {
    "Float": TYPE_FLOAT,
    "Int": TYPE_INT,
    "Bool": TYPE_BOOL,
    "Vector": TYPE_VECTOR,
    "Geometry": TYPE_GEOMETRY,
    "Material": TYPE_MATERIAL,
    "Object": TYPE_OBJECT,
}

_ALLOWED_CONSTS = {"pi": math.pi, "tau": math.tau, "e": math.e}

_FLOAT_FUNCS_1 = {
    "sin": "SINE", "cos": "COSINE", "tan": "TANGENT",
    "asin": "ARCSINE", "acos": "ARCCOSINE", "atan": "ARCTANGENT",
    "sqrt": "SQRT", "abs": "ABSOLUTE", "floor": "FLOOR", "ceil": "CEIL",
    "round": "ROUND", "fract": "FRACT", "radians": "RADIANS", "degrees": "DEGREES",
    "exp": "EXPONENT",
}
_FLOAT_FUNCS_2 = {"min": "MINIMUM", "max": "MAXIMUM", "pow": "POWER", "log": "LOGARITHM", "atan2": "ARCTAN2", "mod": "MODULO"}
_BIN_OPS = {ast.Add: "ADD", ast.Sub: "SUBTRACT", ast.Mult: "MULTIPLY", ast.Div: "DIVIDE", ast.Pow: "POWER", ast.Mod: "MODULO"}
_COMPARE_OPS = {ast.Lt: "LESS_THAN", ast.LtE: "LESS_EQUAL", ast.Gt: "GREATER_THAN", ast.GtE: "GREATER_EQUAL", ast.Eq: "EQUAL", ast.NotEq: "NOT_EQUAL"}
_BOOLEAN_OPS = {ast.And: "AND", ast.Or: "OR"}
_VECTOR_MATH_FLOAT_OUTPUT = {"length": "LENGTH", "distance": "DISTANCE", "dot": "DOT_PRODUCT"}
_VECTOR_MATH_VECTOR_OUTPUT_1 = {"normalize": "NORMALIZE"}
_VECTOR_MATH_VECTOR_OUTPUT_2 = {"cross": "CROSS_PRODUCT", "reflect": "REFLECT", "project": "PROJECT"}
__all__ = [name for name in globals() if name.startswith('_') or name.startswith('TYPE_')]
