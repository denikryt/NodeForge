"""Curated wrappers implemented through the raw node builder."""

from __future__ import annotations

from ..compile_time import reject_compile_time_object
from ..constants import TYPE_BOOL, TYPE_FLOAT, TYPE_VECTOR
from ..errors import CompileError
from ..nodes import _is_number_type
from .raw_nodes import build_raw_node

_COMPARE_OPS = {
    "greater_than": "GREATER_THAN",
    "greater_equal": "GREATER_EQUAL",
    "less_than": "LESS_THAN",
    "less_equal": "LESS_EQUAL",
    "equal": "EQUAL",
    "not_equal": "NOT_EQUAL",
}

NAMES = set(_COMPARE_OPS)


def compile_call(comp, expr, depth=0):
    name = expr.func.id
    if name not in _COMPARE_OPS:
        raise CompileError(f"Unsupported node wrapper: {name}")
    if expr.keywords:
        raise CompileError(f"{name}(a, b) does not accept keyword arguments")
    if len(expr.args) != 2:
        raise CompileError(f"{name}(a, b) expects 2 arguments")
    left = comp.compile(expr.args[0])
    right = comp.compile(expr.args[1])
    reject_compile_time_object(left, f"{name}() argument")
    reject_compile_time_object(right, f"{name}() argument")
    data_type = _compare_data_type(left.typ, right.typ)
    return build_raw_node(
        comp,
        bl_idname="FunctionNodeCompare",
        props={"data_type": data_type, "operation": _COMPARE_OPS[name]},
        inputs={"A": left, "B": right},
        output="Result",
        typ=TYPE_BOOL,
        x=depth * 240,
        y=-depth * 90,
        context=f"{name}()",
    )


def _compare_data_type(left_typ, right_typ):
    if _is_number_type(left_typ) and _is_number_type(right_typ):
        return "FLOAT"
    if left_typ == right_typ == TYPE_VECTOR:
        return "VECTOR"
    raise CompileError("compare wrappers expect both numeric or both Vector arguments")


__all__ = ["NAMES", "compile_call"]
