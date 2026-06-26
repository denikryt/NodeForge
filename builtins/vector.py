"""Vector built-ins for NodeForge DSL."""

from ..constants import (
    TYPE_FLOAT,
    TYPE_VECTOR,
    _VECTOR_MATH_FLOAT_OUTPUT,
    _VECTOR_MATH_VECTOR_OUTPUT_1,
    _VECTOR_MATH_VECTOR_OUTPUT_2,
)
from ..errors import CompileError
from ..nodes import (
    _combine_xyz_mixed,
    _vector_math,
)
from ..consteval import _const_eval, _as_float_const
from ..compile_time import reject_compile_time_object

NAMES = {"vector"} | set(_VECTOR_MATH_FLOAT_OUTPUT) | set(_VECTOR_MATH_VECTOR_OUTPUT_1) | set(_VECTOR_MATH_VECTOR_OUTPUT_2)


def compile_call(comp, expr, depth=0):
    """Compile vector construction, vector math, and vector field helper calls."""
    name = expr.func.id
    x = depth * 240
    y = -depth * 90
    if name == "vector":
        vector_args = _ordered_vector_args(expr)
        comps = []
        for comp_expr in vector_args:
            try:
                comps.append(_as_float_const(_const_eval(comp_expr, comp.consts), "vector component"))
            except CompileError:
                value = comp.compile(comp_expr)
                reject_compile_time_object(value, "vector() component")
                comps.append(value)
        return _combine_xyz_mixed(comp.group, comps, x, y)

    if expr.keywords:
        args_exprs = _ordered_keyword_args(name, expr)
    else:
        args_exprs = list(expr.args)
    args = [comp.compile(arg) for arg in args_exprs]
    reject_compile_time_object(args, f"{name}() arguments")
    if name in _VECTOR_MATH_FLOAT_OUTPUT:
        expected = 2 if name in {"distance", "dot"} else 1
        if len(args) != expected:
            raise CompileError(f"{name}() expects {expected} argument(s)")
        return _vector_math(comp.group, _VECTOR_MATH_FLOAT_OUTPUT[name], args, TYPE_FLOAT, x, y)
    if name in _VECTOR_MATH_VECTOR_OUTPUT_1:
        if len(args) != 1:
            raise CompileError(f"{name}() expects 1 argument")
        return _vector_math(comp.group, _VECTOR_MATH_VECTOR_OUTPUT_1[name], args, TYPE_VECTOR, x, y)
    if name in _VECTOR_MATH_VECTOR_OUTPUT_2:
        if len(args) != 2:
            raise CompileError(f"{name}() expects 2 arguments")
        return _vector_math(comp.group, _VECTOR_MATH_VECTOR_OUTPUT_2[name], args, TYPE_VECTOR, x, y)
    raise CompileError(f"Unsupported vector builtin: {name}")


def _ordered_vector_args(expr):
    """Return vector component AST args after applying x/y/z keyword aliases."""
    names = ["x", "y", "z"]
    if len(expr.args) > 3:
        raise CompileError("vector(x, y, z) expects 3 arguments")
    out = list(expr.args)
    used = set(names[:len(expr.args)])
    by_name = {}
    for kw in expr.keywords:
        if kw.arg is None:
            raise CompileError("vector() does not support **kwargs")
        if kw.arg not in names:
            raise CompileError(f"vector() got unknown keyword argument {kw.arg!r}")
        if kw.arg in used or kw.arg in by_name:
            raise CompileError(f"vector() got multiple values for argument {kw.arg!r}")
        by_name[kw.arg] = kw.value
    for param in names[len(expr.args):]:
        if param not in by_name:
            raise CompileError(f"vector() missing argument: {param}")
        out.append(by_name[param])
    return out


def _ordered_keyword_args(name, expr):
    """Return positional AST args for vector helpers that accept keyword arguments."""
    specs = {
        "rotate2d": ["v", "angle"],
        "polar": ["radius", "angle"],
        "angle_between": ["a", "b"],
        "rotate_around_axis": ["v", "axis", "angle"],
    }
    if name not in specs:
        raise CompileError(f"{name}() does not support keyword arguments")
    names = specs[name]
    if len(expr.args) > len(names):
        raise CompileError(f"{name}() got too many positional arguments")
    out = list(expr.args)
    used = set(names[:len(expr.args)])
    by_name = {}
    for kw in expr.keywords:
        if kw.arg is None:
            raise CompileError(f"{name}() does not support **kwargs")
        if kw.arg not in names:
            raise CompileError(f"{name}() got unknown keyword argument {kw.arg!r}")
        if kw.arg in used or kw.arg in by_name:
            raise CompileError(f"{name}() got multiple values for argument {kw.arg!r}")
        by_name[kw.arg] = kw.value
    for param in names[len(expr.args):]:
        if param not in by_name:
            raise CompileError(f"{name}() missing argument: {param}")
        out.append(by_name[param])
    return out
