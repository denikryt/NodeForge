"""Backend selection and materialization for L-systems."""

from ...constants import TYPE_BOOL, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_VECTOR
from ...errors import CompileError
from ...nodes import _boolean_math, _combine_xyz_mixed, _compare, _int_value, _math, _new_node, _switch, _value, _vector_math
from ...values import Value
from .turtle import _as_numeric_value
from .runtime_tables import (
    DRAW_MASK_ATTR,
    LOCAL_POSITION_ATTR,
    MARKER_DEPTH_ATTR,
    MARKER_ID_ATTRS,
    MARKER_ITERATION_ATTR,
    MARKER_MASK_ATTR,
    MARKER_TANGENT_ATTR,
    MARKER_PATH_ID_ATTR,
    MOVE_DISTANCE_STATIC_ATTR,
    MOVE_PARAM_INDEX_ATTR,
    PARENT_ATTACH_INDEX_ATTR,
    PATH_DEPTH_ATTR,
    PATH_ID_ATTR,
    TURN_DEGREES_STATIC_ATTR,
    TURN_PARAM_INDEX_ATTR,
    TURN_SIGN_ATTR,
    WORLD_POSITION_ATTR,
)
from .modules import marker_identity

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



def _join_geometry_values(group, geos, x=0, y=0):
    """Join multiple geometry values without depending on generic builtin dispatch."""
    geos = [geo for geo in geos if geo is not None]
    if not geos:
        raise CompileError("L-system backend produced no geometry")
    if len(geos) == 1:
        return geos[0]
    node = _new_node(group, "GeometryNodeJoinGeometry", x, y)
    for geo in geos:
        if geo.typ != TYPE_GEOMETRY:
            raise CompileError("L-system join helper received non-geometry")
        group.links.new(geo.socket, _input_socket(node, "Geometry"))
    return Value(_output_socket(node, "Geometry"), TYPE_GEOMETRY)


def _delete_geometry(group, geometry: Value, selection: Value, *, domain: str, x=0, y=0):
    """Delete geometry elements selected by a boolean field."""
    if geometry.typ != TYPE_GEOMETRY or selection.typ != TYPE_BOOL:
        raise CompileError("Delete Geometry backend helper received incompatible values")
    node = _new_node(group, "GeometryNodeDeleteGeometry", x, y)
    try:
        _set_enum(node, "domain", domain, "Delete Geometry domain contract unavailable")
    except CompileError:
        pass
    try:
        _set_enum(node, "mode", "ALL", "Delete Geometry mode contract unavailable")
    except CompileError:
        pass
    group.links.new(geometry.socket, _input_socket(node, "Geometry"))
    group.links.new(selection.socket, _input_socket(node, "Selection"))
    return Value(_output_socket(node, "Geometry"), TYPE_GEOMETRY)


def _resolve_numeric_arg(group, static_attr: Value, param_index_attr: Value, runtime_params, label: str, x=0, y=0):
    """Resolve a point-domain numeric value from static storage plus runtime parameter slots."""
    if static_attr.typ != TYPE_FLOAT or param_index_attr.typ != TYPE_INT:
        raise CompileError(f"{label} argument resolver received incompatible fields")
    result = static_attr
    for idx, (_name, runtime_value) in enumerate(runtime_params):
        match = _compare(group, "EQUAL", param_index_attr, _int_value(group, idx, x - 160, y - idx * 70), x, y - idx * 70)
        runtime_float = _as_numeric_value(group, runtime_value, x + 40, y - idx * 70, label)
        result = _switch(group, match, result, runtime_float, x + 220, y - idx * 70)
    return result


