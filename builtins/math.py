"""Math and procedural scalar/vector built-ins for NodeForge DSL."""

import ast
import math as _py_math
from dataclasses import dataclass
from typing import Callable

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
from ..compile_time import reject_compile_time_object



@dataclass(frozen=True)
class MathBuiltinSpec:
    """Describe one table-driven math builtin."""

    name: str
    params: tuple[str, ...]
    compile_fn: Callable


def _compile_unary_math(name, op):
    """Return a compiler adapter for one-input Blender Math operations."""

    def compile_unary(group, args, x, y):
        if len(args) != 1:
            raise CompileError(f"{name}() expects 1 argument")
        return _math(group, op, args, x, y)

    return compile_unary


def _compile_binary_math(name, op):
    """Return a compiler adapter for two-input Blender Math operations."""

    def compile_binary(group, args, x, y):
        if len(args) != 2:
            raise CompileError(f"{name}() expects 2 arguments")
        return _math(group, op, args, x, y)

    return compile_binary



def _compile_ln_spec(group, args, x, y):
    """Compile ln(value) as logarithm with base e."""
    if len(args) != 1:
        raise CompileError("ln(value) expects 1 argument")
    e_value = _value(group, _py_math.e, x + 20, y - 40)
    return _math(group, "LOGARITHM", [args[0], e_value], x, y)

def _compile_clamp_spec(group, args, x, y):
    """Compile clamp(value, min, max)."""
    if len(args) != 3:
        raise CompileError("clamp(value, min, max) expects 3 arguments")
    return _clamp(group, args[0], args[1], args[2], x, y)


def _compile_mix_spec(group, args, x, y):
    """Compile mix(a, b, factor)."""
    if len(args) != 3:
        raise CompileError("mix(a, b, factor) expects 3 arguments")
    return _mix(group, args[0], args[1], args[2], x, y)


def _compile_select_spec(group, args, x, y):
    """Compile select(cond, false, true)."""
    if len(args) != 3:
        raise CompileError("select(cond, false, true) expects 3 arguments")
    return _switch(group, args[0], args[1], args[2], x, y)


def _compile_map_range_spec(group, args, x, y):
    """Compile map_range(value, from_min, from_max, to_min, to_max)."""
    return _map_range(group, args, x, y)


def compile_call(comp, expr, depth=0):
    """Compile a scalar, field, noise, or random-value built-in call."""
    name = expr.func.id
    x = depth * 240
    y = -depth * 90

    if name == "noise":
        return _compile_noise(comp, expr, x, y)
    if name == "random_value":
        return _compile_random_value(comp, expr, x, y)

    spec = _SPECS.get(name)
    if spec is None:
        raise CompileError(f"Unsupported math builtin: {name}")

    if expr.keywords:
        args_exprs = _ordered_keyword_args(spec, expr)
    else:
        args_exprs = list(expr.args)
    args = [comp.compile(arg) for arg in args_exprs]
    reject_compile_time_object(args, f"{name}() arguments")
    return spec.compile_fn(comp.group, args, x, y)


def _ordered_keyword_args(spec, expr):
    """Return positional AST args after applying the builtin parameter order."""
    name = spec.name
    names = spec.params
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
    reject_compile_time_object(vector, "noise() vector")
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


