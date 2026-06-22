"""Modular NodeForge function: dragon_curve().

This package builds Dragon Curve geometry using generated mesh datablocks and a
small Geometry Nodes wrapper. It avoids expanding every polyline point into
individual Vector/Curve nodes in the node graph.

Modes:
- static: steps is a compile-time integer; one mesh level is generated;
- runtime: steps is an Int socket; levels 0..max_steps are generated as mesh
  datablocks and selected with Switch nodes at evaluation time.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

import bpy

from ...constants import TYPE_INT, TYPE_GEOMETRY
from ...errors import CompileError
from ...values import Value
from ...compile_time import reject_compile_time_object
from ...nodes import _new_node, _value, _compare, _switch
from ...interface import _set_interface_socket_default, _record_group_input_default
from ...storage import _reset_node_group, _store_group_source, INPUT_DEFAULTS_PROP
from ...statements import _kw_dict, _check_no_extra_keywords
from ...consteval import _const_eval, _as_float_const
from ...library import make_library_call_node


def _hash_data(data: Any) -> str:
    """Return a stable short hash for a function specialization."""
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:10]


def _user_facing_source(fallback: str) -> str:
    """Return editable source.nf text for Load Script."""
    path = Path(__file__).with_name("source.nf")
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception:
        pass
    return fallback


def _get_or_reset_function_group(name: str, source: str):
    """Create or clear the reusable GeometryNodeTree backing this function."""
    group = bpy.data.node_groups.get(name)
    if group is None or getattr(group, "bl_idname", None) != "GeometryNodeTree":
        group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    else:
        _reset_node_group(group)
    try:
        group[INPUT_DEFAULTS_PROP] = {}
    except Exception:
        pass
    group.color_tag = "CONVERTER"
    _store_group_source(group, _user_facing_source(source))
    try:
        group["nodeforge_function_module"] = "dragon_curve"
        group["nodeforge_function_backend"] = "mesh_datablock"
    except Exception:
        pass
    return group


def _dragon_curve_points(steps: int, length: float = 1.0):
    """Return point coordinates for a Heighway Dragon polyline."""
    steps = int(steps)
    if steps < 0:
        raise CompileError("dragon_curve steps must be >= 0")
    pts = [(0.0, 0.0, 0.0), (float(length), 0.0, 0.0)]
    for _ in range(steps):
        pivot = pts[-1]
        px, py, pz = pivot
        new_pts = list(pts)
        # Skip the pivot, walk backwards, rotate +90 degrees around it.
        for p in reversed(pts[:-1]):
            vx = p[0] - px
            vy = p[1] - py
            vz = p[2] - pz
            new_pts.append((px - vy, py + vx, pz + vz))
        pts = new_pts
    # Center roughly around its bounding box so the object is easier to view.
    min_x = min(p[0] for p in pts); max_x = max(p[0] for p in pts)
    min_y = min(p[1] for p in pts); max_y = max(p[1] for p in pts)
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    return [(x - cx, y - cy, z) for x, y, z in pts]


def _mesh_object_for_level(level: int, length: float):
    """Create or reuse a hidden mesh object for a Dragon Curve level."""
    data = {"function": "dragon_curve", "level": int(level), "length": float(length)}
    h = _hash_data(data)
    base_name = f"NodeForge.asset.dragon_curve.{int(level)}.{h}"
    obj = bpy.data.objects.get(base_name)
    if obj is not None and obj.type == "MESH":
        return obj

    pts = _dragon_curve_points(level, length)
    edges = [(i, i + 1) for i in range(len(pts) - 1)]
    mesh = bpy.data.meshes.new(base_name + ".mesh")
    mesh.from_pydata(pts, edges, [])
    mesh.update()
    obj = bpy.data.objects.new(base_name, mesh)
    obj.hide_select = True
    obj.hide_viewport = True
    obj.hide_render = True
    try:
        obj["nodeforge_asset"] = "dragon_curve"
        obj["nodeforge_asset_level"] = int(level)
        obj["nodeforge_asset_points"] = len(pts)
    except Exception:
        pass
    # Do not link into the scene collection. Object Info can hold a direct ID
    # reference, and this avoids cluttering the user's scene/outliner.
    return obj


def _object_geometry(group, obj, x=0, y=0):
    """Create an Object Info node returning the object's geometry."""
    node = _new_node(group, "GeometryNodeObjectInfo", x, y)
    node.label = obj.name
    try:
        node.inputs["Object"].default_value = obj
    except Exception:
        node.inputs[0].default_value = obj
    try:
        node.inputs["As Instance"].default_value = False
    except Exception:
        pass
    # Outputs: Transform, Location, Rotation, Scale, Geometry
    return Value(node.outputs["Geometry"], TYPE_GEOMETRY)