def _runtime_motion_fields(comp, table, source_geo: Value, *, x=0, y=0, grouped=False):
    """Build common runtime fields for per-token turn and movement data."""
    move_static = _named_attribute(comp.group, MOVE_DISTANCE_STATIC_ATTR, "FLOAT", TYPE_FLOAT, x - 760, y - 120)
    move_index = _named_attribute(comp.group, MOVE_PARAM_INDEX_ATTR, "INT", TYPE_INT, x - 760, y - 200)
    turn_static = _named_attribute(comp.group, TURN_DEGREES_STATIC_ATTR, "FLOAT", TYPE_FLOAT, x - 760, y - 300)
    turn_index = _named_attribute(comp.group, TURN_PARAM_INDEX_ATTR, "INT", TYPE_INT, x - 760, y - 380)
    turn_sign = _named_attribute(comp.group, TURN_SIGN_ATTR, "FLOAT", TYPE_FLOAT, x - 760, y - 460)
    draw_mask = _named_attribute(comp.group, DRAW_MASK_ATTR, "BOOLEAN", TYPE_BOOL, x + 900, y - 360)

    move_distance = _resolve_numeric_arg(comp.group, move_static, move_index, table.runtime_params, "L-system move", x - 420, y - 120)
    turn_value = _resolve_numeric_arg(comp.group, turn_static, turn_index, table.runtime_params, "L-system turn", x - 420, y - 300)
    runtime_turn = _scale_float(comp.group, turn_sign, turn_value, x - 180, y - 300)
    effective_turn = _math(comp.group, "ADD", [turn_static, runtime_turn], x, y - 300)
    radians = _scale_float(comp.group, effective_turn, _value(comp.group, 3.141592653589793 / 180.0, x, y - 360), x + 180, y - 300)
    if grouped:
        path_id = _named_attribute(comp.group, PATH_ID_ATTR, "INT", TYPE_INT, x - 760, y - 540)
        heading = _accumulate_vector_grouped(comp.group, _combine_xyz_mixed(comp.group, [radians, 0.0, 0.0], x + 340, y - 300), path_id, x + 520, y - 300)
        # Reuse vector accumulation to get x component without adding a float grouped helper.
        from ...nodes import _separate_xyz
        heading = _separate_xyz(comp.group, heading, "x", x + 700, y - 300)
    else:
        heading = _accumulate_float(comp.group, radians, x + 360, y - 300)
        path_id = None
    cos_heading = _math(comp.group, "COSINE", [heading], x + 520, y - 120)
    sin_heading = _math(comp.group, "SINE", [heading], x + 520, y - 180)
    dx = _scale_float(comp.group, move_distance, cos_heading, x + 700, y - 120)
    dy = _scale_float(comp.group, move_distance, sin_heading, x + 700, y - 180)
    delta = _combine_xyz_mixed(comp.group, [dx, dy, 0.0], x + 880, y - 140)
    tangent = _combine_xyz_mixed(comp.group, [cos_heading, sin_heading, 0.0], x + 880, y - 260)
    return delta, draw_mask, path_id, tangent


def _accumulate_float(group, value: Value, x=0, y=0):
    """Accumulate a float field on the point domain."""
    if value.typ != TYPE_FLOAT:
        raise CompileError("L-system float accumulation expects a Float field")
    node = _new_node(group, "GeometryNodeAccumulateField", x, y)
    _set_enum(node, "data_type", "FLOAT", "Accumulate Field float contract unavailable")
    try:
        _set_enum(node, "domain", "POINT", "Accumulate Field point-domain contract unavailable")
    except CompileError:
        pass
    group.links.new(value.socket, _input_socket(node, "Value"))
    return Value(_output_socket(node, "Leading"), TYPE_FLOAT)


def _store_runtime_marker_tangent(comp, geometry: Value, tangent: Value, *, x=0, y=0):
    """Store runtime-computed turtle heading on marker points for downstream orientation."""
    keep_markers = _named_attribute(comp.group, MARKER_MASK_ATTR, "BOOLEAN", TYPE_BOOL, x, y)
    return _store_named_attribute_geometry(comp.group, geometry, MARKER_TANGENT_ATTR, tangent, selection=keep_markers, domain="POINT", data_type="FLOAT_VECTOR", x=x + 220, y=y)


def _store_runtime_marker_params(comp, geometry: Value, table, *, x=0, y=0):
    """Apply runtime marker-parameter attributes after generated geometry creation."""
    result = geometry
    for idx, (attr_name, static_values) in enumerate(getattr(table, "marker_param_static", {}).items()):
        index_name = f"nf_lsys_marker_param_index_{attr_name}"
        static_attr = _named_attribute(comp.group, attr_name, "FLOAT", TYPE_FLOAT, x, y - idx * 150)
        index_attr = _named_attribute(comp.group, index_name, "INT", TYPE_INT, x, y - idx * 150 - 60)
        value = _resolve_numeric_arg(comp.group, static_attr, index_attr, table.runtime_params, f"L-system marker parameter {attr_name}", x + 240, y - idx * 150)
        result = _store_named_attribute_geometry(comp.group, result, attr_name, value, domain="POINT", data_type="FLOAT", x=x + 620, y=y - idx * 150)
    return result


def _marker_selection(comp, marker_name: str, *, x=0, y=0):
    ids = marker_identity(marker_name)
    selection = _named_attribute(comp.group, MARKER_MASK_ATTR, "BOOLEAN", TYPE_BOOL, x, y)
    for idx, (attr_name, wanted) in enumerate(zip(MARKER_ID_ATTRS, ids)):
        attr = _named_attribute(comp.group, attr_name, "INT", TYPE_INT, x, y - 80 * (idx + 1))
        eq = _compare(comp.group, "EQUAL", attr, _int_value(comp.group, wanted, x + 200, y - 80 * (idx + 1)), x + 400, y - 80 * (idx + 1))
        selection = _boolean_math(comp.group, "AND", [selection, eq], x + 600, y - 80 * (idx + 1))
    return selection


