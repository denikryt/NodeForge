"""Repeat Zone and runtime for-loop graph construction helpers."""

import ast
from .constants import *
from .errors import CompileError
from .values import Value
from .nodes import _new_node, _socket_type_for


def _is_range_call(stmt, name):
    return isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id == name


def _parse_runtime_for(stmt, consts):
    """Parse legacy geometry runtime loops: for i in range(steps): geo = ..."""
    if not isinstance(stmt.target, ast.Name):
        raise CompileError("runtime for target must be a simple name, e.g. for i in range(steps)")
    if not (_is_range_call(stmt, "range") and len(stmt.iter.args) == 1):
        raise CompileError("runtime for must look like: for i in range(steps):")
    if len(stmt.body) != 1 or not isinstance(stmt.body[0], ast.Assign):
        raise CompileError("runtime for body currently supports one assignment")
    assign = stmt.body[0]
    if len(assign.targets) != 1 or not isinstance(assign.targets[0], ast.Name):
        raise CompileError("runtime for body assignment must target a simple geometry variable")
    return assign.targets[0].id, stmt.iter.args[0], assign.value


def _parse_runtime_range_for(stmt):
    """Parse scalar runtime loops: for i in runtime_range(n): ..."""
    if not isinstance(stmt.target, ast.Name):
        raise CompileError("runtime_range target must be a simple name, e.g. for i in runtime_range(n)")
    if not (_is_range_call(stmt, "runtime_range") and len(stmt.iter.args) == 1):
        raise CompileError("runtime_range loop must look like: for i in runtime_range(n):")
    if not stmt.body:
        raise CompileError("runtime_range loop body cannot be empty")
    for sub in stmt.body:
        if not isinstance(sub, ast.Assign) or len(sub.targets) != 1 or not isinstance(sub.targets[0], ast.Name):
            raise CompileError("runtime_range body currently supports simple assignments only")
    return stmt.iter.args[0], stmt.body


def _repeat_item_type_for_value(value):
    if value.typ == TYPE_GEOMETRY:
        return "GEOMETRY"
    if value.typ == TYPE_VECTOR:
        return "VECTOR"
    if value.typ == TYPE_BOOL:
        return "BOOLEAN"
    if value.typ == TYPE_INT:
        return "INT"
    return "FLOAT"


def _socket_by_name(sockets, name):
    for sock in sockets:
        if sock.name == name:
            return sock
    raise CompileError(f"Internal error: missing Repeat Zone socket {name!r}")


def _remove_default_repeat_items(repeat_output):
    # New Repeat Zones start with a Geometry item. Remove it when building scalar loops.
    try:
        for item in list(repeat_output.repeat_items):
            repeat_output.repeat_items.remove(item)
    except Exception:
        pass


def _repeat_geometry_assignment(group, comp, geom_name, start_geo, iterations, body_expr, x=0, y=0):
    """Compile legacy geometry assignment loop into a Repeat Zone."""
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


def _repeat_scalar_assignments(group, comp, iterations, body_stmts, index_name=None, x=0, y=0):
    """Compile runtime_range loop with multiple scalar/vector state variables.

    Variables that exist before the loop and are assigned inside the body become
    Repeat Zone state sockets. New variables assigned inside the loop are local
    to one iteration and may be used by later assignments in the same body.
    """
    if iterations.typ != TYPE_INT:
        raise CompileError("runtime_range(n) expects an Int value")

    assigned_names = [sub.targets[0].id for sub in body_stmts]
    state_names = []
    for name in assigned_names:
        if name in comp.vars and name not in state_names:
            state_names.append(name)
    if not state_names:
        raise CompileError("runtime_range loop must update at least one existing variable")

    for name in state_names:
        if comp.vars[name].typ == TYPE_GEOMETRY:
            raise CompileError("runtime_range is for scalar/vector state; use for i in range(...) for Geometry")

    ri = _new_node(group, "GeometryNodeRepeatInput", x, y)
    ro = _new_node(group, "GeometryNodeRepeatOutput", x + 1120, y)
    if not ri.pair_with_output(ro):
        raise CompileError("Could not pair Repeat Zone nodes")
    _remove_default_repeat_items(ro)

    # Add state sockets.
    for name in state_names:
        ro.repeat_items.new(_repeat_item_type_for_value(comp.vars[name]), name)

    group.links.new(iterations.socket, ri.inputs[0])
    for name in state_names:
        group.links.new(comp.vars[name].socket, _socket_by_name(ri.inputs, name))

    old_vars = dict(comp.vars)
    loop_index_name = None
    # The caller sets the index variable in comp.vars if it wants to expose it.
    try:
        for name in state_names:
            comp.vars[name] = Value(_socket_by_name(ri.outputs, name), old_vars[name].typ)
        if index_name:
            comp.vars[index_name] = Value(ri.outputs[0], TYPE_INT)
        for sub in body_stmts:
            target = sub.targets[0].id
            comp.vars[target] = comp.compile(sub.value)
        for name in state_names:
            val = comp.vars.get(name)
            if val is None:
                raise CompileError(f"runtime_range state {name!r} was not assigned")
            if val.typ != old_vars[name].typ:
                # Numeric INT->FLOAT promotion is acceptable because Math nodes output Float.
                if old_vars[name].typ == TYPE_INT and val.typ == TYPE_FLOAT:
                    pass
                else:
                    raise CompileError(f"runtime_range state {name!r} changed type from {old_vars[name].typ} to {val.typ}")
            group.links.new(val.socket, _socket_by_name(ro.inputs, name))
    finally:
        comp.vars.clear()
        comp.vars.update(old_vars)

    result = {}
    for name in state_names:
        old_type = old_vars[name].typ
        out_type = TYPE_FLOAT if old_type == TYPE_INT else old_type
        result[name] = Value(_socket_by_name(ro.outputs, name), out_type)
        comp.vars[name] = result[name]
    return result


__all__ = [
    '_parse_runtime_for', '_parse_runtime_range_for',
    '_repeat_geometry_assignment', '_repeat_scalar_assignments'
]