def _dragon_curve_geometry(group, steps=8, length=1.0, max_steps=12, x=0, y=0):
    """Build static or runtime-selectable Dragon Curve geometry wrapper."""
    max_steps = int(max_steps)
    if max_steps < 0:
        raise CompileError("dragon_curve max_steps must be >= 0")
    if isinstance(steps, Value):
        if steps.typ != TYPE_INT:
            raise CompileError("dragon_curve steps= must be Int")
        levels = []
        for level in range(max_steps + 1):
            obj = _mesh_object_for_level(level, length)
            levels.append(_object_geometry(group, obj, x + level * 360, y - level * 110))
        current = levels[0]
        for level in range(1, max_steps + 1):
            cond = _compare(group, "GREATER_EQUAL", steps, _value(group, level, x + level * 360, y + 120), x + level * 360 + 150, y + 120)
            current = _switch(group, cond, current, levels[level], x + level * 360 + 320, y + 120)
        return current

    level = int(steps)
    if level < 0:
        level = 0
    if level > max_steps:
        level = max_steps
    return _object_geometry(group, _mesh_object_for_level(level, length), x, y)


def _compile_function_group(mode: str, steps, length: float, max_steps: int):
    """Compile the reusable node group backing dragon_curve()."""
    data = {
        "mode": mode,
        "steps": None if mode == "runtime" or isinstance(steps, Value) else int(steps),
        "length": float(length),
        "max_steps": int(max_steps),
        "backend": "mesh_datablock_v1",
    }
    h = _hash_data(data)
    group_name = f"NodeForge.fn.dragon_curve.{mode}.{h}"
    source = (
        "NodeForge function module: dragon_curve\n"
        f"mode={mode}\nlength={float(length)!r}\nmax_steps={int(max_steps)!r}\n"
    )
    group = _get_or_reset_function_group(group_name, source)

    if mode == "runtime":
        group.interface.new_socket(name="Steps", in_out="INPUT", socket_type="NodeSocketInt")
        _set_interface_socket_default(group, "Steps", "INPUT", 8)
        _record_group_input_default(group, "Steps", TYPE_INT, 8)

    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_input = _new_node(group, "NodeGroupInput", -700, 0)
    group_output = _new_node(group, "NodeGroupOutput", 1000, 0)
    group_output.is_active_output = True

    if mode == "runtime":
        steps_socket = next((s for s in group_input.outputs if s.name == "Steps"), None)
        if steps_socket is None:
            raise CompileError("Internal error: dragon_curve Steps input was not created")
        steps_value = Value(steps_socket, TYPE_INT)
        out = _dragon_curve_geometry(group, steps=steps_value, length=length, max_steps=max_steps, x=-520, y=0)
    else:
        out = _dragon_curve_geometry(group, steps=int(steps), length=length, max_steps=max_steps, x=-520, y=0)

    group.links.new(out.socket, group_output.inputs["Geometry"])
    return group


def compile_call(comp, expr: ast.Call, depth=0) -> Value:
    """Compile a caller-side dragon_curve(...) expression."""
    kws = _kw_dict(expr)
    _check_no_extra_keywords(kws, {"steps", "length", "max_steps"})
    if len(expr.args) > 1:
        raise CompileError("dragon_curve(steps=..., length=..., max_steps=...) expects 0 or 1 positional argument")

    steps_expr = kws.get("steps", expr.args[0] if expr.args else ast.Constant(value=8))
    length_expr = kws.get("length", ast.Constant(value=1.0))
    max_steps_expr = kws.get("max_steps", ast.Constant(value=12))

    length = _as_float_const(_const_eval(length_expr, comp.consts), "dragon_curve length")
    max_steps = int(_as_float_const(_const_eval(max_steps_expr, comp.consts), "dragon_curve max_steps"))
    if max_steps < 0:
        raise CompileError("dragon_curve max_steps must be >= 0")

    try:
        steps = int(_as_float_const(_const_eval(steps_expr, comp.consts), "dragon_curve steps"))
        mode = "static"
    except CompileError:
        steps = comp.compile(steps_expr)
        reject_compile_time_object(steps, f"{expr.func.id}() steps")
        if steps.typ != TYPE_INT:
            raise CompileError("dragon_curve steps= must be Int")
        mode = "runtime"

    function_group = _compile_function_group(mode, steps, length, max_steps)
    compiled_args = {"Steps": steps} if isinstance(steps, Value) else {}
    return make_library_call_node(comp.group, function_group, compiled_args, {}, x=depth * 240, y=-depth * 90)


def materialize_group(compile_group_callback=None):
    """Create the default runtime Dragon Curve node group for UI insertion.

    Native function packages return their implementation group directly. This
    avoids creating an extra editable-source wrapper group around the native
    backend when the user presses Add Node Group in the Function Library UI.
    """
    group = _compile_function_group("runtime", None, 1.0, 12)
    try:
        group["nodeforge_native_direct_group"] = True
        group["nodeforge_function_kind"] = "native-package"
        group.description = (
            "NodeForge dragon_curve native backend. Default max_steps=12. "
            "No Python hard limit is enforced; high values generate very large mesh assets. "
            "At max_steps=20 the largest level has 1,048,577 vertices."
        )
    except Exception:
        pass
    return group