def filter_marker_points(comp, geometry: Value, marker_name: str, *, x=0, y=0):
    """Return only point-domain marker geometry matching *marker_name*."""
    if geometry.typ != TYPE_GEOMETRY:
        raise CompileError("ls_points() first argument must be Geometry")
    keep = _marker_selection(comp, marker_name, x=x - 620, y=y)
    delete = _boolean_not(comp.group, keep, x=x + 40, y=y)
    return _delete_geometry(comp.group, geometry, delete, domain="POINT", x=x + 240, y=y)


def _object_geometry_from_generated(comp, obj, *, x=0, y=0):
    node = _new_node(comp.group, "GeometryNodeObjectInfo", x, y)
    try:
        node.transform_space = "ORIGINAL"
    except Exception:
        pass
    _set_object_info_source(node, obj)
    return Value(_object_info_geometry_output(node), TYPE_GEOMETRY)


def branch_free_vectorized_runtime_backend(comp, table, *, angle_degrees, step, x=0, y=0):
    """Materialize branch-free runtime L-systems as a bounded vectorized field graph."""
    if table.draw_count <= 0 and table.marker_count <= 0:
        raise CompileError("Branch-free runtime L-system backend requires at least one drawn F segment")
    from . import resources

    tx = getattr(comp, "generated_resource_transaction", None)
    if tx is None:
        tx = resources.create_transaction(comp.group)
        comp.generated_resource_transaction = tx
    _mesh, obj = resources.create_command_mesh_object_from_table(tx, table, name_hint=getattr(comp.group, "name", "NodeForge"))
    source_geo = _object_geometry_from_generated(comp, obj, x=x, y=y)
    delta, draw_mask, _, tangent = _runtime_motion_fields(comp, table, source_geo, x=x, y=y, grouped=False)
    position = _accumulate_vector(comp.group, delta, x + 1080, y - 140)
    positioned = _set_position_geometry(comp.group, source_geo, position, x + 1280, y)
    positioned = _store_runtime_marker_tangent(comp, positioned, tangent, x=x + 1280, y=y - 500)
    positioned = _store_runtime_marker_params(comp, positioned, table, x=x + 1280, y=y - 700)
    geos = []
    if table.draw_count > 0:
        delete_edges = _boolean_not(comp.group, draw_mask, x + 1220, y - 360)
        drawn_edges = _delete_edges(comp.group, positioned, delete_edges, x + 1480, y)
        geos.append(_mesh_to_curve(comp.group, drawn_edges, x + 1680, y))
    if table.marker_count > 0:
        keep_markers = _named_attribute(comp.group, MARKER_MASK_ATTR, "BOOLEAN", TYPE_BOOL, x + 1220, y - 620)
        delete_nonmarkers = _boolean_not(comp.group, keep_markers, x + 1420, y - 620)
        geos.append(_delete_geometry(comp.group, positioned, delete_nonmarkers, domain="POINT", x=x + 1680, y=y - 260))
    return _join_geometry_values(comp.group, geos, x + 1900, y)


