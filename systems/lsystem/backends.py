"""Backend selection and materialization for L-systems."""

from ...constants import TYPE_BOOL, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_VECTOR
from ...errors import CompileError
from ...nodes import _combine_xyz_mixed, _compare, _int_value, _math, _new_node, _switch, _value, _vector_math
from ...values import Value
from .turtle import _as_numeric_value
from .runtime_tables import (
    DRAW_MASK_ATTR,
    HEADING_INDEX_ATTR,
    LOCAL_POSITION_ATTR,
    MOVE_MASK_ATTR,
    PARENT_ATTACH_INDEX_ATTR,
    PATH_DEPTH_ATTR,
    PATH_ID_ATTR,
    WORLD_POSITION_ATTR,
)
from . import resources

MAX_LSYSTEM_BRANCH_DEPTH = 32


def select_backend_category(analysis):
    """Return the stable internal backend category for an analyzed L-system."""
    if not analysis.angle_is_runtime and not analysis.step_is_runtime:
        return "static"
    if analysis.has_branches:
        return "branched_runtime"
    return "branch_free_runtime"


def validate_branch_aware_backend_available(analysis, category: str) -> None:
    """Apply the branch-aware runtime branch-depth materialization budget."""
    if category != "branched_runtime":
        return
    if analysis.max_branch_depth > MAX_LSYSTEM_BRANCH_DEPTH:
        raise CompileError(
            f"L-system branched_runtime backend exceeds MAX_LSYSTEM_BRANCH_DEPTH={MAX_LSYSTEM_BRANCH_DEPTH}"
        )

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




def _accumulate_vector_grouped(group, value: Value, group_id: Value, x=0, y=0):
    if value.typ != TYPE_VECTOR or group_id.typ != TYPE_INT:
        raise CompileError("L-system grouped accumulation expects Vector value and Int group id")
    node = _new_node(group, "GeometryNodeAccumulateField", x, y)
    _set_enum(node, "data_type", "FLOAT_VECTOR", "Accumulate Field grouped-vector contract unavailable")
    try:
        _set_enum(node, "domain", "POINT", "Accumulate Field point-domain contract unavailable")
    except CompileError:
        pass
    group.links.new(value.socket, _input_socket(node, "Value"))
    try:
        group.links.new(group_id.socket, _input_socket(node, "Group ID"))
    except CompileError as exc:
        raise CompileError("branched_runtime backend unavailable: Accumulate Field has no Group ID input") from exc
    return Value(_output_socket(node, "Leading"), TYPE_VECTOR)


def _zero_vector(group, x=0, y=0):
    return _combine_xyz_mixed(group, [0.0, 0.0, 0.0], x, y)


def _store_named_attribute_geometry(group, geometry: Value, attr_name: str, value: Value, *, selection: Value | None = None, domain="POINT", data_type="FLOAT_VECTOR", x=0, y=0):
    if geometry.typ != TYPE_GEOMETRY:
        raise CompileError("Store Named Attribute backend helper received non-geometry")
    node = _new_node(group, "GeometryNodeStoreNamedAttribute", x, y)
    _set_enum(node, "data_type", data_type, "Store Named Attribute data-type contract unavailable")
    try:
        _set_enum(node, "domain", domain, "Store Named Attribute domain contract unavailable")
    except CompileError:
        pass
    group.links.new(geometry.socket, _input_socket(node, "Geometry"))
    try:
        _input_socket(node, "Selection").default_value = True
    except CompileError:
        pass
    if selection is not None:
        if selection.typ != TYPE_BOOL:
            raise CompileError("Store Named Attribute selection expects Bool")
        group.links.new(selection.socket, _input_socket(node, "Selection"))
    try:
        _input_socket(node, "Name").default_value = attr_name
    except CompileError:
        try:
            node.inputs[2].default_value = attr_name
        except Exception as exc:
            raise CompileError("Store Named Attribute has no usable Name input") from exc
    try:
        group.links.new(value.socket, _input_socket(node, "Value"))
    except CompileError:
        group.links.new(value.socket, node.inputs[3])
    return Value(_output_socket(node, "Geometry"), TYPE_GEOMETRY)


def _sample_index_vector(group, geometry: Value, value: Value, index: Value, x=0, y=0):
    if geometry.typ != TYPE_GEOMETRY or value.typ != TYPE_VECTOR or index.typ != TYPE_INT:
        raise CompileError("Sample Index backend helper received incompatible values")
    node = _new_node(group, "GeometryNodeSampleIndex", x, y)
    _set_enum(node, "data_type", "FLOAT_VECTOR", "Sample Index vector contract unavailable")
    try:
        _set_enum(node, "domain", "POINT", "Sample Index point-domain contract unavailable")
    except CompileError:
        pass
    group.links.new(geometry.socket, _input_socket(node, "Geometry"))
    try:
        group.links.new(value.socket, _input_socket(node, "Value"))
    except CompileError:
        # Some runtimes expose type-specific value inputs after data_type is set.
        group.links.new(value.socket, _input_socket(node, "Vector"))
    group.links.new(index.socket, _input_socket(node, "Index"))
    try:
        out = _output_socket(node, "Value")
    except CompileError:
        out = _output_socket(node, "Vector")
    return Value(out, TYPE_VECTOR)

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




