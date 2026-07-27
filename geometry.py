"""Generic geometry helpers such as cube(), transform(), join(), and polyline()."""

from .constants import *
from .errors import CompileError
from .values import Value
from .nodes import _new_node, _value, _combine_xyz, _combine_xyz_mixed, _separate_xyz, _is_number_type, _vector_math, _compare, _switch, _math
from .consteval import _is_const_vector, _as_float_const




def _as_number_value(group, value, x=0, y=0, label="value"):
    """Return a numeric compile-time constant or Value as a node Value."""
    if _is_const_number(value):
        return _value(group, value, x, y)
    if isinstance(value, Value) and _is_number_type(value.typ):
        return value
    raise CompileError(f"{label} expects Float/Int")


def _set_int_like_socket(group, socket, value, x=0, y=0, label="value", minimum=None):
    """Write or link a numeric value into an integer-like Blender node socket."""
    if _is_const_number(value):
        intval = int(value)
        if minimum is not None:
            intval = max(int(minimum), intval)
        socket.default_value = intval
        return
    if isinstance(value, Value) and _is_number_type(value.typ):
        group.links.new(value.socket, socket)
        return
    raise CompileError(f"{label} expects Float/Int")


def _set_float_like_socket(group, socket, value, x=0, y=0, label="value", minimum=None):
    """Write or link a numeric value into a float-like Blender node socket."""
    if _is_const_number(value):
        fval = float(value)
        if minimum is not None:
            fval = max(float(minimum), fval)
        socket.default_value = fval
        return
    if isinstance(value, Value) and _is_number_type(value.typ):
        group.links.new(value.socket, socket)
        return
    raise CompileError(f"{label} expects Float/Int")


def _grid_geometry(group, width, height, x=0, y=0):
    """Create one rectangular mesh grid and return its geometry plus UV field.

    The DSL-level grid(width, height) represents a real surface, not a
    point cloud. Blender's Mesh Grid node creates a single mesh datablock with
    shared vertices and quad faces, so one material can shade the whole grid
    through generic geometry attributes.
    """
    node = _new_node(group, "GeometryNodeMeshGrid", x, y)
    _set_float_like_socket(group, node.inputs[0], width, x - 220, y, "grid() width", minimum=1)
    _set_float_like_socket(group, node.inputs[1], height, x - 220, y - 50, "grid() height", minimum=1)
    _set_int_like_socket(group, node.inputs[2], width, x - 220, y - 100, "grid() width", minimum=2)
    _set_int_like_socket(group, node.inputs[3], height, x - 220, y - 150, "grid() height", minimum=2)
    return Value(node.outputs[0], TYPE_GEOMETRY), Value(node.outputs[1], TYPE_VECTOR)

def _store_named_attribute_geometry(group, geo, attr_name, value, selection=None, domain="POINT", data_type_override=None, x=0, y=0):
    """Expression-form Store Named Attribute returning Geometry."""
    from .statements import _store_named_attribute
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("store_named_attribute() first argument must be Geometry")
    socket = _store_named_attribute(group, geo.socket, attr_name, value, selection, domain, data_type_override, x, y)
    return Value(socket, TYPE_GEOMETRY)


def _set_material_geometry(group, geo, material, x=0, y=0):
    """Assign a runtime Material value or named Blender material to geometry."""
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("set_material() first argument must be Geometry")
    node = _new_node(group, "GeometryNodeSetMaterial", x, y)
    group.links.new(geo.socket, node.inputs[0])
    node.inputs[1].default_value = True
    if isinstance(material, Value):
        if material.typ != TYPE_MATERIAL:
            raise CompileError("set_material() second argument must be Material or a compile-time material name")
        group.links.new(material.socket, node.inputs[2])
    elif isinstance(material, str):
        import bpy
        mat = bpy.data.materials.get(material)
        if mat is None:
            mat = bpy.data.materials.new(material)
        node.inputs[2].default_value = mat
    else:
        raise CompileError("set_material() second argument must be Material or a compile-time material name")
    return Value(node.outputs[0], TYPE_GEOMETRY)


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

