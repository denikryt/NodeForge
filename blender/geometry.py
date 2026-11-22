"""Generic geometry helpers such as cube(), transform(), join(), and polyline()."""

from ..semantic.constants import *
from ..type_capabilities import ATTRIBUTE_DATA_TYPES, CAPTURE_ITEM_TYPES
from ..semantic.attribute_domains import normalize_attribute_domain
from ..errors import CompileError
from .values import Value, DeferredConstant
from .input_delivery import deliver_input
from .nodes import _new_node, _combine_xyz, _combine_xyz_mixed, _is_number_type
from ..semantic.compile_time import _as_float_const, _is_const_number, _is_const_vector, _is_const_vector_like
from ..semantic.numeric_semantics import normalize_int_constant



_ALLOWED_STORE_TYPES = {
    "FLOAT": "FLOAT",
    "INT": "INT",
    "INTEGER": "INT",
    "VECTOR": "FLOAT_VECTOR",
    "FLOAT_VECTOR": "FLOAT_VECTOR",
    "COLOR": "FLOAT_COLOR",
    "RGBA": "FLOAT_COLOR",
    "FLOAT_COLOR": "FLOAT_COLOR",
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
}
def _attribute_data_type(typ):
    """Map one supported NodeForge field type to Blender attribute data type."""
    return ATTRIBUTE_DATA_TYPES.get(typ)


def _store_named_attribute(
    group, geometry_socket, attr_name, value, *, selection, domain,
    data_type_override, x=0, y=0,
):
    """Build the physical Store Named Attribute primitive from typed backend values."""
    if data_type_override:
        data_type = _ALLOWED_STORE_TYPES.get(data_type_override.upper())
        if data_type is None:
            raise CompileError("Unsupported store() type. Use FLOAT, INT, VECTOR, COLOR or BOOLEAN")
    else:
        data_type = _attribute_data_type(value.typ)
    if data_type is None:
        raise CompileError("store(name, value) supports Float, Int, Vector and Bool values (also Color)")
    domain = normalize_attribute_domain(domain, "store()")
    node = _new_node(group, "GeometryNodeStoreNamedAttribute", x, y)
    node.data_type = data_type
    node.domain = domain
    node.inputs["Selection"].default_value = True
    deliver_input(group, node.inputs["Name"], attr_name, context="store() Name")
    group.links.new(geometry_socket, node.inputs["Geometry"])
    if selection is not None:
        group.links.new(selection.socket, node.inputs["Selection"])
    if isinstance(value, Value) and _attribute_data_type(value.typ) != data_type:
        # An explicit store data_type override intentionally uses Blender's
        # physical socket conversion; strict literal delivery must not erase it.
        group.links.new(value.socket, node.inputs["Value"])
    else:
        deliver_input(group, node.inputs["Value"], value, context="attribute Value")
    return node.outputs["Geometry"]



def _set_int_like_socket(group, socket, value, x=0, y=0, label="value", minimum=None):
    """Write or link a numeric value into an integer-like Blender node socket."""
    if isinstance(value, DeferredConstant):
        if not _is_number_type(value.typ):
            raise CompileError(f"{label} expects Float/Int")
        value = value.value
    if _is_const_number(value):
        intval = int(value)
        if minimum is not None:
            intval = max(int(minimum), intval)
        deliver_input(group, socket, intval, context=label)
        return
    if isinstance(value, Value) and _is_number_type(value.typ):
        if value.typ == TYPE_FLOAT:
            # Existing numeric geometry helper permits physical Float→Int links.
            group.links.new(value.socket, socket)
        else:
            deliver_input(group, socket, value, context=label)
        return
    raise CompileError(f"{label} expects Float/Int")


def _set_float_like_socket(group, socket, value, x=0, y=0, label="value", minimum=None):
    """Write or link a numeric value into a float-like Blender node socket."""
    if isinstance(value, DeferredConstant):
        if not _is_number_type(value.typ):
            raise CompileError(f"{label} expects Float/Int")
        value = value.value
    if _is_const_number(value):
        fval = float(value)
        if minimum is not None:
            fval = max(float(minimum), fval)
        deliver_input(group, socket, fval, context=label)
        return
    if isinstance(value, Value) and _is_number_type(value.typ):
        deliver_input(group, socket, value, context=label)
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

