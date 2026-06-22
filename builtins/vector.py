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
    _separate_xyz,
    _vector_math,
    _math,
    _clamp,
    _value,
    _is_number_type,
)
from ..consteval import _const_eval, _as_float_const
from ..compile_time import reject_compile_time_object

_FIELD_VECTOR_NAMES = {"rotate2d", "polar", "angle_between", "rotate_around_axis"}

NAMES = {"vector"} | set(_VECTOR_MATH_FLOAT_OUTPUT) | set(_VECTOR_MATH_VECTOR_OUTPUT_1) | set(_VECTOR_MATH_VECTOR_OUTPUT_2) | _FIELD_VECTOR_NAMES


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
    if name == "rotate2d":
        return _compile_rotate2d(comp.group, args, x, y)
    if name == "polar":
        return _compile_polar(comp.group, args, x, y)
    if name == "angle_between":
        return _compile_angle_between(comp.group, args, x, y)
    if name == "rotate_around_axis":
        return _compile_rotate_around_axis(comp.group, args, x, y)
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


def _ensure_vector(value, label):
    """Validate a helper argument that must be a Vector."""
    if value.typ != TYPE_VECTOR:
        raise CompileError(f"{label} expects a Vector")


def _ensure_numeric(value, label):
    """Validate a helper argument that must be numeric."""
    if not _is_number_type(value.typ):
        raise CompileError(f"{label} expects a numeric value")


def _scale_vector(group, vector, scalar, x, y):
    """Scale a Vector by a numeric Value."""
    _ensure_vector(vector, "Vector scale")
    _ensure_numeric(scalar, "Vector scale")
    return _vector_math(group, "SCALE", [vector, scalar], TYPE_VECTOR, x, y)


def _compile_rotate2d(group, args, x, y):
    """Compile rotate2d(v, angle) as XY-plane rotation preserving Z."""
    if len(args) != 2:
        raise CompileError("rotate2d(v, angle) expects 2 arguments")
    vector, angle = args
    _ensure_vector(vector, "rotate2d() v")
    _ensure_numeric(angle, "rotate2d() angle")
    vx = _separate_xyz(group, vector, "x", x + 20, y - 40)
    vy = _separate_xyz(group, vector, "y", x + 20, y - 80)
    vz = _separate_xyz(group, vector, "z", x + 20, y - 120)
    cos_a = _math(group, "COSINE", [angle], x + 40, y - 160)
    sin_a = _math(group, "SINE", [angle], x + 40, y - 200)
    x_cos = _math(group, "MULTIPLY", [vx, cos_a], x + 60, y - 240)
    y_sin = _math(group, "MULTIPLY", [vy, sin_a], x + 60, y - 280)
    x_sin = _math(group, "MULTIPLY", [vx, sin_a], x + 60, y - 320)
    y_cos = _math(group, "MULTIPLY", [vy, cos_a], x + 60, y - 360)
    out_x = _math(group, "SUBTRACT", [x_cos, y_sin], x + 80, y - 400)
    out_y = _math(group, "ADD", [x_sin, y_cos], x + 80, y - 440)
    return _combine_xyz_mixed(group, [out_x, out_y, vz], x, y)


def _compile_polar(group, args, x, y):
    """Compile polar(radius, angle) into a Vector on the XY plane."""
    if len(args) != 2:
        raise CompileError("polar(radius, angle) expects 2 arguments")
    radius, angle = args
    _ensure_numeric(radius, "polar() radius")
    _ensure_numeric(angle, "polar() angle")
    cos_a = _math(group, "COSINE", [angle], x + 20, y - 40)
    sin_a = _math(group, "SINE", [angle], x + 20, y - 80)
    out_x = _math(group, "MULTIPLY", [radius, cos_a], x + 40, y - 120)
    out_y = _math(group, "MULTIPLY", [radius, sin_a], x + 40, y - 160)
    return _combine_xyz_mixed(group, [out_x, out_y, 0.0], x, y)


def _compile_angle_between(group, args, x, y):
    """Compile angle_between(a, b) as acos(clamp(dot(normalize(a), normalize(b)), -1, 1))."""
    if len(args) != 2:
        raise CompileError("angle_between(a, b) expects 2 arguments")
    a, b = args
    _ensure_vector(a, "angle_between() a")
    _ensure_vector(b, "angle_between() b")
    na = _vector_math(group, "NORMALIZE", [a], TYPE_VECTOR, x + 20, y - 40)
    nb = _vector_math(group, "NORMALIZE", [b], TYPE_VECTOR, x + 20, y - 80)
    dot_value = _vector_math(group, "DOT_PRODUCT", [na, nb], TYPE_FLOAT, x + 40, y - 120)
    clamped = _clamp(group, dot_value, _value(group, -1.0, x + 40, y - 160), _value(group, 1.0, x + 40, y - 200), x + 40, y - 240)
    return _math(group, "ARCCOSINE", [clamped], x, y)


def _compile_rotate_around_axis(group, args, x, y):
    """Compile rotate_around_axis(v, axis, angle) with Rodrigues' rotation formula."""
    if len(args) != 3:
        raise CompileError("rotate_around_axis(v, axis, angle) expects 3 arguments")
    vector, axis, angle = args
    _ensure_vector(vector, "rotate_around_axis() v")
    _ensure_vector(axis, "rotate_around_axis() axis")
    _ensure_numeric(angle, "rotate_around_axis() angle")
    axis_n = _vector_math(group, "NORMALIZE", [axis], TYPE_VECTOR, x + 20, y - 40)
    cos_a = _math(group, "COSINE", [angle], x + 20, y - 80)
    sin_a = _math(group, "SINE", [angle], x + 20, y - 120)
    one_minus_cos = _math(group, "SUBTRACT", [_value(group, 1.0, x + 20, y - 160), cos_a], x + 20, y - 200)

    term_a = _scale_vector(group, vector, cos_a, x + 40, y - 240)
    cross_axis_v = _vector_math(group, "CROSS_PRODUCT", [axis_n, vector], TYPE_VECTOR, x + 40, y - 280)
    term_b = _scale_vector(group, cross_axis_v, sin_a, x + 40, y - 320)
    axis_dot_v = _vector_math(group, "DOT_PRODUCT", [axis_n, vector], TYPE_FLOAT, x + 40, y - 360)
    axis_weight = _math(group, "MULTIPLY", [axis_dot_v, one_minus_cos], x + 40, y - 400)
    term_c = _scale_vector(group, axis_n, axis_weight, x + 40, y - 440)

    partial = _vector_math(group, "ADD", [term_a, term_b], TYPE_VECTOR, x + 60, y - 480)
    return _vector_math(group, "ADD", [partial, term_c], TYPE_VECTOR, x, y)
