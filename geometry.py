"""Generic geometry helpers such as cube(), transform(), join(), and polyline()."""

from .constants import *
from .errors import CompileError
from .values import Value
from .nodes import _new_node, _value, _combine_xyz, _combine_xyz_mixed, _is_number_type, _vector_math, _compare, _switch
from .consteval import _is_const_vector, _as_float_const




def _set_vector_socket_default(socket, vec):
    """Function `_set_vector_socket_default` used by the NodeForge addon."""
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
    """Function `_is_const_number` used by the NodeForge addon."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)

def _is_const_vector_like(v):
    """Function `_is_const_vector_like` used by the NodeForge addon."""
    return _is_const_vector(v) or (isinstance(v, (tuple, list)) and len(v) == 3 and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in v))

def _cube_geometry(group, size, x=0, y=0):
    """Function `_cube_geometry` used by the NodeForge addon."""
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
    """Function `_join_geometry` used by the NodeForge addon."""
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
    """Function `_transform_geometry` used by the NodeForge addon."""
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
    """Function `_realize_instances` used by the NodeForge addon."""
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("realize_instances() expects Geometry")
    node = _new_node(group, "GeometryNodeRealizeInstances", x, y)
    group.links.new(geo.socket, node.inputs[0])
    return Value(node.outputs[0], TYPE_GEOMETRY)


def _points_geometry(group, count, x=0, y=0):
    """Create a runtime point/vertex geometry with Count controlled by an Int value.

    This uses Mesh Line as a compact point source. Positions are normally set later
    with set_position(points, vector_field).
    """
    node = _new_node(group, "GeometryNodeMeshLine", x, y)
    # Count
    if _is_const_number(count):
        node.inputs[0].default_value = max(0, int(count))
    elif isinstance(count, Value) and count.typ == TYPE_INT:
        group.links.new(count.socket, node.inputs[0])
    elif isinstance(count, Value) and count.typ == TYPE_FLOAT:
        group.links.new(count.socket, node.inputs[0])
    else:
        raise CompileError("points(count) expects an Int count")
    # Start at origin and use zero offset; set_position() can place points by Index.
    try:
        _set_vector_socket_default(node.inputs[2], (0, 0, 0))
        _set_vector_socket_default(node.inputs[3], (0, 0, 0))
    except Exception:
        pass
    return Value(node.outputs[0], TYPE_GEOMETRY)


def _set_position_geometry(group, geo, pos, selection=None, x=0, y=0):
    """Expression-form set_position(geometry, position, selection=...)."""
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("set_position(geo, position) first argument must be Geometry")
    if pos.typ != TYPE_VECTOR:
        raise CompileError("set_position(geo, position) second argument must be Vector")
    node = _new_node(group, "GeometryNodeSetPosition", x, y)
    group.links.new(geo.socket, node.inputs[0])
    if selection is not None:
        if selection.typ != TYPE_BOOL:
            raise CompileError("set_position(..., selection=...) expects Bool selection")
        group.links.new(selection.socket, node.inputs[1])
    group.links.new(pos.socket, node.inputs[2])
    return Value(node.outputs[0], TYPE_GEOMETRY)


def _instance_on_points(group, instance, points, scale=None, rotation=None, realize=True, x=0, y=0):
    """Place instance geometry on point geometry and optionally realize instances."""
    if instance.typ != TYPE_GEOMETRY:
        raise CompileError("instance_on_points(instance, points) first argument must be Geometry")
    if points.typ != TYPE_GEOMETRY:
        raise CompileError("instance_on_points(instance, points) second argument must be Geometry")
    node = _new_node(group, "GeometryNodeInstanceOnPoints", x, y)
    group.links.new(points.socket, node.inputs[0])
    group.links.new(instance.socket, node.inputs[2])
    if scale is not None:
        if _is_const_number(scale):
            _set_vector_socket_default(node.inputs[6], (scale, scale, scale))
        elif _is_const_vector_like(scale):
            _set_vector_socket_default(node.inputs[6], scale)
        elif isinstance(scale, Value) and scale.typ == TYPE_VECTOR:
            group.links.new(scale.socket, node.inputs[6])
        elif isinstance(scale, Value) and _is_number_type(scale.typ):
            v = _combine_xyz(group, scale, scale, scale, x - 180, y - 80)
            group.links.new(v.socket, node.inputs[6])
        else:
            raise CompileError("instance_on_points scale= expects Float/Int or Vector")
    if rotation is not None:
        if _is_const_vector_like(rotation):
            _set_rotation_socket_default(node.inputs[5], rotation)
        elif isinstance(rotation, Value) and rotation.typ == TYPE_VECTOR:
            rot = _euler_to_rotation(group, rotation, x - 180, y - 120)
            group.links.new(rot.socket, node.inputs[5])
        else:
            raise CompileError("instance_on_points rotation= expects Vector in radians")
    out = Value(node.outputs[0], TYPE_GEOMETRY)
    if realize:
        return _realize_instances(group, out, x + 240, y)
    return out


__all__ = ['_set_vector_socket_default', '_set_rotation_socket_default', '_euler_to_rotation', '_is_const_number', '_is_const_vector_like', '_cube_geometry', '_join_geometry', '_normalize_points', '_polyline_geometry', '_transform_geometry', '_realize_instances', '_points_geometry', '_set_position_geometry', '_instance_on_points']
