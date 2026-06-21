"""Modular NodeForge function: koch_curve().

The function supports two modes:
- static: steps is a compile-time integer and only one curve level is generated;
- runtime: steps is an Int socket and levels 0..max_steps are prebuilt, then
  selected dynamically by switch nodes.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

import bpy

from ...constants import TYPE_INT
from ...errors import CompileError
from ...values import Value
from ...nodes import _new_node, _value, _compare, _switch
from ...interface import _set_interface_socket_default, _record_group_input_default
from ...storage import _reset_node_group, _store_group_source, INPUT_DEFAULTS_PROP
from ...statements import _kw_dict, _check_no_extra_keywords
from ...consteval import _const_eval, _as_float_const
from ...geometry import _polyline_geometry
from ...library import make_library_call_node


def _hash_data(data: Any) -> str:
    """Return a stable short hash for a function specialization."""
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:10]


def _user_facing_source(fallback: str) -> str:
    """Return editable source.nf text for Load Script, or fallback native metadata."""
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
        group["nodeforge_function_module"] = "koch_curve"
    except Exception:
        pass
    return group



def _koch_curve_points(steps, length=1.0):
    """Return compile-time points for a single Koch curve iteration count.

    This is deliberately local to the koch_curve function module so the core
    geometry module stays generic and does not know about individual fractals.
    """
    steps = int(steps)
    if steps < 0:
        raise CompileError("koch_curve steps must be >= 0")
    half = float(length) * 0.5
    pts = [(-half, 0.0, 0.0), (half, 0.0, 0.0)]
    peak_factor = 0.28867513459481287  # sqrt(3) / 6
    for _ in range(steps):
        new_pts = []
        for a, b in zip(pts, pts[1:]):
            ax, ay, az = a
            bx, by, bz = b
            vx, vy, vz = bx - ax, by - ay, bz - az
            p0 = a
            p1 = (ax + vx / 3.0, ay + vy / 3.0, az + vz / 3.0)
            p2 = (ax + vx / 2.0 - vy * peak_factor,
                  ay + vy / 2.0 + vx * peak_factor,
                  az + vz / 2.0)
            p3 = (ax + vx * 2.0 / 3.0, ay + vy * 2.0 / 3.0, az + vz * 2.0 / 3.0)
            new_pts.extend([p0, p1, p2, p3])
        new_pts.append(pts[-1])
        pts = new_pts
    return pts


def _koch_curve_geometry(group, steps=3, length=1.0, max_steps=5, x=0, y=0):
    """Build static or runtime-selectable Koch curve geometry.

    Constant steps generate only one baked polyline. Dynamic Int steps generate
    levels 0..max_steps and switch between them at runtime.
    """
    max_steps = int(max_steps)
    if max_steps < 0:
        raise CompileError("koch_curve max_steps must be >= 0")
    if max_steps > 7:
        raise CompileError("koch_curve max_steps must be <= 7")
    if isinstance(steps, Value):
        if steps.typ != TYPE_INT:
            raise CompileError("koch_curve steps= must be Int")
        levels = []
        for level in range(max_steps + 1):
            pts = _koch_curve_points(level, length)
            levels.append(_polyline_geometry(group, pts, x + level * 520, y - level * 120))
        current = levels[0]
        for level in range(1, max_steps + 1):
            cond = _compare(group, "GREATER_EQUAL", steps, _value(group, level, x + level * 520, y + 90), x + level * 520 + 220, y + 90)
            current = _switch(group, cond, current, levels[level], x + level * 520 + 420, y + 90)
        return current
    level = int(steps)
    if level < 0:
        level = 0
    if level > max_steps:
        level = max_steps
    return _polyline_geometry(group, _koch_curve_points(level, length), x, y)

def _compile_function_group(mode: str, steps, length: float, max_steps: int):
    """Compile the reusable node group backing koch_curve()."""
    data = {"mode": mode, "steps": None if isinstance(steps, Value) else int(steps), "length": float(length), "max_steps": int(max_steps)}
    h = _hash_data(data)
    group_name = f"NodeForge.fn.koch_curve.{mode}.{h}"
    source = (
        "NodeForge function module: koch_curve\n"
        f"mode={mode}\nlength={float(length)!r}\nmax_steps={int(max_steps)!r}\n"
    )
    group = _get_or_reset_function_group(group_name, source)
    if mode == "runtime":
        group.interface.new_socket(name="Steps", in_out="INPUT", socket_type="NodeSocketInt")
        _set_interface_socket_default(group, "Steps", "INPUT", 0)
        _record_group_input_default(group, "Steps", TYPE_INT, 0)
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_input = _new_node(group, "NodeGroupInput", -900, 0)
    group_output = _new_node(group, "NodeGroupOutput", 1100, 0)
    group_output.is_active_output = True
    if mode == "runtime":
        steps_socket = next((s for s in group_input.outputs if s.name == "Steps"), None)
        if steps_socket is None:
            raise CompileError("Internal error: koch_curve Steps input was not created")
        steps_value = Value(steps_socket, TYPE_INT)
        out = _koch_curve_geometry(group, steps=steps_value, length=length, max_steps=max_steps, x=-720, y=0)
    else:
        out = _koch_curve_geometry(group, steps=int(steps), length=length, max_steps=max_steps, x=-720, y=0)
    group.links.new(out.socket, group_output.inputs["Geometry"])
    return group


def compile_call(comp, expr: ast.Call, depth=0) -> Value:
    """Compile a caller-side koch_curve(...) expression."""
    kws = _kw_dict(expr)
    _check_no_extra_keywords(kws, {"steps", "length", "max_steps"})
    if len(expr.args) > 1:
        raise CompileError("koch_curve(steps=..., length=..., max_steps=...) expects 0 or 1 positional argument")
    steps_expr = kws.get("steps", expr.args[0] if expr.args else ast.Constant(value=3))
    length_expr = kws.get("length", ast.Constant(value=1.0))
    max_steps_expr = kws.get("max_steps", ast.Constant(value=5))
    length = _as_float_const(_const_eval(length_expr, comp.consts), "koch_curve length")
    max_steps = int(_as_float_const(_const_eval(max_steps_expr, comp.consts), "koch_curve max_steps"))
    if max_steps < 0 or max_steps > 7:
        raise CompileError("koch_curve max_steps must be between 0 and 7")
    try:
        steps = int(_as_float_const(_const_eval(steps_expr, comp.consts), "koch_curve steps"))
        mode = "static"
    except CompileError:
        steps = comp.compile(steps_expr)
        if steps.typ != TYPE_INT:
            raise CompileError("koch_curve steps= must be Int")
        mode = "runtime"
    function_group = _compile_function_group(mode, steps, length, max_steps)
    compiled_args = {"Steps": steps} if isinstance(steps, Value) else {}
    return make_library_call_node(comp.group, function_group, compiled_args, {}, x=depth * 240, y=-depth * 90)


def materialize_group(compile_group_callback=None):
    """Create the default reusable koch_curve node group for UI browsing/insertion."""
    return _compile_function_group("static", 3, 1.0, 5)