def _empty_geometry(group, x=0, y=0):
    """Create a valid Geometry value with zero elements.

    Empty geometry intentionally uses the same Mesh Line point-source contract as
    points(count), with Count fixed to 0 and no offset. This creates an ordinary
    Geometry socket without depending on implicit no-input join behavior.
    """
    node = _new_node(group, "GeometryNodeMeshLine", x, y)
    node.inputs[0].default_value = 0
    try:
        _set_vector_socket_default(node.inputs[2], (0, 0, 0))
        _set_vector_socket_default(node.inputs[3], (0, 0, 0))
    except Exception:
        pass
    return Value(node.outputs[0], TYPE_GEOMETRY)


def _set_vector_socket_value(group, socket, value, x=0, y=0, label="value", *, link_const=False):
    """Write or link a Vector into a Blender node socket.

    Some Geometry Nodes field inputs, notably Set Position's Position socket in
    Blender 5.x, ignore an unlinked socket default during modifier evaluation.
    `link_const=True` materializes compile-time vectors as a Combine XYZ node so
    those sockets receive an explicit field value while ordinary value sockets can
    still keep compact defaults.
    """
    if _is_const_vector_like(value):
        if link_const:
            vector_value = _combine_xyz_mixed(group, list(value), x, y)
            group.links.new(vector_value.socket, socket)
        else:
            _set_vector_socket_default(socket, value)
        return
    if isinstance(value, Value) and value.typ == TYPE_VECTOR:
        group.links.new(value.socket, socket)
        return
    raise CompileError(f"{label} expects Vector")


def _socket_by_name_or_index(node, name, index):
    """Return a node input by stable name, falling back to Blender's index order."""
    socket = node.inputs.get(name) if hasattr(node.inputs, "get") else None
    return socket if socket is not None else node.inputs[index]


def _point_geometry(group, position, x=0, y=0):
    """Create one point and set its position from a const or runtime Vector."""
    geo = _points_geometry(group, 1, x, y)
    node = _new_node(group, "GeometryNodeSetPosition", x + 220, y)
    group.links.new(geo.socket, node.inputs[0])
    _set_vector_socket_value(group, node.inputs[2], position, x, y, "point() position", link_const=True)
    return Value(node.outputs[0], TYPE_GEOMETRY)


def _line_geometry(group, start, end, x=0, y=0):
    """Create a Curve Primitive Line from const or runtime Vector endpoints."""
    node = _new_node(group, "GeometryNodeCurvePrimitiveLine", x, y)
    if hasattr(node, "mode"):
        try:
            node.mode = "POINTS"
        except Exception:
            pass
    start_socket = _socket_by_name_or_index(node, "Start", 0)
    end_socket = _socket_by_name_or_index(node, "End", 1)
    _set_vector_socket_value(group, start_socket, start, x - 180, y, "line() start")
    _set_vector_socket_value(group, end_socket, end, x - 180, y - 60, "line() end")
    return Value(node.outputs[0], TYPE_GEOMETRY)


def _join_geometry(group, geos, x=0, y=0):
    """Join Geometry values, using empty_geometry as the identity for empty lists."""
    if len(geos) == 0:
        return _empty_geometry(group, x, y)
    for geo in geos:
        if not isinstance(geo, Value) or geo.typ != TYPE_GEOMETRY:
            raise CompileError("join() expects Geometry arguments")
    if len(geos) == 1:
        return geos[0]
    node = _new_node(group, "GeometryNodeJoinGeometry", x, y)
    for geo in geos:
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


def _ensure_numeric_value(value, label):
    """Validate a runtime scalar Value used by layout formulas."""
    if not isinstance(value, Value) or not _is_number_type(value.typ):
        raise CompileError(f"{label} expects Float/Int")
    return value


