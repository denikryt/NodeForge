"""Backend selection and materialization for L-systems."""

from ...constants import TYPE_GEOMETRY
from ...errors import CompileError
from ...geometry import _set_vector_socket_default
from ...nodes import _combine_xyz_mixed, _math, _new_node, _value
from ...values import Value
from .turtle import _as_numeric_value, _point_to_vector
from .runtime_tables import DRAW_MASK_ATTR, HEADING_INDEX_ATTR, MOVE_MASK_ATTR
from . import resources

MAX_LSYSTEM_SEGMENTS = 1000


def select_backend_category(analysis):
    """Return the stable internal backend category for an analyzed L-system."""
    if not analysis.angle_is_runtime and not analysis.step_is_runtime:
        return "static"
    if analysis.has_branches:
        return "branched_runtime"
    return "branch_free_runtime"


def validate_stage1_backend_available(analysis, category: str) -> None:
    """Apply the Stage 1 limited per-segment materialization budget."""
    if analysis.segment_count > MAX_LSYSTEM_SEGMENTS:
        raise CompileError(
            f"L-system {category} backend exceeds Stage 1 MAX_LSYSTEM_SEGMENTS={MAX_LSYSTEM_SEGMENTS}"
        )


def _wire_point(group, point, socket, x, y):
    """Write or link a TurtlePoint into a vector socket."""
    if all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in (point.x, point.y, point.z)):
        _set_vector_socket_default(socket, (point.x, point.y, point.z))
        return
    vec = _point_to_vector(group, point, x, y)
    group.links.new(vec.socket, socket)


