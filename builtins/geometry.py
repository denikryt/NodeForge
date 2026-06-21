"""Geometry built-ins for NodeForge DSL."""

import ast
from ..constants import TYPE_GEOMETRY
from ..errors import CompileError
from ..statements import _kw_dict, _check_no_extra_keywords
from ..consteval import _const_eval
from ..geometry import (
    _points_geometry,
    _set_position_geometry,
    _cube_geometry,
    _join_geometry,
    _transform_geometry,
    _polyline_geometry,
)

NAMES = {"points", "set_position", "cube", "join", "transform", "polyline"}


def compile_call(comp, expr, depth=0):
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
        return _points_geometry(comp.group, count, x, y)

    if name == "set_position":
        _check_no_extra_keywords(kws, {"selection"})
        if len(expr.args) != 2:
            raise CompileError("set_position(geo, position, selection=...) expects Geometry and Vector")
        geo = comp.compile(expr.args[0])
        pos = comp.compile(expr.args[1])
        selection = comp.compile(kws["selection"]) if "selection" in kws else None
        return _set_position_geometry(comp.group, geo, pos, selection, x, y)

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
        else:
            for arg in expr.args:
                val = comp.compile(arg)
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
    try:
        return _const_eval(expr, comp.consts)
    except CompileError:
        return comp.compile(expr)