def _ensure_vector_value(value, label):
    """Validate a runtime Vector Value used by layout formulas."""
    if not isinstance(value, Value) or value.typ != TYPE_VECTOR:
        raise CompileError(f"{label} expects Vector")
    return value


def _layout_value(group, value, x=0, y=0, label="value"):
    """Normalize a compile-time constant or Value into a node Value."""
    if isinstance(value, Value):
        return value
    if _is_const_number(value):
        return _value(group, value, x, y)
    if _is_const_vector_like(value):
        return _combine_xyz_mixed(group, list(value), x, y)
    raise CompileError(f"{label} expects Float/Int or Vector")


def _layout_vector_value(group, value, x=0, y=0, label="value"):
    value = _layout_value(group, value, x, y, label)
    return _ensure_vector_value(value, label)


def _layout_numeric_value(group, value, x=0, y=0, label="value"):
    value = _layout_value(group, value, x, y, label)
    return _ensure_numeric_value(value, label)


def _safe_minimum_one(group, value, x=0, y=0):
    """Return max(value, 1) as a numeric field."""
    value = _ensure_numeric_value(value, "layout count component")
    return _math(group, "MAXIMUM", [value, _value(group, 1.0, x + 20, y - 40)], x, y)


def _safe_minimum_zero(group, value, x=0, y=0):
    """Return max(value, 0) as a numeric field."""
    value = _ensure_numeric_value(value, "layout point count")
    return _math(group, "MAXIMUM", [value, _value(group, 0.0, x + 20, y - 40)], x, y)


def _scale_or_multiply_vector(group, vector, factor, x=0, y=0, label="spacing"):
    vector = _ensure_vector_value(vector, "layout position")
    if isinstance(factor, Value) and factor.typ == TYPE_VECTOR:
        return _vector_math(group, "MULTIPLY", [vector, factor], TYPE_VECTOR, x, y)
    if isinstance(factor, Value) and _is_number_type(factor.typ):
        return _vector_math(group, "SCALE", [vector, factor], TYPE_VECTOR, x, y)
    raise CompileError(f"{label} expects Float/Int or Vector")


def _layout_count_components(group, count, x=0, y=0):
    count = _layout_vector_value(group, count, x, y, "layout_grid() count")
    cx = _separate_xyz(group, count, "x", x + 20, y - 40)
    cy = _separate_xyz(group, count, "y", x + 20, y - 80)
    cz = _separate_xyz(group, count, "z", x + 20, y - 120)
    return cx, cy, cz


def _layout_grid_total_count(group, count, x=0, y=0):
    cx, cy, cz = _layout_count_components(group, count, x, y)
    sx = _safe_minimum_zero(group, cx, x + 40, y - 160)
    sy = _safe_minimum_zero(group, cy, x + 40, y - 200)
    sz = _safe_minimum_zero(group, cz, x + 40, y - 240)
    xy = _math(group, "MULTIPLY", [sx, sy], x + 60, y - 280)
    return _math(group, "MULTIPLY", [xy, sz], x + 60, y - 320)


