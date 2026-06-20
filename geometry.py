"""Geometry macro implementations such as cube(), transform(), join(), and copy_by_offsets()."""

from .constants import *
from .errors import CompileError
from .values import Value
from .nodes import _new_node, _value, _combine_xyz, _combine_xyz_mixed, _is_number_type, _vector_math
from .consteval import _is_const_vector, _as_float_const



def _normalize_offsets(offsets):
    """Function `_normalize_offsets` used by the GN Script MVP addon."""
    if not isinstance(offsets, list) or not offsets:
        raise CompileError("offsets must be a non-empty compile-time list")
    out = []
    for v in offsets:
        if _is_const_vector(v):
            out.append(tuple(float(c) for c in v))
        elif isinstance(v, (tuple, list)) and len(v) == 3:
            out.append(tuple(_as_float_const(c, "offset component") for c in v))
        else:
            raise CompileError("offsets must contain vector(x,y,z) values")
    return out

def _set_vector_socket_default(socket, vec):
    """Function `_set_vector_socket_default` used by the GN Script MVP addon."""
    try:
        socket.default_value = (float(vec[0]), float(vec[1]), float(vec[2]))
    except Exception:
        socket.default_value[0] = float(vec[0]); socket.default_value[1] = float(vec[1]); socket.default_value[2] = float(vec[2])

def _set_rotation_socket_default(socket, vec):
    """Set a Rotation/Euler socket default from a 3-component radians vector."""
    try:
        socket.default_value = (float(vec[0]), float(vec[1]), float(vec[2]))
    except Exception:
        socket.default_value[0] = float(vec[0]); socket.default_value[1] = float(vec[1]); socket.default_value[2] = float(vec[2])

def _euler_to_rotation(group, euler_vec, x=0, y=0):
    """Convert a Vector/Euler value into a Rotation socket value for Transform Geometry."""
    if not isinstance(euler_vec, Value) or euler_vec.typ != TYPE_VECTOR:
        raise CompileError("rotation= must be Vector")
    node = _new_node(group, "FunctionNodeEulerToRotation", x, y)
    group.links.new(euler_vec.socket, node.inputs[0])
    return Value(node.outputs[0], "ROTATION")

