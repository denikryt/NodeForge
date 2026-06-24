"""Point layout built-ins for NodeForge DSL."""

from ..constants import TYPE_GEOMETRY, TYPE_VECTOR
from ..errors import CompileError
from ..statements import _kw_dict, _check_no_extra_keywords
from ..consteval import _const_eval, _is_const_vector
from ..compile_time import reject_compile_time_object
from ..geometry import (
    _layout_grid_geometry,
    _grid_points_geometry,
    _layout_circle_geometry,
    _circle_points_geometry,
    _layout_spiral_geometry,
    _spiral_points_geometry,
    _layout_random_geometry,
    _random_points_geometry,
)
from .math import _random_value_node
from ..nodes import _is_number_type

NAMES = {
    "layout_grid",
    "grid_points",
    "layout_circle",
    "circle_points",
    "layout_spiral",
    "spiral_points",
    "layout_random",
    "random_points",
}

_MISSING = object()


def compile_call(comp, expr, depth=0):
    """Compile point layout helper calls through the built-in registry."""
    name = expr.func.id
    x = depth * 240
    y = -depth * 90
    if name == "layout_grid":
        args = _bind_args(expr, name, ("geometry", "count", "spacing", "centered"), {"spacing": 1.0, "centered": False})
        geo = _compile_value(comp, args["geometry"], "layout_grid() geometry")
        count = _compile_grid_count(comp, args["count"], "layout_grid() count", allow_zero=False)
        spacing = _compile_or_const(comp, args["spacing"], "layout_grid() spacing")
        centered = _compile_bool_const(comp, args["centered"], "layout_grid() centered")
        return _layout_grid_geometry(comp.group, geo, count, spacing, centered, x, y)

    if name == "grid_points":
        args = _bind_args(expr, name, ("count", "spacing", "centered"), {"spacing": 1.0, "centered": False})
        count = _compile_grid_count(comp, args["count"], "grid_points() count", allow_zero=True)
        spacing = _compile_or_const(comp, args["spacing"], "grid_points() spacing")
        centered = _compile_bool_const(comp, args["centered"], "grid_points() centered")
        return _grid_points_geometry(comp.group, count, spacing, centered, x, y)

    if name == "layout_circle":
        args = _bind_args(
            expr,
            name,
            ("geometry", "count", "radius", "start_angle", "end_angle", "include_endpoint"),
            {"radius": 1.0, "start_angle": 0.0, "end_angle": 6.283185307179586, "include_endpoint": False},
        )
        geo = _compile_value(comp, args["geometry"], "layout_circle() geometry")
        count = _compile_scalar_count(comp, args["count"], "layout_circle() count", reject_negative=True)
        radius = _compile_numeric(comp, args["radius"], "layout_circle() radius")
        start_angle = _compile_numeric(comp, args["start_angle"], "layout_circle() start_angle")
        end_angle = _compile_numeric(comp, args["end_angle"], "layout_circle() end_angle")
        include_endpoint = _compile_bool_const(comp, args["include_endpoint"], "layout_circle() include_endpoint")
        return _layout_circle_geometry(comp.group, geo, count, radius, start_angle, end_angle, include_endpoint, x, y)

    if name == "circle_points":
        args = _bind_args(
            expr,
            name,
            ("count", "radius", "start_angle", "end_angle", "include_endpoint"),
            {"radius": 1.0, "start_angle": 0.0, "end_angle": 6.283185307179586, "include_endpoint": False},
        )
        count = _compile_scalar_count(comp, args["count"], "circle_points() count", reject_negative=True)
        radius = _compile_numeric(comp, args["radius"], "circle_points() radius")
        start_angle = _compile_numeric(comp, args["start_angle"], "circle_points() start_angle")
        end_angle = _compile_numeric(comp, args["end_angle"], "circle_points() end_angle")
        include_endpoint = _compile_bool_const(comp, args["include_endpoint"], "circle_points() include_endpoint")
        return _circle_points_geometry(comp.group, count, radius, start_angle, end_angle, include_endpoint, x, y)

    if name == "layout_spiral":
        args = _bind_args(
            expr,
            name,
            ("geometry", "count", "radius", "turns", "height", "start_radius", "start_angle"),
            {"radius": 1.0, "turns": 1.0, "height": 0.0, "start_radius": 0.0, "start_angle": 0.0},
        )
        geo = _compile_value(comp, args["geometry"], "layout_spiral() geometry")
        count = _compile_scalar_count(comp, args["count"], "layout_spiral() count", reject_negative=True)
        radius = _compile_numeric(comp, args["radius"], "layout_spiral() radius")
        turns = _compile_numeric(comp, args["turns"], "layout_spiral() turns")
        height = _compile_numeric(comp, args["height"], "layout_spiral() height")
        start_radius = _compile_numeric(comp, args["start_radius"], "layout_spiral() start_radius")
        start_angle = _compile_numeric(comp, args["start_angle"], "layout_spiral() start_angle")
        return _layout_spiral_geometry(comp.group, geo, count, radius, turns, height, start_radius, start_angle, x, y)

    if name == "spiral_points":
        args = _bind_args(
            expr,
            name,
            ("count", "radius", "turns", "height", "start_radius", "start_angle"),
            {"radius": 1.0, "turns": 1.0, "height": 0.0, "start_radius": 0.0, "start_angle": 0.0},
        )
        count = _compile_scalar_count(comp, args["count"], "spiral_points() count", reject_negative=True)
        radius = _compile_numeric(comp, args["radius"], "spiral_points() radius")
        turns = _compile_numeric(comp, args["turns"], "spiral_points() turns")
        height = _compile_numeric(comp, args["height"], "spiral_points() height")
        start_radius = _compile_numeric(comp, args["start_radius"], "spiral_points() start_radius")
        start_angle = _compile_numeric(comp, args["start_angle"], "spiral_points() start_angle")
        return _spiral_points_geometry(comp.group, count, radius, turns, height, start_radius, start_angle, x, y)

    if name == "layout_random":
        args = _bind_args(expr, name, ("geometry", "min", "max", "seed"), {"min": (-1.0, -1.0, -1.0), "max": (1.0, 1.0, 1.0), "seed": 0})
        geo = _compile_value(comp, args["geometry"], "layout_random() geometry")
        min_vec = _compile_vector(comp, args["min"], "layout_random() min")
        max_vec = _compile_vector(comp, args["max"], "layout_random() max")
        seed = _compile_seed(comp, args["seed"], "layout_random() seed")
        return _layout_random_geometry(comp.group, geo, min_vec, max_vec, seed, _random_value_node, x, y)

    if name == "random_points":
        args = _bind_args(expr, name, ("count", "min", "max", "seed"), {"min": (-1.0, -1.0, -1.0), "max": (1.0, 1.0, 1.0), "seed": 0})
        count = _compile_scalar_count(comp, args["count"], "random_points() count", reject_negative=True)
        min_vec = _compile_vector(comp, args["min"], "random_points() min")
        max_vec = _compile_vector(comp, args["max"], "random_points() max")
        seed = _compile_seed(comp, args["seed"], "random_points() seed")
        return _random_points_geometry(comp.group, count, min_vec, max_vec, seed, _random_value_node, x, y)

    raise CompileError(f"Unsupported layout builtin: {name}")


