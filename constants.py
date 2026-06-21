"""Shared constants and Blender socket type names for NodeForge compiler."""

import ast
import math

TYPE_FLOAT = "FLOAT"
TYPE_VECTOR = "VECTOR"
TYPE_BOOL = "BOOL"
TYPE_GEOMETRY = "GEOMETRY"
TYPE_INT = "INT"

_ALLOWED_CONSTS = {"pi": math.pi, "tau": math.tau, "e": math.e}

_FLOAT_FUNCS_1 = {
    "sin": "SINE", "cos": "COSINE", "tan": "TANGENT",
    "asin": "ARCSINE", "acos": "ARCCOSINE", "atan": "ARCTANGENT",
    "sqrt": "SQRT", "abs": "ABSOLUTE", "floor": "FLOOR", "ceil": "CEIL",
    "round": "ROUND", "fract": "FRACT", "frac": "FRACT", "radians": "RADIANS", "degrees": "DEGREES",
    "exp": "EXPONENT", "ln": "NATURAL_LOGARITHM", "sign": "SIGN",
}
_FLOAT_FUNCS_2 = {"min": "MINIMUM", "max": "MAXIMUM", "pow": "POWER", "log": "LOGARITHM", "atan2": "ARCTANGENT2", "mod": "MODULO"}
_BIN_OPS = {ast.Add: "ADD", ast.Sub: "SUBTRACT", ast.Mult: "MULTIPLY", ast.Div: "DIVIDE", ast.Pow: "POWER", ast.Mod: "MODULO"}
_COMPARE_OPS = {ast.Lt: "LESS_THAN", ast.LtE: "LESS_EQUAL", ast.Gt: "GREATER_THAN", ast.GtE: "GREATER_EQUAL", ast.Eq: "EQUAL", ast.NotEq: "NOT_EQUAL"}
_BOOLEAN_OPS = {ast.And: "AND", ast.Or: "OR"}
_VECTOR_MATH_FLOAT_OUTPUT = {"length": "LENGTH", "distance": "DISTANCE", "dot": "DOT_PRODUCT"}
_VECTOR_MATH_VECTOR_OUTPUT_1 = {"normalize": "NORMALIZE"}
_VECTOR_MATH_VECTOR_OUTPUT_2 = {"cross": "CROSS_PRODUCT", "reflect": "REFLECT", "project": "PROJECT"}
GEOMETRY_MACRO_NAMES = {
    "cube", "join", "transform", "polyline",
    "points", "set_position", "instance_on_points",
    "realize_instances", "input_geometry", "input_float", "input_int",
    "input_bool", "input_vector",
}

_BUILTIN_NAMES = set(_FLOAT_FUNCS_1) | set(_FLOAT_FUNCS_2) | set(_VECTOR_MATH_FLOAT_OUTPUT) | set(_VECTOR_MATH_VECTOR_OUTPUT_1) | set(_VECTOR_MATH_VECTOR_OUTPUT_2) | {
    "clamp", "mix", "lerp", "select", "vector", "position", "normal", "index", "id",
    "set_position", "output", "store", "mod", "frac", "map_range", "sign", "noise", "random_value", "range", "runtime_range"
} | GEOMETRY_MACRO_NAMES

__all__ = [name for name in globals() if name.startswith('_') or name.startswith('TYPE_') or name == "GEOMETRY_MACRO_NAMES"]