def _store_named_attribute_geometry(group, geo, attr_name, value, *, selection, domain, data_type_override, x=0, y=0):
    """Expression-form Store Named Attribute returning Geometry."""
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("store_named_attribute() first argument must be Geometry")
    socket = _store_named_attribute(
        group,
        geo.socket,
        attr_name,
        value,
        selection=selection,
        domain=domain,
        data_type_override=data_type_override,
        x=x,
        y=y,
    )
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
    return Value(node.outputs[0], TYPE_ROTATION)

def _cube_geometry(group, size, x=0, y=0):
    """Create Mesh Cube with direct scalar/vector defaults or existing runtime links."""
    node = _new_node(group, "GeometryNodeMeshCube", x, y)
    for index in (1, 2, 3):
        node.inputs[index].default_value = 2
    if isinstance(size, DeferredConstant) and _is_number_type(size.typ):
        size = (size.value,) * 3
    elif _is_const_number(size):
        size = (size,) * 3
    if _is_const_vector_like(size) or (
        isinstance(size, (Value, DeferredConstant)) and size.typ == TYPE_VECTOR
    ):
        _set_vector_socket_value(group, node.inputs[0], size, x, y, "cube() size")
    elif isinstance(size, Value) and _is_number_type(size.typ):
        vector = _combine_xyz(group, size, size, size, x - 180, y - 60)
        deliver_input(group, node.inputs[0], vector, context="cube() size")
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


def _set_vector_socket_value(group, socket, value, x=0, y=0, label="value"):
    """Deliver a Vector to its resolved Blender consumer and respect implicit fields."""
    if _is_const_vector_like(value):
        deliver_input(group, socket, tuple(value), context=label)
    elif isinstance(value, (Value, DeferredConstant)) and value.typ == TYPE_VECTOR:
        deliver_input(group, socket, value, context=label)
    else:
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
    _set_vector_socket_value(group, node.inputs[2], position, x, y, "point() position")
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
        _set_vector_socket_value(group, node.inputs[0], a, x, y, "polyline() start")
        _set_vector_socket_value(group, node.inputs[1], b, x, y, "polyline() end")
        segments.append(Value(node.outputs[0], TYPE_GEOMETRY))
    return _join_geometry(group, segments, x + 280, y)



def _transform_geometry(group, geo, *, translation, scale, rotation, x=0, y=0):
    """Apply translation, Euler rotation and scale with resolved input semantics."""
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("transform() expects Geometry")
    node = _new_node(group, "GeometryNodeTransform", x, y)
    group.links.new(geo.socket, node.inputs[0])
    if translation is not None:
        _set_vector_socket_value(group, node.inputs[2], translation, x, y, "translation=")
    if rotation is not None:
        if isinstance(rotation, DeferredConstant) and rotation.typ == TYPE_VECTOR:
            rotation = DeferredConstant(TYPE_ROTATION, rotation.value)
        elif _is_const_vector_like(rotation):
            rotation = DeferredConstant(TYPE_ROTATION, tuple(rotation))
        elif isinstance(rotation, Value) and rotation.typ == TYPE_VECTOR:
            rotation = _euler_to_rotation(group, rotation, x - 180, y - 120)
        else:
            raise CompileError("rotation= must be Vector in radians")
        deliver_input(group, node.inputs[3], rotation, context="transform() rotation")
    if scale is not None:
        if isinstance(scale, DeferredConstant) and _is_number_type(scale.typ):
            scale = (scale.value,) * 3
        elif _is_const_number(scale):
            scale = (scale,) * 3
        if _is_const_vector_like(scale) or (
            isinstance(scale, (Value, DeferredConstant)) and scale.typ == TYPE_VECTOR
        ):
            _set_vector_socket_value(group, node.inputs[4], scale, x, y, "scale=")
        elif isinstance(scale, Value) and _is_number_type(scale.typ):
            combined = _combine_xyz(group, scale, scale, scale, x - 180, y - 80)
            deliver_input(group, node.inputs[4], combined, context="transform() scale")
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
    if isinstance(count, DeferredConstant):
        count = count.value
    if _is_const_number(count):
        deliver_input(group, node.inputs[0], max(0, int(count)), context="points() Count")
    elif isinstance(count, Value) and count.typ == TYPE_INT:
        deliver_input(group, node.inputs[0], count, context="points() Count")
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


