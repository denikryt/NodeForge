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
    _map_range,
    _new_node,
    _value,
    _position,
    _is_number_type,
)
from ..statements import _kw_dict, _check_no_extra_keywords
from ..consteval import _const_eval
from ..values import Value

NAMES = set(_FLOAT_FUNCS_1) | set(_FLOAT_FUNCS_2) | {
    "clamp", "mix", "lerp", "select", "map_range",
    "noise", "random_value",
}


def compile_call(comp, expr, depth=0):
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


def _compile_noise(comp, expr, x, y):
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
    try:
        socket.default_value = float(_const_eval(expr, comp.consts))
        return
    except CompileError:
        pass
    value = comp.compile(expr)
    if not _is_number_type(value.typ):
        raise CompileError(f"{label} expects numeric input")
    comp.group.links.new(value.socket, socket)