def limited_segment_node_backend(comp, segments, x=0, y=0):
    """Materialize turtle segments as bounded Curve Line nodes joined as Geometry."""
    if not segments:
        raise CompileError("Stage 1 L-system backend requires at least one drawn F segment")
    geos = []
    for idx, segment in enumerate(segments):
        sx = x + (idx % 4) * 320
        sy = y - (idx // 4) * 220
        node = _new_node(comp.group, "GeometryNodeCurvePrimitiveLine", sx, sy)
        _wire_point(comp.group, segment.start, node.inputs[0], sx - 180, sy - 40)
        _wire_point(comp.group, segment.end, node.inputs[1], sx - 180, sy - 100)
        geos.append(Value(node.outputs[0], TYPE_GEOMETRY))
    if len(geos) == 1:
        return geos[0]
    join = _new_node(comp.group, "GeometryNodeJoinGeometry", x + 420, y)
    for geo in geos:
        group = comp.group
        group.links.new(geo.socket, join.inputs[0])
    return Value(join.outputs[0], TYPE_GEOMETRY)


def _object_info_geometry_output(node):
    try:
        return node.outputs["Geometry"]
    except Exception:
        pass
    for socket in node.outputs:
        if getattr(socket, "name", "") == "Geometry":
            return socket
    raise CompileError("GeometryNodeObjectInfo has no Geometry output in this Blender runtime")


def _set_object_info_source(node, obj):
    if hasattr(node, "object"):
        try:
            node.object = obj
            return
        except Exception:
            pass
    try:
        node.inputs["Object"].default_value = obj
        return
    except Exception:
        pass
    for socket in node.inputs:
        if getattr(socket, "name", "") == "Object":
            socket.default_value = obj
            return
    raise CompileError("GeometryNodeObjectInfo has no Object source input in this Blender runtime")



def _socket_by_name(sockets, name: str, *, node, direction: str):
    try:
        return sockets[name]
    except Exception:
        pass
    for socket in sockets:
        if getattr(socket, "name", "") == name:
            return socket
    raise CompileError(f"{getattr(node, 'bl_idname', '<node>')} has no {direction} socket {name!r} in this Blender runtime")


def _input_socket(node, name: str):
    return _socket_by_name(node.inputs, name, node=node, direction="input")


def _output_socket(node, name: str):
    return _socket_by_name(node.outputs, name, node=node, direction="output")


def _set_enum(node, attr: str, value: str, diagnostic: str) -> None:
    try:
        setattr(node, attr, value)
    except Exception as exc:
        raise CompileError(f"{diagnostic}: cannot set {attr}={value!r} in this Blender runtime") from exc


def _set_named_attribute_name(node, name: str) -> None:
    try:
        _input_socket(node, "Name").default_value = name
        return
    except Exception:
        pass
    try:
        node.inputs[0].default_value = name
        return
    except Exception as exc:
        raise CompileError("GeometryNodeInputNamedAttribute has no usable Name input in this Blender runtime") from exc


def _named_attribute(group, attr_name: str, data_type: str, typ: str, x=0, y=0):
    node = _new_node(group, "GeometryNodeInputNamedAttribute", x, y)
    _set_enum(node, "data_type", data_type, "Named Attribute node contract unavailable")
    _set_named_attribute_name(node, attr_name)
    try:
        out = _output_socket(node, "Attribute")
    except CompileError:
        # Older builds sometimes expose type-specific output names only.
        fallback = {
            "FLOAT": "Float",
            "BOOLEAN": "Boolean",
            "INT": "Integer",
            "FLOAT_VECTOR": "Vector",
        }.get(data_type)
        if fallback is None:
            raise
        out = _output_socket(node, fallback)
    return Value(out, typ)


def _accumulate_vector(group, value: Value, x=0, y=0):
    if value.typ != "VECTOR":
        raise CompileError("L-system position accumulation expects a Vector field")
    node = _new_node(group, "GeometryNodeAccumulateField", x, y)
    _set_enum(node, "data_type", "FLOAT_VECTOR", "Accumulate Field node vector contract unavailable")
    try:
        _set_enum(node, "domain", "POINT", "Accumulate Field point-domain contract unavailable")
    except CompileError:
        # Blender versions where the node follows the incoming field domain do not expose domain.
        pass
    group.links.new(value.socket, _input_socket(node, "Value"))
    return Value(_output_socket(node, "Leading"), "VECTOR")


def _set_position_geometry(group, geometry: Value, position: Value, x=0, y=0):
    if geometry.typ != "GEOMETRY" or position.typ != "VECTOR":
        raise CompileError("Set Position backend helper received incompatible values")
    node = _new_node(group, "GeometryNodeSetPosition", x, y)
    group.links.new(geometry.socket, _input_socket(node, "Geometry"))
    group.links.new(position.socket, _input_socket(node, "Position"))
    return Value(_output_socket(node, "Geometry"), "GEOMETRY")


def _boolean_not(group, value: Value, x=0, y=0):
    if value.typ != "BOOL":
        raise CompileError("Boolean NOT expects Bool")
    node = _new_node(group, "FunctionNodeBooleanMath", x, y)
    _set_enum(node, "operation", "NOT", "Boolean Math NOT contract unavailable")
    group.links.new(value.socket, _input_socket(node, "Boolean"))
    return Value(_output_socket(node, "Boolean"), "BOOL")


def _delete_edges(group, geometry: Value, selection: Value, x=0, y=0):
    if geometry.typ != "GEOMETRY" or selection.typ != "BOOL":
        raise CompileError("Delete Geometry backend helper received incompatible values")
    node = _new_node(group, "GeometryNodeDeleteGeometry", x, y)
    try:
        _set_enum(node, "domain", "EDGE", "Delete Geometry edge-domain contract unavailable")
    except CompileError:
        pass
    try:
        _set_enum(node, "mode", "ALL", "Delete Geometry mode contract unavailable")
    except CompileError:
        pass
    group.links.new(geometry.socket, _input_socket(node, "Geometry"))
    group.links.new(selection.socket, _input_socket(node, "Selection"))
    return Value(_output_socket(node, "Geometry"), "GEOMETRY")


def _mesh_to_curve(group, geometry: Value, x=0, y=0):
    if geometry.typ != "GEOMETRY":
        raise CompileError("Mesh to Curve backend helper received non-geometry")
    node = _new_node(group, "GeometryNodeMeshToCurve", x, y)
    try:
        _set_enum(node, "mode", "EDGES", "Mesh to Curve edge conversion contract unavailable")
    except CompileError:
        pass
    try:
        group.links.new(geometry.socket, _input_socket(node, "Mesh"))
    except CompileError:
        group.links.new(geometry.socket, _input_socket(node, "Geometry"))
    try:
        out = _output_socket(node, "Curve")
    except CompileError:
        out = _output_socket(node, "Geometry")
    return Value(out, "GEOMETRY")


def _scale_float(group, a: Value, b: Value, x=0, y=0):
    return _math(group, "MULTIPLY", [a, b], x, y)


def branch_free_vectorized_runtime_backend(comp, table, *, angle_degrees, step, x=0, y=0):
    """Materialize branch-free runtime L-systems as a bounded vectorized field graph."""
    if table.draw_count <= 0:
        raise CompileError("Branch-free runtime L-system backend requires at least one drawn F segment")
    tx = getattr(comp, "generated_resource_transaction", None)
    if tx is None:
        tx = resources.create_transaction(comp.group)
        comp.generated_resource_transaction = tx
    _mesh, obj = resources.create_command_mesh_object_from_table(
        tx,
        table,
        name_hint=getattr(comp.group, "name", "NodeForge"),
    )

    object_info = _new_node(comp.group, "GeometryNodeObjectInfo", x, y)
    try:
        object_info.transform_space = "ORIGINAL"
    except Exception:
        pass
    _set_object_info_source(object_info, obj)
    source_geo = Value(_object_info_geometry_output(object_info), "GEOMETRY")

    move_mask = _named_attribute(comp.group, MOVE_MASK_ATTR, "FLOAT", "FLOAT", x - 620, y - 120)
    heading_index = _named_attribute(comp.group, HEADING_INDEX_ATTR, "FLOAT", "FLOAT", x - 620, y - 240)
    draw_mask = _named_attribute(comp.group, DRAW_MASK_ATTR, "BOOLEAN", "BOOL", x + 520, y - 300)

    angle = _as_numeric_value(comp.group, angle_degrees, x - 420, y - 20, "ls_angle")
    step_value = _as_numeric_value(comp.group, step, x - 420, y - 70, "ls_step")
    radians_per_index = _scale_float(comp.group, angle, _value(comp.group, 3.141592653589793 / 180.0, x - 420, y - 120), x - 240, y - 20)
    heading = _scale_float(comp.group, heading_index, radians_per_index, x - 80, y - 80)
    cos_heading = _math(comp.group, "COSINE", [heading], x + 80, y - 60)
    sin_heading = _math(comp.group, "SINE", [heading], x + 80, y - 120)
    distance = _scale_float(comp.group, move_mask, step_value, x - 80, y - 200)
    dx = _scale_float(comp.group, distance, cos_heading, x + 260, y - 80)
    dy = _scale_float(comp.group, distance, sin_heading, x + 260, y - 160)
    delta = _combine_xyz_mixed(comp.group, [dx, dy, 0.0], x + 440, y - 120)
    position = _accumulate_vector(comp.group, delta, x + 620, y - 120)
    positioned = _set_position_geometry(comp.group, source_geo, position, x + 820, y)
    delete_selection = _boolean_not(comp.group, draw_mask, x + 760, y - 300)
    drawn_edges = _delete_edges(comp.group, positioned, delete_selection, x + 1020, y)
    return _mesh_to_curve(comp.group, drawn_edges, x + 1220, y)


def static_baked_backend(comp, segments, x=0, y=0):
    """Materialize static turtle segments as generated Curve/Object IDs and Object Info geometry."""
    if not segments:
        raise CompileError("Static L-system backend requires at least one drawn F segment")
    tx = getattr(comp, "generated_resource_transaction", None)
    if tx is None:
        tx = resources.create_transaction(comp.group)
        comp.generated_resource_transaction = tx
    _curve, obj = resources.create_curve_object_from_segments(tx, segments, name_hint=getattr(comp.group, "name", "NodeForge"))
    node = _new_node(comp.group, "GeometryNodeObjectInfo", x, y)
    try:
        node.transform_space = "ORIGINAL"
    except Exception:
        pass
    _set_object_info_source(node, obj)
    return Value(_object_info_geometry_output(node), TYPE_GEOMETRY)


__all__ = [
    "MAX_LSYSTEM_SEGMENTS", "select_backend_category", "validate_stage1_backend_available",
    "limited_segment_node_backend", "static_baked_backend", "branch_free_vectorized_runtime_backend",
]