def _set_position_geometry(group, geo, pos, *, selection, x=0, y=0):
    """Expression-form set_position(geometry, position, selection=...)."""
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("set_position(geo, position) first argument must be Geometry")
    if pos.typ != TYPE_VECTOR:
        raise CompileError("set_position(geo, position) second argument must be Vector")
    if selection is not None and (not isinstance(selection, (Value, DeferredConstant)) or selection.typ != TYPE_BOOL):
        raise CompileError("set_position(..., selection=...) expects Bool selection")
    node = _new_node(group, "GeometryNodeSetPosition", x, y)
    group.links.new(geo.socket, node.inputs[0])
    node.inputs[1].default_value = True
    if selection is not None:
        deliver_input(group, node.inputs[1], selection, context="selection field")
    deliver_input(group, node.inputs[2], pos, context="set_position() Position")
    return Value(node.outputs[0], TYPE_GEOMETRY)




def _instance_on_points(group, instance, points, *, selection, scale, rotation, realize, x=0, y=0):
    """Place instance geometry on point geometry and optionally realize instances."""
    if instance.typ != TYPE_GEOMETRY:
        raise CompileError("instance_on_points(instance, points) first argument must be Geometry")
    if points.typ != TYPE_GEOMETRY:
        raise CompileError("instance_on_points(instance, points) second argument must be Geometry")
    if selection is not None and (not isinstance(selection, (Value, DeferredConstant)) or selection.typ != TYPE_BOOL):
        raise CompileError("instance_on_points selection= expects Bool")
    node = _new_node(group, "GeometryNodeInstanceOnPoints", x, y)
    group.links.new(points.socket, node.inputs[0])
    node.inputs[1].default_value = True
    if selection is not None:
        deliver_input(group, node.inputs[1], selection, context="selection field")
    group.links.new(instance.socket, node.inputs[2])
    if scale is not None:
        if isinstance(scale, DeferredConstant) and _is_number_type(scale.typ):
            scale = (scale.value,) * 3
        elif _is_const_number(scale):
            scale = (scale,) * 3
        if _is_const_vector_like(scale) or (
            isinstance(scale, (Value, DeferredConstant)) and scale.typ == TYPE_VECTOR
        ):
            _set_vector_socket_value(group, node.inputs[6], scale, x, y, "instance_on_points scale")
        elif isinstance(scale, Value) and _is_number_type(scale.typ):
            vector = _combine_xyz(group, scale, scale, scale, x - 180, y - 80)
            deliver_input(group, node.inputs[6], vector, context="instance_on_points scale")
        else:
            raise CompileError("instance_on_points scale= expects Float/Int or Vector")
    if rotation is not None:
        if isinstance(rotation, DeferredConstant) and rotation.typ == TYPE_VECTOR:
            rotation = DeferredConstant(TYPE_ROTATION, rotation.value)
        elif _is_const_vector_like(rotation):
            rotation = DeferredConstant(TYPE_ROTATION, tuple(rotation))
        elif isinstance(rotation, Value) and rotation.typ == TYPE_VECTOR:
            rotation = _euler_to_rotation(group, rotation, x - 180, y - 120)
        else:
            raise CompileError("instance_on_points rotation= expects Vector in radians")
        deliver_input(group, node.inputs[5], rotation, context="instance_on_points rotation")
    out = Value(node.outputs[0], TYPE_GEOMETRY)
    if realize:
        return _realize_instances(group, out, x + 240, y)
    return out