def _layout_grid_position(group, count, spacing, centered, x=0, y=0):
    cx, cy, cz = _layout_count_components(group, count, x, y)
    sx = _safe_minimum_one(group, cx, x + 40, y - 160)
    sy = _safe_minimum_one(group, cy, x + 40, y - 200)
    sz = _safe_minimum_one(group, cz, x + 40, y - 240)
    idx = _new_node(group, "GeometryNodeInputIndex", x + 20, y - 280)
    index_val = Value(idx.outputs[0], TYPE_INT)
    layer_size = _math(group, "MULTIPLY", [sx, sy], x + 60, y - 320)
    x_pos = _math(group, "MODULO", [index_val, sx], x + 80, y - 360)
    row = _math(group, "DIVIDE", [index_val, sx], x + 80, y - 400)
    row_floor = _math(group, "FLOOR", [row], x + 100, y - 440)
    y_pos = _math(group, "MODULO", [row_floor, sy], x + 120, y - 480)
    z_raw = _math(group, "DIVIDE", [index_val, layer_size], x + 80, y - 520)
    z_pos = _math(group, "FLOOR", [z_raw], x + 100, y - 560)
    pos = _combine_xyz_mixed(group, [x_pos, y_pos, z_pos], x + 140, y - 600)
    spacing_val = _layout_value(group, spacing, x + 140, y - 640, "layout_grid() spacing")
    pos = _scale_or_multiply_vector(group, pos, spacing_val, x + 160, y - 680, "layout_grid() spacing")
    if centered:
        ext_counts = _combine_xyz_mixed(
            group,
            [
                _math(group, "SUBTRACT", [sx, _value(group, 1.0, x + 20, y - 720)], x + 180, y - 720),
                _math(group, "SUBTRACT", [sy, _value(group, 1.0, x + 20, y - 760)], x + 180, y - 760),
                _math(group, "SUBTRACT", [sz, _value(group, 1.0, x + 20, y - 800)], x + 180, y - 800),
            ],
            x + 200,
            y - 840,
        )
        offset = _scale_or_multiply_vector(group, ext_counts, spacing_val, x + 220, y - 880, "layout_grid() spacing")
        offset = _vector_math(group, "SCALE", [offset, _value(group, 0.5, x + 240, y - 920)], TYPE_VECTOR, x + 240, y - 920)
        pos = _vector_math(group, "SUBTRACT", [pos, offset], TYPE_VECTOR, x + 260, y - 960)
    return pos


def _layout_grid_geometry(group, geo, count, spacing, centered=False, x=0, y=0):
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("layout_grid() first argument must be Geometry")
    pos = _layout_grid_position(group, count, spacing, centered, x + 20, y - 40)
    return _set_position_geometry(group, geo, pos, None, x, y)


def _grid_points_geometry(group, count, spacing, centered=False, x=0, y=0):
    total = _layout_grid_total_count(group, count, x + 20, y - 40)
    pts = _points_geometry(group, total, x + 20, y - 120)
    return _layout_grid_geometry(group, pts, count, spacing, centered, x, y)



def _socket_by_name_or_compile_error(sockets, name, label):
    """Return a named Blender socket or raise a controlled compile error."""
    try:
        return sockets[name]
    except Exception as exc:
        raise CompileError(f"{label}: Blender node missing {name!r} socket") from exc


def _geometry_point_count(group, geo, label, x=0, y=0):
    """Return the runtime Point-domain count of a Geometry value."""
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError(f"{label}: count derivation expects Geometry")
    try:
        node = _new_node(group, "GeometryNodeAttributeDomainSize", x, y)
    except Exception as exc:
        raise CompileError(f"{label}: Blender Domain Size node is unavailable") from exc
    geometry_input = _socket_by_name_or_compile_error(node.inputs, "Geometry", label)
    point_count = _socket_by_name_or_compile_error(node.outputs, "Point Count", label)
    group.links.new(geo.socket, geometry_input)
    return Value(point_count, TYPE_INT)


def _resolve_layout_count(group, geo, count, label, x=0, y=0):
    """Use an explicit layout count or derive one from input geometry."""
    if count is None:
        return _geometry_point_count(group, geo, label, x, y)
    return count

def _layout_normalized_index(group, count, include_endpoint=False, x=0, y=0):
    count_val = _layout_numeric_value(group, count, x, y, "layout count")
    idx = _new_node(group, "GeometryNodeInputIndex", x + 20, y - 40)
    index_val = Value(idx.outputs[0], TYPE_INT)
    if include_endpoint:
        raw = _math(group, "SUBTRACT", [count_val, _value(group, 1.0, x + 20, y - 80)], x + 40, y - 80)
    else:
        raw = count_val
    denom = _math(group, "MAXIMUM", [raw, _value(group, 1.0, x + 20, y - 120)], x + 60, y - 120)
    return _math(group, "DIVIDE", [index_val, denom], x + 80, y - 160)