def branch_aware_vectorized_runtime_backend(comp, table, *, angle_degrees, step, x=0, y=0):
    """Materialize branched runtime L-systems as a bounded depth-unrolled field graph."""
    if table.draw_count <= 0 and table.marker_count <= 0:
        raise CompileError("Branch-aware runtime L-system backend requires at least one drawn F segment")
    if table.max_branch_depth > MAX_LSYSTEM_BRANCH_DEPTH:
        raise CompileError(f"L-system branched_runtime backend exceeds MAX_LSYSTEM_BRANCH_DEPTH={MAX_LSYSTEM_BRANCH_DEPTH}")
    from . import resources

    tx = getattr(comp, "generated_resource_transaction", None)
    if tx is None:
        tx = resources.create_transaction(comp.group)
        comp.generated_resource_transaction = tx
    _mesh, obj = resources.create_branch_aware_command_mesh_object_from_table(tx, table, name_hint=getattr(comp.group, "name", "NodeForge"))
    source_geo = _object_geometry_from_generated(comp, obj, x=x, y=y)
    delta, draw_mask, path_id, tangent = _runtime_motion_fields(comp, table, source_geo, x=x, y=y, grouped=True)
    path_depth = _named_attribute(comp.group, PATH_DEPTH_ATTR, "INT", TYPE_INT, x - 760, y - 620)
    parent_attach_index = _named_attribute(comp.group, PARENT_ATTACH_INDEX_ATTR, "INT", TYPE_INT, x - 760, y - 700)
    local_position = _accumulate_vector_grouped(comp.group, delta, path_id, x + 1080, y - 140)
    geometry = _store_named_attribute_geometry(comp.group, source_geo, LOCAL_POSITION_ATTR, local_position, domain="POINT", data_type="FLOAT_VECTOR", x=x + 1280, y=y - 140)
    root_depth = _compare(comp.group, "EQUAL", path_depth, _int_value(comp.group, 0, x + 1080, y - 620), x + 1280, y - 620)
    safe_parent_attach_index = _switch(comp.group, root_depth, parent_attach_index, _int_value(comp.group, 0, x + 1280, y - 700), x + 1480, y - 700)
    zero = _zero_vector(comp.group, x + 1280, y - 540)
    initial_world = _switch(comp.group, root_depth, zero, local_position, x + 1480, y - 300)
    geometry = _store_named_attribute_geometry(comp.group, geometry, WORLD_POSITION_ATTR, initial_world, domain="POINT", data_type="FLOAT_VECTOR", x=x + 1680, y=y - 160)
    for depth in range(1, table.max_branch_depth + 1):
        row_y = y - 180 - depth * 170
        current_world = _named_attribute(comp.group, WORLD_POSITION_ATTR, "FLOAT_VECTOR", TYPE_VECTOR, x + 1500, row_y)
        parent_world = _sample_index_vector(comp.group, geometry, current_world, safe_parent_attach_index, x + 1700, row_y)
        candidate_world = _vector_math(comp.group, "ADD", [parent_world, local_position], TYPE_VECTOR, x + 1900, row_y)
        depth_match = _compare(comp.group, "EQUAL", path_depth, _int_value(comp.group, depth, x + 1700, row_y - 70), x + 1900, row_y - 70)
        geometry = _store_named_attribute_geometry(comp.group, geometry, WORLD_POSITION_ATTR, candidate_world, selection=depth_match, domain="POINT", data_type="FLOAT_VECTOR", x=x + 2100, y=row_y)
    final_world = _named_attribute(comp.group, WORLD_POSITION_ATTR, "FLOAT_VECTOR", TYPE_VECTOR, x + 2300, y - 120)
    positioned = _set_position_geometry(comp.group, geometry, final_world, x + 2500, y)
    positioned = _store_runtime_marker_tangent(comp, positioned, tangent, x=x + 2500, y=y - 500)
    positioned = _store_runtime_marker_params(comp, positioned, table, x=x + 2500, y=y - 700)
    geos = []
    if table.draw_count > 0:
        delete_edges = _boolean_not(comp.group, draw_mask, x + 2440, y - 360)
        drawn_edges = _delete_edges(comp.group, positioned, delete_edges, x + 2700, y)
        geos.append(_mesh_to_curve(comp.group, drawn_edges, x + 2900, y))
    if table.marker_count > 0:
        keep_markers = _named_attribute(comp.group, MARKER_MASK_ATTR, "BOOLEAN", TYPE_BOOL, x + 2440, y - 620)
        delete_nonmarkers = _boolean_not(comp.group, keep_markers, x + 2640, y - 620)
        geos.append(_delete_geometry(comp.group, positioned, delete_nonmarkers, domain="POINT", x=x + 2900, y=y - 260))
    return _join_geometry_values(comp.group, geos, x + 3120, y)


def static_baked_backend(comp, interpretation, x=0, y=0):
    """Materialize static turtle segments and marker points as generated IDs."""
    segments = getattr(interpretation, "segments", interpretation)
    markers = getattr(interpretation, "markers", ())
    if not segments and not markers:
        raise CompileError("Static L-system backend requires at least one drawn F segment")
    from . import resources

    tx = getattr(comp, "generated_resource_transaction", None)
    if tx is None:
        tx = resources.create_transaction(comp.group)
        comp.generated_resource_transaction = tx
    geos = []
    if segments:
        _curve, obj = resources.create_curve_object_from_segments(tx, segments, name_hint=getattr(comp.group, "name", "NodeForge"))
        geos.append(_object_geometry_from_generated(comp, obj, x=x, y=y))
    if markers:
        _mesh, obj, runtime_slots = resources.create_marker_mesh_object_from_points(tx, markers, name_hint=getattr(comp.group, "name", "NodeForge"))
        marker_geo = _object_geometry_from_generated(comp, obj, x=x, y=y - 180)
        table = type("StaticMarkerParamTable", (), {
            "runtime_params": runtime_slots,
            "marker_param_static": {name: () for marker in markers for name in marker.parameters},
        })()
        marker_geo = _store_runtime_marker_params(comp, marker_geo, table, x=x + 260, y=y - 360)
        geos.append(marker_geo)
    return _join_geometry_values(comp.group, geos, x + 260, y)


__all__ = [
    "MAX_LSYSTEM_BRANCH_DEPTH", "select_backend_category", "validate_branch_aware_backend_available",
    "static_baked_backend", "branch_free_vectorized_runtime_backend", "branch_aware_vectorized_runtime_backend", "filter_marker_points",
]