def _bind_args(expr, name, params, defaults):
    kws = _kw_dict(expr)
    _check_no_extra_keywords(kws, params)
    if len(expr.args) > len(params):
        raise CompileError(f"{name}() got too many positional arguments")
    result = {}
    for index, arg in enumerate(expr.args):
        result[params[index]] = arg
    for key, value in kws.items():
        if key in result:
            raise CompileError(f"{name}() got multiple values for argument {key!r}")
        result[key] = value
    for param in params:
        if param not in result:
            if param in defaults:
                result[param] = defaults[param]
            else:
                raise CompileError(f"{name}() missing argument: {param}")
    return result


def _compile_or_const(comp, expr_or_value, label):
    if not hasattr(expr_or_value, "_fields") and not hasattr(expr_or_value, "lineno"):
        return expr_or_value
    try:
        return _const_eval(expr_or_value, comp.consts)
    except CompileError:
        value = comp.compile(expr_or_value)
        reject_compile_time_object(value, label)
        return value


def _compile_value(comp, expr, label):
    value = comp.compile(expr)
    reject_compile_time_object(value, label)
    return value


def _compile_bool_const(comp, expr_or_value, label):
    if isinstance(expr_or_value, bool):
        return expr_or_value
    try:
        value = _const_eval(expr_or_value, comp.consts)
    except CompileError as exc:
        raise CompileError(f"{label} must be a compile-time bool") from exc
    if not isinstance(value, bool):
        raise CompileError(f"{label} must be a compile-time bool")
    return value