def _random_value_node(group, min_val, max_val, *, seed_val=None, id_val=None, seed_default=None, x=0, y=0):
    """Create a Random Value node shared by random_value() and random layouts."""
    reject_compile_time_object(min_val, "random_value() min")
    reject_compile_time_object(max_val, "random_value() max")

    if min_val.typ == TYPE_VECTOR and max_val.typ == TYPE_VECTOR:
        data_type = "FLOAT_VECTOR"
        out_type = TYPE_VECTOR
    elif _is_number_type(min_val.typ) and _is_number_type(max_val.typ):
        data_type = "FLOAT"
        out_type = TYPE_FLOAT
    else:
        raise CompileError("random_value(min, max, ...) expects both numeric or both Vector arguments")

    node = _new_node(group, "FunctionNodeRandomValue", x, y)
    node.data_type = data_type
    group.links.new(min_val.socket, node.inputs[0])
    group.links.new(max_val.socket, node.inputs[1])

    if id_val is not None:
        reject_compile_time_object(id_val, "random_value() id")
        if id_val.typ != TYPE_INT:
            raise CompileError("random_value(..., id=...) expects Int")
        group.links.new(id_val.socket, node.inputs[2])

    if seed_val is not None:
        reject_compile_time_object(seed_val, "random_value() seed")
        if seed_val.typ != TYPE_INT:
            if seed_default is None:
                raise CompileError("random_value(..., seed=...) expects Int")
            node.inputs[3].default_value = int(seed_default)
        else:
            group.links.new(seed_val.socket, node.inputs[3])
    elif seed_default is not None:
        node.inputs[3].default_value = int(seed_default)

    return Value(node.outputs[0], out_type)


def _compile_random_value(comp, expr, x, y):
    """Compile random_value() or random_value(min, max, seed=..., id=...)."""
    kws = _kw_dict(expr)
    _check_no_extra_keywords(kws, {"seed", "id"})
    if len(expr.args) not in {0, 2}:
        raise CompileError("random_value() or random_value(min, max, seed=...) expects 0 or 2 positional arguments")

    min_val = comp.compile(expr.args[0]) if len(expr.args) == 2 else _value(comp.group, 0.0, x + 20, y - 80)
    max_val = comp.compile(expr.args[1]) if len(expr.args) == 2 else _value(comp.group, 1.0, x + 20, y - 120)
    id_val = None
    if "id" in kws:
        id_val = comp.compile(kws["id"])

    seed_val = None
    seed_default = None
    if "seed" in kws:
        seed_val = comp.compile(kws["seed"])
        if seed_val.typ != TYPE_INT:
            try:
                const_seed = _const_eval(kws["seed"], comp.consts)
                if isinstance(const_seed, bool) or not isinstance(const_seed, (int, float)):
                    raise CompileError("random_value(..., seed=...) expects Int")
                seed_default = int(const_seed)
            except CompileError as exc:
                raise CompileError("random_value(..., seed=...) expects Int") from exc

    return _random_value_node(
        comp.group,
        min_val,
        max_val,
        seed_val=seed_val,
        id_val=id_val,
        seed_default=seed_default,
        x=x,
        y=y,
    )


def _wire_or_default_numeric(comp, socket, expr, label):
    """Wire a numeric expression into a socket or set a compile-time default."""
    try:
        socket.default_value = float(_const_eval(expr, comp.consts))
        return
    except CompileError:
        pass
    value = comp.compile(expr)
    reject_compile_time_object(value, label)
    if not _is_number_type(value.typ):
        raise CompileError(f"{label} expects numeric input")
    comp.group.links.new(value.socket, socket)

_SPECS = {
    **{
        name: MathBuiltinSpec(
            name=name,
            params=("value",),
            compile_fn=_compile_unary_math(name, op),
        )
        for name, op in _FLOAT_FUNCS_1.items()
    },
    **{
        name: MathBuiltinSpec(
            name=name,
            params=("a", "b"),
            compile_fn=_compile_binary_math(name, op),
        )
        for name, op in _FLOAT_FUNCS_2.items()
    },
    "ln": MathBuiltinSpec("ln", ("value",), _compile_ln_spec),
    "clamp": MathBuiltinSpec("clamp", ("value", "min", "max"), _compile_clamp_spec),
    "mix": MathBuiltinSpec("mix", ("a", "b", "factor"), _compile_mix_spec),
    "select": MathBuiltinSpec("select", ("cond", "false", "true"), _compile_select_spec),
    "map_range": MathBuiltinSpec("map_range", ("value", "from_min", "from_max", "to_min", "to_max"), _compile_map_range_spec),
}
_CUSTOM_NAMES = {"noise", "random_value"}
NAMES = set(_SPECS) | _CUSTOM_NAMES