def _layout_circle_geometry(group, geo, count, radius, start_angle, end_angle, include_endpoint=False, x=0, y=0):
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("layout_circle() first argument must be Geometry")
    count = _resolve_layout_count(group, geo, count, "layout_circle() count", x + 20, y - 40)
    t = _layout_normalized_index(group, count, include_endpoint, x + 20, y - 80)
    radius_val = _layout_numeric_value(group, radius, x + 20, y - 260, "layout_circle() radius")
    start_val = _layout_numeric_value(group, start_angle, x + 20, y - 300, "layout_circle() start_angle")
    end_val = _layout_numeric_value(group, end_angle, x + 20, y - 340, "layout_circle() end_angle")
    span = _math(group, "SUBTRACT", [end_val, start_val], x + 40, y - 380)
    scaled = _math(group, "MULTIPLY", [span, t], x + 60, y - 420)
    angle = _math(group, "ADD", [start_val, scaled], x + 80, y - 460)
    cos_a = _math(group, "COSINE", [angle], x + 100, y - 500)
    sin_a = _math(group, "SINE", [angle], x + 100, y - 540)
    px = _math(group, "MULTIPLY", [cos_a, radius_val], x + 120, y - 580)
    py = _math(group, "MULTIPLY", [sin_a, radius_val], x + 120, y - 620)
    pos = _combine_xyz_mixed(group, [px, py, 0.0], x + 140, y - 660)
    return _set_position_geometry(group, geo, pos, None, x, y)


def _circle_points_geometry(group, count, radius, start_angle, end_angle, include_endpoint=False, x=0, y=0):
    count_val = _layout_numeric_value(group, count, x + 20, y - 40, "circle_points() count")
    pts = _points_geometry(group, _safe_minimum_zero(group, count_val, x + 40, y - 80), x + 20, y - 120)
    return _layout_circle_geometry(group, pts, count_val, radius, start_angle, end_angle, include_endpoint, x, y)


def _layout_spiral_geometry(group, geo, count, radius, turns, height, start_radius, start_angle, x=0, y=0):
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("layout_spiral() first argument must be Geometry")
    count = _resolve_layout_count(group, geo, count, "layout_spiral() count", x + 20, y - 40)
    t = _layout_normalized_index(group, count, True, x + 20, y - 80)
    radius_val = _layout_numeric_value(group, radius, x + 20, y - 260, "layout_spiral() radius")
    turns_val = _layout_numeric_value(group, turns, x + 20, y - 300, "layout_spiral() turns")
    height_val = _layout_numeric_value(group, height, x + 20, y - 340, "layout_spiral() height")
    start_radius_val = _layout_numeric_value(group, start_radius, x + 20, y - 380, "layout_spiral() start_radius")
    start_angle_val = _layout_numeric_value(group, start_angle, x + 20, y - 420, "layout_spiral() start_angle")
    dr = _math(group, "SUBTRACT", [radius_val, start_radius_val], x + 40, y - 460)
    r = _math(group, "ADD", [start_radius_val, _math(group, "MULTIPLY", [dr, t], x + 60, y - 500)], x + 80, y - 540)
    tau_turns = _math(group, "MULTIPLY", [_value(group, 6.283185307179586, x + 40, y - 580), turns_val], x + 40, y - 580)
    angle = _math(group, "ADD", [start_angle_val, _math(group, "MULTIPLY", [tau_turns, t], x + 60, y - 620)], x + 80, y - 660)
    z = _math(group, "MULTIPLY", [height_val, t], x + 80, y - 700)
    cos_a = _math(group, "COSINE", [angle], x + 100, y - 740)
    sin_a = _math(group, "SINE", [angle], x + 100, y - 780)
    px = _math(group, "MULTIPLY", [cos_a, r], x + 120, y - 820)
    py = _math(group, "MULTIPLY", [sin_a, r], x + 120, y - 860)
    pos = _combine_xyz_mixed(group, [px, py, z], x + 140, y - 900)
    return _set_position_geometry(group, geo, pos, None, x, y)


