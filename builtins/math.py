"""Math and procedural scalar/vector built-ins for NodeForge DSL."""

import ast

from ..constants import (
    TYPE_FLOAT,
    TYPE_VECTOR,
    TYPE_INT,
    _FLOAT_FUNCS_1,
    _FLOAT_FUNCS_2,
)
from ..errors import CompileError
from ..nodes import (
    _math,
    _clamp,
    _mix,
    _switch,
    _compare,
    _map_range,
    _new_node,
    _value,
    _position,
    _is_number_type,
)
from ..statements import _kw_dict, _check_no_extra_keywords
from ..consteval import _const_eval
from ..values import Value

_FIELD_MATH_NAMES = {
    "inverse_lerp", "remap", "saturate", "step",
    "smoothstep", "smootherstep", "pingpong", "wrap",
}

NAMES = set(_FLOAT_FUNCS_1) | set(_FLOAT_FUNCS_2) | {
    "clamp", "mix", "lerp", "select", "map_range",
    "noise", "random_value",
} | _FIELD_MATH_NAMES


def compile_call(comp, expr, depth=0):
    """Compile a scalar, field, noise, or random-value built-in call."""
    name = expr.func.id
    x = depth * 240
    y = -depth * 90

    if name == "noise":
        return _compile_noise(comp, expr, x, y)
    if name == "random_value":
        return _compile_random_value(comp, expr, x, y)

    if expr.keywords:
        args_exprs = _ordered_keyword_args(name, expr)
    else:
        args_exprs = list(expr.args)
    args = [comp.compile(arg) for arg in args_exprs]
    if name in _FLOAT_FUNCS_1:
        if len(args) != 1:
            raise CompileError(f"{name}() expects 1 argument")
        return _math(comp.group, _FLOAT_FUNCS_1[name], args, x, y)
    if name in _FLOAT_FUNCS_2:
        if len(args) != 2:
            raise CompileError(f"{name}() expects 2 arguments")
        return _math(comp.group, _FLOAT_FUNCS_2[name], args, x, y)
    if name == "clamp":
        if len(args) != 3:
            raise CompileError("clamp(value, min, max) expects 3 arguments")
        return _clamp(comp.group, args[0], args[1], args[2], x, y)
    if name in {"mix", "lerp"}:
        if len(args) != 3:
            raise CompileError("mix(a, b, factor) expects 3 arguments")
        return _mix(comp.group, args[0], args[1], args[2], x, y)
    if name == "select":
        if len(args) != 3:
            raise CompileError("select(cond, false, true) expects 3 arguments")
        return _switch(comp.group, args[0], args[1], args[2], x, y)
    if name == "map_range":
        return _map_range(comp.group, args, x, y)
    if name == "inverse_lerp":
        return _compile_inverse_lerp(comp.group, args, x, y)
    if name == "remap":
        return _compile_remap(comp.group, args, x, y)
    if name == "saturate":
        return _compile_saturate(comp.group, args, x, y)
    if name == "step":
        return _compile_step(comp.group, args, x, y)
    if name == "smoothstep":
        return _compile_smoothstep(comp.group, args, x, y)
    if name == "smootherstep":
        return _compile_smootherstep(comp.group, args, x, y)
    if name == "pingpong":
        return _compile_pingpong(comp.group, args, x, y)
    if name == "wrap":
        return _compile_wrap(comp.group, args, x, y)
    raise CompileError(f"Unsupported math builtin: {name}")


def _ordered_keyword_args(name, expr):
    """Return positional AST args after applying simple keyword aliases."""
    specs = {}
    for fn in _FLOAT_FUNCS_1:
        specs[fn] = ["value"]
    for fn in _FLOAT_FUNCS_2:
        specs[fn] = ["a", "b"]
    specs.update({
        "clamp": ["value", "min", "max"],
        "mix": ["a", "b", "factor"],
        "lerp": ["a", "b", "factor"],
        "select": ["cond", "false", "true"],
        "map_range": ["value", "from_min", "from_max", "to_min", "to_max"],
        "inverse_lerp": ["a", "b", "x"],
        "remap": ["x", "in_min", "in_max", "out_min", "out_max"],
        "saturate": ["x"],
        "step": ["edge", "x"],
        "smoothstep": ["edge0", "edge1", "x"],
        "smootherstep": ["edge0", "edge1", "x"],
        "pingpong": ["x", "length"],
        "wrap": ["x", "min", "max"],
    })
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


def _ensure_numeric_args(name, args, count):
    """Validate the number and semantic type of scalar helper arguments."""
    if len(args) != count:
        raise CompileError(f"{name}() expects {count} argument(s)")
    if not all(_is_number_type(arg.typ) for arg in args):
        raise CompileError(f"{name}() expects numeric arguments")


