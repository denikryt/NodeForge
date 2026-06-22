"""Backend selection and materialization for L-systems."""

from ...constants import TYPE_GEOMETRY
from ...errors import CompileError
from ...geometry import _set_vector_socket_default
from ...nodes import _new_node
from ...values import Value
from .turtle import _point_to_vector
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
    "limited_segment_node_backend", "static_baked_backend",
]