def _spiral_points_geometry(group, count, radius, turns, height, start_radius, start_angle, x=0, y=0):
    count_val = _layout_numeric_value(group, count, x + 20, y - 40, "spiral_points() count")
    pts = _points_geometry(group, _safe_minimum_zero(group, count_val, x + 40, y - 80), x + 20, y - 120)
    return _layout_spiral_geometry(group, pts, count_val, radius, turns, height, start_radius, start_angle, x, y)


def _layout_random_geometry(group, geo, min_vec, max_vec, seed=None, random_value_helper=None, x=0, y=0):
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("layout_random() first argument must be Geometry")
    if random_value_helper is None:
        raise CompileError("Internal error: layout_random() missing random helper")
    min_val = _layout_vector_value(group, min_vec, x + 20, y - 40, "layout_random() min")
    max_val = _layout_vector_value(group, max_vec, x + 20, y - 80, "layout_random() max")
    idx = _new_node(group, "GeometryNodeInputIndex", x + 20, y - 120)
    id_val = Value(idx.outputs[0], TYPE_INT)
    if isinstance(seed, Value):
        pos = random_value_helper(group, min_val, max_val, seed_val=seed, id_val=id_val, x=x + 80, y=y - 160)
    elif seed is None:
        pos = random_value_helper(group, min_val, max_val, id_val=id_val, x=x + 80, y=y - 160)
    elif _is_const_number(seed):
        pos = random_value_helper(group, min_val, max_val, seed_default=int(seed), id_val=id_val, x=x + 80, y=y - 160)
    else:
        raise CompileError("layout_random() seed expects Int")
    return _set_position_geometry(group, geo, pos, None, x, y)


def _random_points_geometry(group, count, min_vec, max_vec, seed=None, random_value_helper=None, x=0, y=0):
    count_val = _layout_numeric_value(group, count, x + 20, y - 40, "random_points() count")
    pts = _points_geometry(group, _safe_minimum_zero(group, count_val, x + 40, y - 80), x + 20, y - 120)
    return _layout_random_geometry(group, pts, min_vec, max_vec, seed, random_value_helper, x, y)


def _instance_on_points(group, instance, points, selection=None, scale=None, rotation=None, realize=True, x=0, y=0):
    """Place instance geometry on point geometry and optionally realize instances."""
    if instance.typ != TYPE_GEOMETRY:
        raise CompileError("instance_on_points(instance, points) first argument must be Geometry")
    if points.typ != TYPE_GEOMETRY:
        raise CompileError("instance_on_points(instance, points) second argument must be Geometry")
    node = _new_node(group, "GeometryNodeInstanceOnPoints", x, y)
    group.links.new(points.socket, node.inputs[0])
    if selection is not None:
        if selection.typ != TYPE_BOOL:
            raise CompileError("instance_on_points selection= expects Bool")
        group.links.new(selection.socket, node.inputs[1])
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


__all__ = ['_as_number_value', '_set_int_like_socket', '_set_float_like_socket', '_grid_geometry', '_store_named_attribute_geometry', '_set_material_geometry', '_set_vector_socket_default', '_set_rotation_socket_default', '_euler_to_rotation', '_is_const_number', '_is_const_vector_like', '_cube_geometry', '_join_geometry', '_normalize_points', '_polyline_geometry', '_transform_geometry', '_realize_instances', '_points_geometry', '_set_position_geometry', '_layout_grid_geometry', '_grid_points_geometry', '_geometry_point_count', '_layout_circle_geometry', '_circle_points_geometry', '_layout_spiral_geometry', '_spiral_points_geometry', '_layout_random_geometry', '_random_points_geometry', '_instance_on_points']
