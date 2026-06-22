"""Geometry built-ins for NodeForge DSL."""

import ast
from ..constants import TYPE_GEOMETRY
from ..errors import CompileError
from ..statements import _kw_dict, _check_no_extra_keywords, _selection_kw, _optional_string_kw
from ..consteval import _const_eval
from ..parsing import _literal_string
from ..compile_time import reject_compile_time_object
from ..geometry import (
    _points_geometry,
    _set_position_geometry,
    _cube_geometry,
    _join_geometry,
    _transform_geometry,
    _polyline_geometry,
    _grid_geometry,
    _store_named_attribute_geometry,
    _set_material_geometry,
)

NAMES = {"points", "grid", "grid_uv", "set_position", "store_named_attribute", "set_material", "cube", "join", "transform", "polyline"}


def compile_call(comp, expr, depth=0):
    """Compile geometry DSL built-in calls through the registry dispatcher."""
    name = expr.func.id
    x = depth * 240
    y = -depth * 90
    kws = _kw_dict(expr)

    if name == "points":
        if kws:
            raise CompileError("points() does not support keyword arguments")
        if len(expr.args) != 1:
            raise CompileError("points(count) expects one Int argument")
        try:
            count = _const_eval(expr.args[0], comp.consts)
        except CompileError:
            count = comp.compile(expr.args[0])
            reject_compile_time_object(count, "points() count")
        return _points_geometry(comp.group, count, x, y)

    if name == "grid":
        if kws:
            raise CompileError("grid() does not support keyword arguments")
        if len(expr.args) != 2:
            raise CompileError("grid(width, height) expects two Int arguments")
        width = _const_or_compile(comp, expr.args[0])
        height = _const_or_compile(comp, expr.args[1])
        geo, uv = _grid_geometry(comp.group, width, height, x, y)
        comp.grid_context = {"uv": uv}
        return geo

    if name == "grid_uv":
        if kws:
            raise CompileError("grid_uv() does not support keyword arguments")
        if expr.args:
            raise CompileError("grid_uv() expects no arguments")
        ctx = getattr(comp, "grid_context", None)
        if not ctx:
            raise CompileError("grid_uv() requires a preceding grid(width, height) call")
        return ctx["uv"]

    if name == "set_position":
        _check_no_extra_keywords(kws, {"selection"})
        if len(expr.args) != 2:
            raise CompileError("set_position(geo, position, selection=...) expects Geometry and Vector")
        geo = comp.compile(expr.args[0])
        pos = comp.compile(expr.args[1])
        selection = comp.compile(kws["selection"]) if "selection" in kws else None
        reject_compile_time_object(geo, "set_position() geometry")
        reject_compile_time_object(pos, "set_position() position")
        reject_compile_time_object(selection, "set_position() selection")
        return _set_position_geometry(comp.group, geo, pos, selection, x, y)

    if name == "store_named_attribute":
        _check_no_extra_keywords(kws, {"selection", "domain", "type"})
        if len(expr.args) != 3:
            raise CompileError('store_named_attribute(geometry, "name", value, selection=..., domain="POINT", type=...) expects 3 positional arguments')
        geo = comp.compile(expr.args[0])
        attr_name = _literal_string(expr.args[1], "store_named_attribute() name")
        value = comp.compile(expr.args[2])
        reject_compile_time_object(geo, "store_named_attribute() geometry")
        reject_compile_time_object(value, "store_named_attribute() value")
        if isinstance(value, list):
            raise CompileError("store_named_attribute() value cannot be an array")
        selection = _selection_kw(comp, kws)
        domain = _optional_string_kw(kws, "domain", "POINT")
        data_type_override = _optional_string_kw(kws, "type", None)
        return _store_named_attribute_geometry(comp.group, geo, attr_name, value, selection, domain, data_type_override, x, y)

    if name == "set_material":
        if kws:
            raise CompileError("set_material() does not support keyword arguments")
        if len(expr.args) != 2:
            raise CompileError('set_material(geometry, "MaterialName") expects Geometry and a compile-time material name')
        geo = comp.compile(expr.args[0])
        reject_compile_time_object(geo, "set_material() geometry")
        material_name = _literal_string(expr.args[1], "set_material() material name")
        return _set_material_geometry(comp.group, geo, material_name, x, y)

    if name == "cube":
        _check_no_extra_keywords(kws, {"size"})
        if len(expr.args) > 1:
            raise CompileError("cube(size) expects 0 or 1 positional argument")
        size_expr = expr.args[0] if expr.args else kws.get("size", ast.Constant(value=1.0))
        try:
            size = _const_eval(size_expr, comp.consts)
        except CompileError:
            size = comp.compile(size_expr)
        return _cube_geometry(comp.group, size, x, y)

    if name == "join":
        if kws:
            raise CompileError("join() does not support keyword arguments")
        if len(expr.args) < 1:
            raise CompileError("join([geo_a, geo_b, ...]) or join(geo_a, geo_b, ...) expects Geometry")
        geos = []
        if len(expr.args) == 1 and isinstance(expr.args[0], (ast.List, ast.Tuple)):
            geos = [comp.compile(arg) for arg in expr.args[0].elts]
            reject_compile_time_object(geos, "join() arguments")
        else:
            for arg in expr.args:
                val = comp.compile(arg)
                reject_compile_time_object(val, "join() argument")
                if isinstance(val, list):
                    geos.extend(val)
                else:
                    geos.append(val)
        if not geos:
            raise CompileError("join() expects at least one Geometry")
        return _join_geometry(comp.group, geos, x, y)

    if name == "transform":
        _check_no_extra_keywords(kws, {"translation", "scale", "rotation"})
        if not expr.args or len(expr.args) > 3:
            raise CompileError("transform(geo, translation=..., scale=..., rotation=...) expects Geometry")
        geo = comp.compile(expr.args[0])
        reject_compile_time_object(geo, "transform() geometry")
        if geo.typ != TYPE_GEOMETRY:
            raise CompileError("transform() first argument must be Geometry")
        translation_expr = kws.get("translation", expr.args[1] if len(expr.args) > 1 else None)
        scale_expr = kws.get("scale", expr.args[2] if len(expr.args) > 2 else None)
        rotation_expr = kws.get("rotation", None)
        translation = _const_or_compile(comp, translation_expr) if translation_expr is not None else None
        scale = _const_or_compile(comp, scale_expr) if scale_expr is not None else None
        rotation = _const_or_compile(comp, rotation_expr) if rotation_expr is not None else None
        return _transform_geometry(comp.group, geo, translation=translation, scale=scale, rotation=rotation, x=x, y=y)

    if name == "polyline":
        if kws:
            raise CompileError("polyline() does not support keyword arguments")
        if len(expr.args) != 1:
            raise CompileError("polyline(points) expects one compile-time list of vector points")
        points = comp._const_eval_macro_arg(expr.args[0])
        return _polyline_geometry(comp.group, points, x, y)

    raise CompileError(f"Unsupported geometry builtin: {name}")


def _const_or_compile(comp, expr):
    """Return a compile-time constant when possible, otherwise compile a node value."""
    try:
        return _const_eval(expr, comp.consts)
    except CompileError:
        value = comp.compile(expr)
        reject_compile_time_object(value, "geometry builtin argument")
        return value