def _is_const_number(v):
    """Function `_is_const_number` used by the GN Script MVP addon."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)

def _is_const_vector_like(v):
    """Function `_is_const_vector_like` used by the GN Script MVP addon."""
    return _is_const_vector(v) or (isinstance(v, (tuple, list)) and len(v) == 3 and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in v))

def _cube_geometry(group, size, x=0, y=0):
    """Function `_cube_geometry` used by the GN Script MVP addon."""
    node = _new_node(group, "GeometryNodeMeshCube", x, y)
    for i in [1, 2, 3]:
        try: node.inputs[i].default_value = 2
        except Exception: pass
    if _is_const_number(size):
        _set_vector_socket_default(node.inputs[0], (size, size, size))
    elif _is_const_vector_like(size):
        _set_vector_socket_default(node.inputs[0], size)
    elif isinstance(size, Value) and size.typ == TYPE_VECTOR:
        group.links.new(size.socket, node.inputs[0])
    elif isinstance(size, Value) and _is_number_type(size.typ):
        v = _combine_xyz(group, size, size, size, x - 180, y - 60)
        group.links.new(v.socket, node.inputs[0])
    else:
        raise CompileError("cube(size) expects Float/Int or Vector size")
    return Value(node.outputs[0], TYPE_GEOMETRY)

def _join_geometry(group, geos, x=0, y=0):
    """Function `_join_geometry` used by the GN Script MVP addon."""
    if len(geos) == 1:
        return geos[0]
    node = _new_node(group, "GeometryNodeJoinGeometry", x, y)
    for geo in geos:
        if geo.typ != TYPE_GEOMETRY:
            raise CompileError("join() expects Geometry arguments")
        group.links.new(geo.socket, node.inputs[0])
    return Value(node.outputs[0], TYPE_GEOMETRY)

def _normalize_points(points):
    """Return a compile-time list of 3D point tuples for polyline()."""
    if not isinstance(points, list) or len(points) < 2:
        raise CompileError("polyline(points) expects a compile-time list with at least 2 vector points")
    out = []
    for p in points:
        if _is_const_vector(p):
            out.append(tuple(float(c) for c in p))
        elif isinstance(p, (tuple, list)) and len(p) == 3:
            out.append(tuple(_as_float_const(c, "point component") for c in p))
        else:
            raise CompileError("polyline(points) expects vector(x,y,z) points")
    return out

def _polyline_geometry(group, points, x=0, y=0):
    """Build a curve polyline as joined Curve Primitive Line segments."""
    points = _normalize_points(points)
    segments = []
    for idx, (a, b) in enumerate(zip(points, points[1:])):
        node = _new_node(group, "GeometryNodeCurvePrimitiveLine", x, y - idx * 90)
        _set_vector_socket_default(node.inputs[0], a)
        _set_vector_socket_default(node.inputs[1], b)
        segments.append(Value(node.outputs[0], TYPE_GEOMETRY))
    return _join_geometry(group, segments, x + 280, y)

def _transform_geometry(group, geo, translation=None, scale=None, rotation=None, x=0, y=0):
    """Function `_transform_geometry` used by the GN Script MVP addon."""
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("transform() expects Geometry")
    node = _new_node(group, "GeometryNodeTransform", x, y)
    group.links.new(geo.socket, node.inputs[0])
    if translation is not None:
        if _is_const_vector_like(translation):
            _set_vector_socket_default(node.inputs[2], translation)
        elif isinstance(translation, Value) and translation.typ == TYPE_VECTOR:
            group.links.new(translation.socket, node.inputs[2])
        else:
            raise CompileError("translation= must be Vector")
    if rotation is not None:
        if _is_const_vector_like(rotation):
            _set_rotation_socket_default(node.inputs[3], rotation)
        elif isinstance(rotation, Value) and rotation.typ == TYPE_VECTOR:
            rot = _euler_to_rotation(group, rotation, x - 180, y - 120)
            group.links.new(rot.socket, node.inputs[3])
        else:
            raise CompileError("rotation= must be Vector in radians")
    if scale is not None:
        if _is_const_number(scale):
            _set_vector_socket_default(node.inputs[4], (scale, scale, scale))
        elif _is_const_vector_like(scale):
            _set_vector_socket_default(node.inputs[4], scale)
        elif isinstance(scale, Value) and scale.typ == TYPE_VECTOR:
            group.links.new(scale.socket, node.inputs[4])
        elif isinstance(scale, Value) and _is_number_type(scale.typ):
            v = _combine_xyz(group, scale, scale, scale, x - 180, y - 80)
            group.links.new(v.socket, node.inputs[4])
        else:
            raise CompileError("scale= must be Float/Int or Vector")
    return Value(node.outputs[0], TYPE_GEOMETRY)

def _realize_instances(group, geo, x=0, y=0):
    """Function `_realize_instances` used by the GN Script MVP addon."""
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("realize_instances() expects Geometry")
    node = _new_node(group, "GeometryNodeRealizeInstances", x, y)
    group.links.new(geo.socket, node.inputs[0])
    return Value(node.outputs[0], TYPE_GEOMETRY)

def _const_vector_value(group, vec, x=0, y=0):
    """Function `_const_vector_value` used by the GN Script MVP addon."""
    # Kept for dynamic fallbacks/backwards compatibility. Static geometry macros
    # should prefer direct socket defaults via _transform_geometry(..., translation=tuple).
    return _combine_xyz(group, _value(group, vec[0], x-120, y), _value(group, vec[1], x-120, y-40), _value(group, vec[2], x-120, y-80), x, y)

def _scale_to_static_vec(scale):
    """Function `_scale_to_static_vec` used by the GN Script MVP addon."""
    if _is_const_number(scale):
        s = float(scale)
        return (s, s, s)
    if _is_const_vector_like(scale):
        return (float(scale[0]), float(scale[1]), float(scale[2]))
    return None

def _scale_to_vector_value(group, scale, x=0, y=0):
    """Function `_scale_to_vector_value` used by the GN Script MVP addon."""
    if isinstance(scale, Value):
        if scale.typ == TYPE_VECTOR:
            return scale
        if _is_number_type(scale.typ):
            return _combine_xyz_mixed(group, [scale, scale, scale], x, y)
    raise CompileError("scale= must be Float/Int or Vector")

def _cell_translation_for_offset(group, off, scale, x=0, y=0):
    """Return translation that places a scaled copy into a -1/0/1 cell.

    For static scale, this is folded to socket defaults. For dynamic scale,
    translation = offset * (1 - scale) is built as vector nodes. This makes
    offsets behave like third-grid cell positions: scale=1/3 -> centers at
    -2/3, 0, 2/3 instead of -1/3, 0, 1/3.
    """
    sv = _scale_to_static_vec(scale)
    if sv is not None:
        return (float(off[0]) * (1.0 - sv[0]), float(off[1]) * (1.0 - sv[1]), float(off[2]) * (1.0 - sv[2]))
    scale_vec = _scale_to_vector_value(group, scale, x - 260, y)
    one_vec = _combine_xyz_mixed(group, [1.0, 1.0, 1.0], x - 520, y)
    inv_scale = _vector_math(group, "SUBTRACT", [one_vec, scale_vec], TYPE_VECTOR, x - 320, y)
    off_vec = _combine_xyz_mixed(group, [float(off[0]), float(off[1]), float(off[2])], x - 520, y - 70)
    return _vector_math(group, "MULTIPLY", [off_vec, inv_scale], TYPE_VECTOR, x - 120, y)

def _copy_by_offsets(group, geo, offsets, scale=1.0/3.0, x=0, y=0):
    """Function `_copy_by_offsets` used by the GN Script MVP addon."""
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("copy_by_offsets() first argument must be Geometry")
    offsets = _normalize_offsets(offsets)
    # Correct Sierpinski/Menger semantics: each copy is scaled and translated
    # into its own cell, then all copies are joined. The previous implementation
    # translated all copies first and scaled the joined result, which compressed
    # cell positions and delayed/obscured holes.
    copies = []
    for idx, off in enumerate(offsets):
        yy = y - idx * 110
        translation = _cell_translation_for_offset(group, off, scale, x + 220, yy)
        copies.append(_transform_geometry(group, geo, translation=translation, scale=scale, x=x + 340, y=yy))
    return _join_geometry(group, copies, x + 720, y)

def _repeat_copy_by_offsets(group, geo, iterations, offsets, scale=1.0/3.0, x=0, y=0):
    """Function `_repeat_copy_by_offsets` used by the GN Script MVP addon."""
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("runtime for input must be Geometry")
    ri = _new_node(group, "GeometryNodeRepeatInput", x, y)
    ro = _new_node(group, "GeometryNodeRepeatOutput", x + 1120, y)
    if not ri.pair_with_output(ro):
        raise CompileError("Could not pair Repeat Zone nodes")
    group.links.new(iterations.socket, ri.inputs[0])
    group.links.new(geo.socket, ri.inputs[1])
    current = Value(ri.outputs[1], TYPE_GEOMETRY)
    body = _copy_by_offsets(group, current, offsets, scale, x + 240, y - 160)
    group.links.new(body.socket, ro.inputs[0])
    return Value(ro.outputs[0], TYPE_GEOMETRY)

__all__ = ['_normalize_offsets', '_set_vector_socket_default', '_set_rotation_socket_default', '_euler_to_rotation', '_is_const_number', '_is_const_vector_like', '_cube_geometry', '_join_geometry', '_normalize_points', '_polyline_geometry', '_transform_geometry', '_realize_instances', '_const_vector_value', '_scale_to_static_vec', '_scale_to_vector_value', '_cell_translation_for_offset', '_copy_by_offsets', '_repeat_copy_by_offsets']
