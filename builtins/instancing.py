"""Instancing built-ins for NodeForge DSL."""

from ..constants import TYPE_GEOMETRY
from ..errors import CompileError
from ..statements import _kw_dict, _check_no_extra_keywords
from ..consteval import _const_eval
from ..geometry import _instance_on_points, _realize_instances

NAMES = {"instance_on_points", "realize_instances"}


def compile_call(comp, expr, depth=0):
    """Compile instancing DSL built-ins through the registry dispatcher."""
    name = expr.func.id
    x = depth * 240
    y = -depth * 90
    kws = _kw_dict(expr)

    if name == "instance_on_points":
        _check_no_extra_keywords(kws, {"scale", "rotation", "realize"})
        if len(expr.args) != 2:
            raise CompileError("instance_on_points(instance, points, ...) expects two Geometry arguments")
        instance = comp.compile(expr.args[0])
        points_geo = comp.compile(expr.args[1])
        if instance.typ != TYPE_GEOMETRY or points_geo.typ != TYPE_GEOMETRY:
            raise CompileError("instance_on_points(instance, points, ...) expects two Geometry arguments")
        scale = _const_or_compile(comp, kws["scale"]) if "scale" in kws else None
        rotation = _const_or_compile(comp, kws["rotation"]) if "rotation" in kws else None
        realize = True
        if "realize" in kws:
            try:
                realize = bool(_const_eval(kws["realize"], comp.consts))
            except CompileError:
                raise CompileError("instance_on_points realize= must be a compile-time bool")
        return _instance_on_points(comp.group, instance, points_geo, scale=scale, rotation=rotation, realize=realize, x=x, y=y)

    if name == "realize_instances":
        if kws:
            raise CompileError("realize_instances() does not support keyword arguments")
        if len(expr.args) != 1:
            raise CompileError("realize_instances(geo) expects one Geometry")
        geo = comp.compile(expr.args[0])
        return _realize_instances(comp.group, geo, x, y)

    raise CompileError(f"Unsupported instancing builtin: {name}")


def _const_or_compile(comp, expr):
    """Return a compile-time option value or compile a dynamic node value."""
    try:
        return _const_eval(expr, comp.consts)
    except CompileError:
        return comp.compile(expr)