def _compile_inverse_lerp(group, args, x, y):
    """Compile inverse_lerp(a, b, x) as (x - a) / (b - a)."""
    _ensure_numeric_args("inverse_lerp", args, 3)
    a, b, value = args
    numerator = _math(group, "SUBTRACT", [value, a], x + 20, y - 40)
    denominator = _math(group, "SUBTRACT", [b, a], x + 20, y - 80)
    return _math(group, "DIVIDE", [numerator, denominator], x, y)


def _compile_remap(group, args, x, y):
    """Compile remap(x, in_min, in_max, out_min, out_max) as an unclamped linear map."""
    _ensure_numeric_args("remap", args, 5)
    value, in_min, in_max, out_min, out_max = args
    t = _compile_inverse_lerp(group, [in_min, in_max, value], x + 20, y - 40)
    out_size = _math(group, "SUBTRACT", [out_max, out_min], x + 20, y - 80)
    scaled = _math(group, "MULTIPLY", [t, out_size], x + 20, y - 120)
    return _math(group, "ADD", [scaled, out_min], x, y)


def _compile_saturate(group, args, x, y):
    """Compile saturate(x) as clamp(x, 0, 1)."""
    _ensure_numeric_args("saturate", args, 1)
    return _clamp(group, args[0], _value(group, 0.0, x + 20, y - 40), _value(group, 1.0, x + 20, y - 80), x, y)


def _compile_step(group, args, x, y):
    """Compile step(edge, x) as 0 below the edge and 1 at or above it."""
    _ensure_numeric_args("step", args, 2)
    edge, value = args
    cond = _compare(group, "GREATER_EQUAL", value, edge, x + 20, y - 40)
    return _switch(group, cond, _value(group, 0.0, x + 20, y - 80), _value(group, 1.0, x + 20, y - 120), x, y)


def _compile_smoothstep(group, args, x, y):
    """Compile smoothstep(edge0, edge1, x) with cubic Hermite smoothing."""
    _ensure_numeric_args("smoothstep", args, 3)
    t = _compile_saturate(group, [_compile_inverse_lerp(group, args, x + 20, y - 40)], x + 20, y - 80)
    t2 = _math(group, "MULTIPLY", [t, t], x + 40, y - 120)
    two_t = _math(group, "MULTIPLY", [_value(group, 2.0, x + 40, y - 160), t], x + 40, y - 200)
    three_minus_two_t = _math(group, "SUBTRACT", [_value(group, 3.0, x + 40, y - 240), two_t], x + 40, y - 280)
    return _math(group, "MULTIPLY", [t2, three_minus_two_t], x, y)


def _compile_smootherstep(group, args, x, y):
    """Compile smootherstep(edge0, edge1, x) with quintic smoothing."""
    _ensure_numeric_args("smootherstep", args, 3)
    t = _compile_saturate(group, [_compile_inverse_lerp(group, args, x + 20, y - 40)], x + 20, y - 80)
    t2 = _math(group, "MULTIPLY", [t, t], x + 40, y - 120)
    t3 = _math(group, "MULTIPLY", [t2, t], x + 40, y - 160)
    six_t = _math(group, "MULTIPLY", [_value(group, 6.0, x + 40, y - 200), t], x + 40, y - 240)
    six_t_minus_15 = _math(group, "SUBTRACT", [six_t, _value(group, 15.0, x + 40, y - 280)], x + 40, y - 320)
    inner = _math(group, "MULTIPLY", [six_t_minus_15, t], x + 40, y - 360)
    inner_plus_10 = _math(group, "ADD", [inner, _value(group, 10.0, x + 40, y - 400)], x + 40, y - 440)
    return _math(group, "MULTIPLY", [t3, inner_plus_10], x, y)


def _compile_pingpong(group, args, x, y):
    """Compile pingpong(x, length) as a positive triangular repeating wave."""
    _ensure_numeric_args("pingpong", args, 2)
    value, length = args
    double_length = _math(group, "MULTIPLY", [_value(group, 2.0, x + 20, y - 40), length], x + 20, y - 80)
    raw_wrapped = _math(group, "MODULO", [value, double_length], x + 20, y - 120)
    positive_offset = _math(group, "ADD", [raw_wrapped, double_length], x + 20, y - 160)
    wrapped = _math(group, "MODULO", [positive_offset, double_length], x + 20, y - 200)
    centered = _math(group, "SUBTRACT", [wrapped, length], x + 20, y - 240)
    distance = _math(group, "ABSOLUTE", [centered], x + 20, y - 280)
    return _math(group, "SUBTRACT", [length, distance], x, y)


