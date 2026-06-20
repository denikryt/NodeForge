"""Repeat Zone and runtime for-loop graph construction helpers."""

import ast
from .constants import *
from .errors import CompileError
from .values import Value
from .nodes import _new_node
from .consteval import _const_eval
from .geometry import _repeat_copy_by_offsets



def _parse_runtime_for(stmt, consts):
    """Function `_parse_runtime_for` used by the GN Script MVP addon."""
    if not isinstance(stmt.target, ast.Name):
        raise CompileError("runtime for target must be a simple name, e.g. for i in range(steps)")
    if not (isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id == "range" and len(stmt.iter.args) == 1):
        raise CompileError("runtime for must look like: for i in range(steps):")
    if len(stmt.body) != 1 or not isinstance(stmt.body[0], ast.Assign):
        raise CompileError("runtime for body currently supports one assignment")
    assign = stmt.body[0]
    if len(assign.targets) != 1 or not isinstance(assign.targets[0], ast.Name):
        raise CompileError("runtime for body assignment must target a simple geometry variable")
    return assign.targets[0].id, stmt.iter.args[0], assign.value

def _repeat_geometry_assignment(group, comp, geom_name, start_geo, iterations, body_expr, x=0, y=0):
    """Function `_repeat_geometry_assignment` used by the GN Script MVP addon."""
    if start_geo.typ != TYPE_GEOMETRY:
        raise CompileError("runtime for input must be Geometry")
    ri = _new_node(group, "GeometryNodeRepeatInput", x, y)
    ro = _new_node(group, "GeometryNodeRepeatOutput", x + 1120, y)
    if not ri.pair_with_output(ro):
        raise CompileError("Could not pair Repeat Zone nodes")
    group.links.new(iterations.socket, ri.inputs[0])
    group.links.new(start_geo.socket, ri.inputs[1])
    old = comp.vars.get(geom_name)
    comp.vars[geom_name] = Value(ri.outputs[1], TYPE_GEOMETRY)
    try:
        body = comp.compile(body_expr)
    finally:
        if old is None:
            comp.vars.pop(geom_name, None)
        else:
            comp.vars[geom_name] = old
    if body.typ != TYPE_GEOMETRY:
        raise CompileError("runtime for body must assign Geometry")
    group.links.new(body.socket, ro.inputs[0])
    return Value(ro.outputs[0], TYPE_GEOMETRY)

__all__ = ['_parse_runtime_for', '_repeat_geometry_assignment']