def branch_aware_vectorized_runtime_backend(comp, table, *, angle_degrees, step, x=0, y=0):
    """Materialize branched runtime L-systems as a bounded depth-unrolled field graph."""
    if table.draw_count <= 0:
        raise CompileError("Branch-aware runtime L-system backend requires at least one drawn F segment")
    if table.max_branch_depth > MAX_LSYSTEM_BRANCH_DEPTH:
        raise CompileError(
            f"L-system branched_runtime backend exceeds MAX_LSYSTEM_BRANCH_DEPTH={MAX_LSYSTEM_BRANCH_DEPTH}"
        )
    tx = getattr(comp, "generated_resource_transaction", None)
    if tx is None:
        tx = resources.create_transaction(comp.group)
        comp.generated_resource_transaction = tx
    _mesh, obj = resources.create_branch_aware_command_mesh_object_from_table(
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
    source_geo = Value(_object_info_geometry_output(object_info), TYPE_GEOMETRY)

    move_mask = _named_attribute(comp.group, MOVE_MASK_ATTR, "FLOAT", TYPE_FLOAT, x - 760, y - 120)
    heading_index = _named_attribute(comp.group, HEADING_INDEX_ATTR, "FLOAT", TYPE_FLOAT, x - 760, y - 240)
    path_id = _named_attribute(comp.group, PATH_ID_ATTR, "INT", TYPE_INT, x - 760, y - 360)
    path_depth = _named_attribute(comp.group, PATH_DEPTH_ATTR, "INT", TYPE_INT, x - 760, y - 480)
    parent_attach_index = _named_attribute(comp.group, PARENT_ATTACH_INDEX_ATTR, "INT", TYPE_INT, x - 760, y - 600)
    draw_mask = _named_attribute(comp.group, DRAW_MASK_ATTR, "BOOLEAN", TYPE_BOOL, x + 1160, y - 360)

    angle = _as_numeric_value(comp.group, angle_degrees, x - 560, y - 20, "ls_angle")
    step_value = _as_numeric_value(comp.group, step, x - 560, y - 70, "ls_step")
    radians_per_index = _scale_float(comp.group, angle, _value(comp.group, 3.141592653589793 / 180.0, x - 560, y - 120), x - 380, y - 20)
    heading = _scale_float(comp.group, heading_index, radians_per_index, x - 220, y - 80)
    cos_heading = _math(comp.group, "COSINE", [heading], x - 60, y - 60)
    sin_heading = _math(comp.group, "SINE", [heading], x - 60, y - 120)
    distance = _scale_float(comp.group, move_mask, step_value, x - 220, y - 200)
    dx = _scale_float(comp.group, distance, cos_heading, x + 120, y - 80)
    dy = _scale_float(comp.group, distance, sin_heading, x + 120, y - 160)
    delta = _combine_xyz_mixed(comp.group, [dx, dy, 0.0], x + 300, y - 120)
    local_position = _accumulate_vector_grouped(comp.group, delta, path_id, x + 500, y - 120)
    geometry = _store_named_attribute_geometry(
        comp.group,
        source_geo,
        LOCAL_POSITION_ATTR,
        local_position,
        domain="POINT",
        data_type="FLOAT_VECTOR",
        x=x + 700,
        y=y - 120,
    )

    root_depth = _compare(comp.group, "EQUAL", path_depth, _int_value(comp.group, 0, x + 500, y - 430), x + 700, y - 430)
    safe_parent_attach_index = _switch(
        comp.group,
        root_depth,
        parent_attach_index,
        _int_value(comp.group, 0, x + 700, y - 600),
        x + 900,
        y - 600,
    )
    zero = _zero_vector(comp.group, x + 700, y - 520)
    initial_world = _switch(comp.group, root_depth, zero, local_position, x + 900, y - 300)
    geometry = _store_named_attribute_geometry(
        comp.group,
        geometry,
        WORLD_POSITION_ATTR,
        initial_world,
        domain="POINT",
        data_type="FLOAT_VECTOR",
        x=x + 1100,
        y=y - 160,
    )

    for depth in range(1, table.max_branch_depth + 1):
        row_y = y - 180 - depth * 170
        current_world = _named_attribute(comp.group, WORLD_POSITION_ATTR, "FLOAT_VECTOR", TYPE_VECTOR, x + 920, row_y)
        parent_world = _sample_index_vector(comp.group, geometry, current_world, safe_parent_attach_index, x + 1120, row_y)
        candidate_world = _vector_math(comp.group, "ADD", [parent_world, local_position], TYPE_VECTOR, x + 1320, row_y)
        depth_match = _compare(comp.group, "EQUAL", path_depth, _int_value(comp.group, depth, x + 1120, row_y - 70), x + 1320, row_y - 70)
        geometry = _store_named_attribute_geometry(
            comp.group,
            geometry,
            WORLD_POSITION_ATTR,
            candidate_world,
            selection=depth_match,
            domain="POINT",
            data_type="FLOAT_VECTOR",
            x=x + 1520,
            y=row_y,
        )

    final_world = _named_attribute(comp.group, WORLD_POSITION_ATTR, "FLOAT_VECTOR", TYPE_VECTOR, x + 1720, y - 120)
    positioned = _set_position_geometry(comp.group, geometry, final_world, x + 1920, y)
    delete_selection = _boolean_not(comp.group, draw_mask, x + 1860, y - 360)
    drawn_edges = _delete_edges(comp.group, positioned, delete_selection, x + 2120, y)
    return _mesh_to_curve(comp.group, drawn_edges, x + 2320, y)

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
    "MAX_LSYSTEM_BRANCH_DEPTH", "select_backend_category",
    "validate_branch_aware_backend_available",
    "static_baked_backend", "branch_free_vectorized_runtime_backend",
    "branch_aware_vectorized_runtime_backend",
]