def _compile_wrap(group, args, x, y):
    """Compile wrap(x, min, max) as a positive repeating value in the given range."""
    _ensure_numeric_args("wrap", args, 3)
    value, min_value, max_value = args
    size = _math(group, "SUBTRACT", [max_value, min_value], x + 20, y - 40)
    shifted = _math(group, "SUBTRACT", [value, min_value], x + 20, y - 80)
    raw_wrapped = _math(group, "MODULO", [shifted, size], x + 20, y - 120)
    positive_offset = _math(group, "ADD", [raw_wrapped, size], x + 20, y - 160)
    wrapped = _math(group, "MODULO", [positive_offset, size], x + 20, y - 200)
    return _math(group, "ADD", [wrapped, min_value], x, y)


def _compile_noise(comp, expr, x, y):
    """Compile noise(vector=..., scale=..., detail=...) into a Noise Texture node."""
    kws = _kw_dict(expr)
    _check_no_extra_keywords(kws, {"scale", "detail", "roughness", "lacunarity", "distortion", "normalize"})
    if len(expr.args) > 1:
        raise CompileError("noise(vector, scale=..., detail=..., roughness=...) expects 0 or 1 positional argument")

    node = _new_node(comp.group, "ShaderNodeTexNoise", x, y)
    node.noise_dimensions = "3D"

    if "normalize" in kws:
        try:
            node.normalize = bool(_const_eval(kws["normalize"], comp.consts))
        except CompileError as exc:
            raise CompileError("noise(..., normalize=...) must be a compile-time bool") from exc

    vector = comp.compile(expr.args[0]) if expr.args else _position(comp.group, x + 20, y - 80)
    if vector.typ != TYPE_VECTOR:
        raise CompileError("noise(vector, ...) expects a Vector input")
    comp.group.links.new(vector.socket, node.inputs[0])

    socket_by_kw = {
        "scale": 2,
        "detail": 3,
        "roughness": 4,
        "lacunarity": 5,
        "distortion": 8,
    }
    for key, idx in socket_by_kw.items():
        if key in kws:
            _wire_or_default_numeric(comp, node.inputs[idx], kws[key], f"noise {key}")

    return Value(node.outputs[0], TYPE_FLOAT)


def _compile_random_value(comp, expr, x, y):
    """Compile random_value() or random_value(min, max, seed=..., id=...)."""
    kws = _kw_dict(expr)
    _check_no_extra_keywords(kws, {"seed", "id"})
    if len(expr.args) not in {0, 2}:
        raise CompileError("random_value() or random_value(min, max, seed=...) expects 0 or 2 positional arguments")

    min_val = comp.compile(expr.args[0]) if len(expr.args) == 2 else _value(comp.group, 0.0, x + 20, y - 80)
    max_val = comp.compile(expr.args[1]) if len(expr.args) == 2 else _value(comp.group, 1.0, x + 20, y - 120)

    if min_val.typ == TYPE_VECTOR and max_val.typ == TYPE_VECTOR:
        data_type = "FLOAT_VECTOR"
        out_type = TYPE_VECTOR
    elif _is_number_type(min_val.typ) and _is_number_type(max_val.typ):
        data_type = "FLOAT"
        out_type = TYPE_FLOAT
    else:
        raise CompileError("random_value(min, max, ...) expects both numeric or both Vector arguments")

    node = _new_node(comp.group, "FunctionNodeRandomValue", x, y)
    node.data_type = data_type
    comp.group.links.new(min_val.socket, node.inputs[0])
    comp.group.links.new(max_val.socket, node.inputs[1])

    if "id" in kws:
        id_val = comp.compile(kws["id"])
        if id_val.typ != TYPE_INT:
            raise CompileError("random_value(..., id=...) expects Int")
        comp.group.links.new(id_val.socket, node.inputs[2])
    if "seed" in kws:
        seed_val = comp.compile(kws["seed"])
        if seed_val.typ != TYPE_INT:
            # Compile-time numeric seed defaults are common; set the socket default instead.
            try:
                node.inputs[3].default_value = int(_const_eval(kws["seed"], comp.consts))
            except CompileError as exc:
                raise CompileError("random_value(..., seed=...) expects Int") from exc
        else:
            comp.group.links.new(seed_val.socket, node.inputs[3])

    return Value(node.outputs[0], out_type)


def _wire_or_default_numeric(comp, socket, expr, label):
    """Wire a numeric expression into a socket or set a compile-time default."""
    try:
        socket.default_value = float(_const_eval(expr, comp.consts))
        return
    except CompileError:
        pass
    value = comp.compile(expr)
    if not _is_number_type(value.typ):
        raise CompileError(f"{label} expects numeric input")
    comp.group.links.new(value.socket, socket)