def _compile_grid_count(comp, expr, label, *, allow_zero):
    try:
        value = _const_eval(expr, comp.consts)
    except CompileError:
        compiled = comp.compile(expr)
        reject_compile_time_object(compiled, label)
        if compiled.typ != TYPE_VECTOR:
            raise CompileError(f"{label} expects Vector")
        return compiled
    if _is_const_vector(value) or _is_plain_vector(value):
        components = tuple(float(v) for v in value)
        if any(not component.is_integer() for component in components):
            raise CompileError(f"{label} components must be whole numbers")
        if any(component < 0 for component in components):
            raise CompileError(f"{label} cannot contain negative components")
        if not allow_zero and any(component == 0 for component in components):
            raise CompileError(f"{label} components must be greater than zero")
        return components
    raise CompileError(f"{label} expects Vector")


def _compile_scalar_count(comp, expr, label, *, reject_negative):
    try:
        value = _const_eval(expr, comp.consts)
    except CompileError:
        compiled = comp.compile(expr)
        reject_compile_time_object(compiled, label)
        if not _is_number_type(compiled.typ):
            raise CompileError(f"{label} expects Float/Int")
        return compiled
    if not _is_const_number(value):
        raise CompileError(f"{label} expects Float/Int")
    if reject_negative and value < 0:
        raise CompileError(f"{label} cannot be negative")
    return value


def _compile_numeric(comp, expr_or_value, label):
    if _is_const_number(expr_or_value):
        return expr_or_value
    if _is_plain_vector(expr_or_value):
        raise CompileError(f"{label} expects Float/Int")
    try:
        value = _const_eval(expr_or_value, comp.consts)
    except CompileError:
        compiled = comp.compile(expr_or_value)
        reject_compile_time_object(compiled, label)
        if not _is_number_type(compiled.typ):
            raise CompileError(f"{label} expects Float/Int")
        return compiled
    if not _is_const_number(value):
        raise CompileError(f"{label} expects Float/Int")
    return value


def _compile_vector(comp, expr_or_value, label):
    if _is_plain_vector(expr_or_value):
        return tuple(float(v) for v in expr_or_value)
    try:
        value = _const_eval(expr_or_value, comp.consts)
    except CompileError:
        compiled = comp.compile(expr_or_value)
        reject_compile_time_object(compiled, label)
        if compiled.typ != TYPE_VECTOR:
            raise CompileError(f"{label} expects Vector")
        return compiled
    if _is_const_vector(value) or _is_plain_vector(value):
        return tuple(float(v) for v in value)
    raise CompileError(f"{label} expects Vector")


def _compile_seed(comp, expr_or_value, label):
    if _is_const_number(expr_or_value):
        return int(expr_or_value)
    try:
        value = _const_eval(expr_or_value, comp.consts)
    except CompileError:
        compiled = comp.compile(expr_or_value)
        reject_compile_time_object(compiled, label)
        return compiled
    if not _is_const_number(value):
        raise CompileError(f"{label} expects Int")
    return int(value)


def _is_const_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_plain_vector(value):
    return (
        isinstance(value, (tuple, list))
        and len(value) == 3
        and all(_is_const_number(component) for component in value)
    )