def _sample_index_geometry(group, geometry, value, index, *, domain, clamp, x=0, y=0):
    """Build one typed Geometry Nodes Sample Index operation from normalized inputs."""
    if not isinstance(geometry, Value) or geometry.typ != TYPE_GEOMETRY:
        raise CompileError("sample_index() first argument must be Geometry")
    if not isinstance(value, Value):
        raise CompileError("sample_index() value must be a runtime field")
    data_type = _attribute_data_type(value.typ)
    if data_type is None:
        raise CompileError("sample_index() value supports Float, Int, Bool and Vector (also Color)")
    domain = normalize_attribute_domain(domain, "sample_index()")
    if type(clamp) is not bool:
        raise CompileError("sample_index() clamp= must be a compile-time Bool")
    if isinstance(index, (Value, DeferredConstant)):
        if index.typ != TYPE_INT:
            raise CompileError("sample_index() index must be Int")
        constant_index = None
    else:
        constant_index = normalize_int_constant(index)

    node = _new_node(group, "GeometryNodeSampleIndex", x, y)
    node.data_type = data_type
    node.domain = domain
    node.clamp = clamp
    group.links.new(geometry.socket, node.inputs["Geometry"])
    deliver_input(group, node.inputs["Value"], value, context="attribute Value")
    if constant_index is None:
        deliver_input(group, node.inputs["Index"], index, context="sample_index() Index")
    else:
        deliver_input(group, node.inputs["Index"], constant_index, context="sample_index() Index")
    return Value(node.outputs["Value"], value.typ)


__all__ = ['_set_int_like_socket', '_set_float_like_socket', '_grid_geometry', '_store_named_attribute_geometry', '_set_material_geometry', '_set_vector_socket_default', '_set_rotation_socket_default', '_euler_to_rotation', '_is_const_number', '_is_const_vector_like', '_cube_geometry', '_join_geometry', '_normalize_points', '_polyline_geometry', '_transform_geometry', '_realize_instances', '_points_geometry', '_set_position_geometry', '_instance_on_points', '_capture_attribute_geometry', '_sample_index_geometry']


def _capture_attribute_geometry(group, geometry, value, *, selection, domain, data_type, x=0, y=0):
    """Capture a field on geometry and return (geometry, anonymous attribute field)."""
    if not isinstance(geometry, Value) or geometry.typ != TYPE_GEOMETRY:
        raise CompileError("capture_attribute() expects Geometry")
    if not isinstance(value, Value):
        raise CompileError("capture_attribute() value must be a runtime field")
    socket_type_to_value_type = {token: typ for typ, token in CAPTURE_ITEM_TYPES.items()}
    socket_type = (data_type.upper() if data_type else CAPTURE_ITEM_TYPES.get(value.typ))
    if socket_type not in socket_type_to_value_type:
        raise CompileError("capture_attribute() supports Float, Int, Bool and Vector values (also Color and Rotation)")
    domain = normalize_attribute_domain(domain, "capture_attribute()")
    if selection is not None and (not isinstance(selection, (Value, DeferredConstant)) or selection.typ != TYPE_BOOL):
        raise CompileError("capture_attribute() selection= must be a Bool expression")
    node = _new_node(group, "GeometryNodeCaptureAttribute", x, y)
    node.domain = domain
    node.capture_items.new(socket_type, "Value")
    group.links.new(geometry.socket, node.inputs["Geometry"])
    if selection is None:
        node.inputs["Selection"].default_value = True
    else:
        group.links.new(selection.socket, node.inputs["Selection"])
    if isinstance(value, Value) and CAPTURE_ITEM_TYPES.get(value.typ) != socket_type:
        # Existing explicit capture type overrides allow Blender to convert a
        # linked field; this is not an implicit raw constant conversion.
        group.links.new(value.socket, node.inputs["Value"])
    else:
        deliver_input(group, node.inputs["Value"], value, context="attribute Value")
    return Value(node.outputs["Geometry"], TYPE_GEOMETRY), Value(node.outputs["Value"], socket_type_to_value_type[socket_type])
