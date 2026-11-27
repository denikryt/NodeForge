"""Blender GeometryNodeTree publication and rollback backend.

The compiler supplies population of an already-created fresh group. This module
owns physical datablock creation, replacement/cutover, nested transaction
journaling, and generated-resource commit/rollback coordination.
"""

from __future__ import annotations

import uuid
import warnings
from collections import Counter
from dataclasses import replace
from typing import Callable

import bpy

from ..errors import CompileError
from ..compiler_identities import GroupCompilationIdentity
from .group_build_request import BlenderGroupBuildRequest
from .storage import _reset_node_group
from .interface import NAMED_RESULTS_PROP, has_named_results
from .update import (
    _capture_group_external_state,
    _restore_group_external_state,
    _validate_group_external_state_for_replacement,
)
from . import generated_resources
from .group_authority import (
    BACKEND_PRIVATE_GROUP_PROPERTIES,
    forget as forget_provisional,
    is_authority_ineligible_group,
    mark_provisional,
    mark_transaction_private,
    publish as publish_provisional,
)
from ..function_instances import (
    FUNCTION_ROOT_OWNER_ID_PROP,
    FunctionCompilationTrace,
    function_group_owner_scope as make_function_group_owner_scope,
    interface_contract,
    new_root_owner_id,
    stored_fingerprint,
    stored_interface_contract,
    validate_root_owner_id,
)

_TEST_CUTOVER_FAIL_AFTER_RESET = False
_TEST_BACKUP_COPY_FAIL_AFTER_RESET = False


def _copy_custom_properties(src, dst, *, strict=False):
    """Copy ordinary properties while preserving destination-owned backend state."""
    def _handle(exc):
        if strict:
            raise exc

    preserved = {}
    for key in BACKEND_PRIVATE_GROUP_PROPERTIES:
        try:
            if key in dst.keys():
                preserved[key] = dst[key]
        except Exception as exc:
            _handle(exc)

    try:
        keys = list(dst.keys())
    except Exception as exc:
        _handle(exc)
        keys = []
    for key in keys:
        if key in BACKEND_PRIVATE_GROUP_PROPERTIES:
            continue
        try:
            del dst[key]
        except Exception as exc:
            _handle(exc)

    try:
        src_keys = list(src.keys())
    except Exception as exc:
        _handle(exc)
        src_keys = []
    for key in src_keys:
        if key in BACKEND_PRIVATE_GROUP_PROPERTIES:
            continue
        try:
            dst[key] = src[key]
        except Exception as exc:
            _handle(exc)

    for key, value in preserved.items():
        try:
            dst[key] = value
        except Exception as exc:
            _handle(exc)


def _copy_socket_default(src_socket, dst_socket):
    """Copy a socket default value when Blender exposes one."""
    if not hasattr(src_socket, "default_value") or not hasattr(dst_socket, "default_value"):
        return
    try:
        value = src_socket.default_value
        try:
            dst_socket.default_value = value
        except Exception:
            for index, component in enumerate(value):
                dst_socket.default_value[index] = component
    except Exception:
        pass


def _copy_curve_mapping(src_mapping, dst_mapping):
    """Copy Blender CurveMapping state, including manually edited curve points."""
    if src_mapping is None or dst_mapping is None:
        return
    try:
        props = src_mapping.bl_rna.properties
    except Exception:
        props = []
    for prop in props:
        ident = getattr(prop, "identifier", "")
        if not ident or ident in {"rna_type", "curves"} or getattr(prop, "is_readonly", False):
            continue
        try:
            value = getattr(src_mapping, ident)
            try:
                setattr(dst_mapping, ident, value)
            except Exception:
                target = getattr(dst_mapping, ident)
                for index, component in enumerate(value):
                    target[index] = component
        except Exception:
            pass

    for src_curve, dst_curve in zip(src_mapping.curves, dst_mapping.curves):
        src_points = list(src_curve.points)
        dst_points = dst_curve.points
        # CurveMapping starts with two endpoint points. Remove any previous
        # interior points, then restore endpoints and recreate source interiors.
        try:
            while len(dst_points) > 2:
                dst_points.remove(dst_points[1])
        except Exception:
            pass
        if not src_points or len(dst_points) < 2:
            continue
        endpoints = ((dst_points[0], src_points[0]), (dst_points[-1], src_points[-1]))
        for dst_point, src_point in endpoints:
            try:
                dst_point.location = tuple(src_point.location)
                dst_point.handle_type = src_point.handle_type
                dst_point.select = bool(src_point.select)
            except Exception:
                pass
        for src_point in src_points[1:-1]:
            try:
                point = dst_points.new(float(src_point.location[0]), float(src_point.location[1]))
                point.handle_type = src_point.handle_type
                point.select = bool(src_point.select)
            except Exception:
                pass
    try:
        dst_mapping.update()
    except Exception:
        pass


def _copy_node_properties(src_node, dst_node, *, proofs=None):
    """Copy writable RNA/custom properties needed by NodeForge-generated nodes."""
    skip = {"rna_type", "type", "dimensions", "inputs", "outputs", "internal_links", "select", "width_hidden", "height"}
    try:
        props = src_node.bl_rna.properties
    except Exception:
        props = []
    for prop in props:
        ident = getattr(prop, "identifier", "")
        if not ident or ident in skip or getattr(prop, "is_readonly", False):
            continue
        try:
            setattr(dst_node, ident, getattr(src_node, ident))
        except Exception:
            pass
    try:
        dst_node.location = tuple(src_node.location)
    except Exception:
        pass
    try:
        dst_node.name = src_node.name
    except Exception:
        pass
    try:
        dst_node.label = src_node.label
    except Exception:
        pass
    _copy_custom_properties(src_node, dst_node)
    if _has_local_collection(src_node):
        _copy_standalone_collection(src_node, dst_node, proofs=proofs)
        return
    for index, src_socket in enumerate(src_node.inputs):
        if index < len(dst_node.inputs):
            _copy_socket_default(src_socket, dst_node.inputs[index])
    for index, src_socket in enumerate(src_node.outputs):
        if index < len(dst_node.outputs):
            _copy_socket_default(src_socket, dst_node.outputs[index])


def _sync_curve_mapping_state(src_group, node_map):
    """Restore CurveMapping state after sockets and links have been rebuilt."""
    for src_node in src_group.nodes:
        dst_node = node_map.get(src_node.name)
        if dst_node is None or not hasattr(src_node, "mapping") or not hasattr(dst_node, "mapping"):
            continue
        _copy_curve_mapping(src_node.mapping, dst_node.mapping)


def _copy_interface(src_group, dst_group):
    """Copy sockets and native panel hierarchy between Geometry Node interfaces."""

    def item_pointer(item):
        try:
            return int(item.as_pointer())
        except Exception:
            return id(item)

    copied_items = {}
    child_positions = {}
    for item in getattr(src_group.interface, "items_tree", []):
        item_type = getattr(item, "item_type", None)
        src_parent = getattr(item, "parent", None)
        dst_parent = copied_items.get(item_pointer(src_parent)) if src_parent is not None else None
        parent_key = item_pointer(dst_parent) if dst_parent is not None else None
        position = child_positions.get(parent_key, 0)

        if item_type == "PANEL":
            copied = dst_group.interface.new_panel(
                name=item.name,
                description=getattr(item, "description", "") or "",
                default_closed=bool(getattr(item, "default_closed", False)),
            )
            if dst_parent is not None:
                dst_group.interface.move_to_parent(copied, dst_parent, position)
        elif item_type == "SOCKET":
            kwargs = {
                "name": item.name,
                "description": getattr(item, "description", "") or "",
                "in_out": item.in_out,
            }
            try:
                copied = dst_group.interface.new_socket(socket_type=item.socket_type, **kwargs)
            except Exception:
                copied = dst_group.interface.new_socket(
                    socket_type=getattr(item, "bl_socket_idname", "NodeSocketFloat"),
                    **kwargs,
                )
            if dst_parent is not None:
                dst_group.interface.move_to_parent(copied, dst_parent, position)
            _copy_socket_default(item, copied)
        else:
            continue

        copied_items[item_pointer(item)] = copied
        child_positions[parent_key] = position + 1


def _copy_group_contents(src_group, dst_group, *, copy_role=None):
    """Replace ``dst_group`` contents while preserving backend-private state.

    ``copy_role`` is used only by Blender regression failure injection so backup
    construction and destructive authoritative cutover can be tested separately.
    """
    _validate_collection_copy_source(src_group)
    _reset_node_group(dst_group)
    global _TEST_CUTOVER_FAIL_AFTER_RESET, _TEST_BACKUP_COPY_FAIL_AFTER_RESET
    if copy_role == "cutover" and _TEST_CUTOVER_FAIL_AFTER_RESET:
        _TEST_CUTOVER_FAIL_AFTER_RESET = False
        raise RuntimeError("Injected NodeForge cutover failure after destructive reset")
    if copy_role == "backup" and _TEST_BACKUP_COPY_FAIL_AFTER_RESET:
        _TEST_BACKUP_COPY_FAIL_AFTER_RESET = False
        raise RuntimeError("Injected NodeForge rollback-backup copy failure after reset")
    _copy_interface(src_group, dst_group)
    from . import raw_nodes as _raw_nodes

    node_map = {}
    proofs = {}
    for src_node in src_group.nodes:
        dst_node = dst_group.nodes.new(src_node.bl_idname)
        if _raw_nodes.is_raw_node(src_node):
            _raw_nodes.copy_raw_node_properties(src_node, dst_node, proofs=proofs)
        else:
            _copy_node_properties(src_node, dst_node, proofs=proofs)
        node_map[src_node.name] = dst_node
    _sync_repeat_zone_dynamic_items(src_group, node_map)
    _sync_bundle_dynamic_items(src_group, node_map)

    copied_edges = []
    for src_link in src_group.links:
        from_node = node_map.get(src_link.from_node.name)
        to_node = node_map.get(src_link.to_node.name)
        if from_node is None or to_node is None:
            continue
        if (_has_local_collection(src_link.from_node)
                or _has_local_collection(src_link.to_node)):
            from_socket = _copy_link_endpoint(src_link.from_node, src_link.from_socket, from_node, output=True, proofs=proofs)
            to_socket = _copy_link_endpoint(src_link.to_node, src_link.to_socket, to_node, output=False, proofs=proofs)
            copied_edges.append((src_link, from_socket, to_socket))
            link = dst_group.links.new(from_socket, to_socket)
            if link.from_socket != from_socket or link.to_socket != to_socket or not link.is_valid:
                raise CompileError("Collection copy: Blender did not preserve the linked endpoints")
            continue
        if _raw_nodes.is_raw_node(src_link.from_node) or _raw_nodes.is_raw_node(src_link.to_node):
            from_socket = _raw_nodes.resolve_cutover_socket(src_link.from_node, src_link.from_socket, from_node, direction="output")
            to_socket = _raw_nodes.resolve_cutover_socket(src_link.to_node, src_link.to_socket, to_node, direction="input")
            dst_group.links.new(from_socket, to_socket)
            copied_edges.append((src_link, from_socket, to_socket))
            continue
        try:
            from_index = list(src_link.from_node.outputs).index(src_link.from_socket)
            to_index = list(src_link.to_node.inputs).index(src_link.to_socket)
            from_socket, to_socket = from_node.outputs[from_index], to_node.inputs[to_index]
            dst_group.links.new(from_socket, to_socket)
        except Exception:
            # Name fallback for ordinary dynamic sockets only.
            from_socket, to_socket = from_node.outputs[src_link.from_socket.name], to_node.inputs[src_link.to_socket.name]
            dst_group.links.new(from_socket, to_socket)
        copied_edges.append((src_link, from_socket, to_socket))
    for src_node in src_group.nodes:
        dst_node = node_map[src_node.name]
        if _has_local_collection(src_node):
            _verify_standalone_copy(src_node, dst_node, proofs=proofs)
            _verify_standalone_defaults(src_node, dst_node)
            if _raw_nodes.is_raw_node(src_node):
                _raw_nodes.validate_collection_raw_copy(src_node, dst_node, proofs=proofs)
        if _raw_nodes.is_raw_node(dst_node):
            _raw_nodes.validate_raw_node_after_cutover(dst_node, dst_group)
    _verify_collection_edges(dst_group, copied_edges,
                             [node_map[node.name] for node in src_group.nodes if _has_local_collection(node)])
    # CurveMapping can be reset by Blender while node sockets/topology are being
    # reconstructed, so restore it only after the final links are in place.
    _sync_curve_mapping_state(src_group, node_map)
    try:
        dst_group.color_tag = src_group.color_tag
    except Exception:
        pass
    try:
        dst_group.description = src_group.description
    except Exception:
        pass
    _copy_custom_properties(src_group, dst_group, strict=True)
    if (src_group.get(NAMED_RESULTS_PROP) != dst_group.get(NAMED_RESULTS_PROP)
            or (NAMED_RESULTS_PROP in src_group) != (NAMED_RESULTS_PROP in dst_group)):
        raise CompileError("Group result shape marker changed during copy")




def _sync_bundle_dynamic_items(src_group, node_map):
    """Recreate Bundle node dynamic items before restoring copied links.

    Combine/Separate Bundle item collections define their dynamic sockets and
    are not recreated by generic RNA property copying. Rebuild the collection
    first so transactional cutover can restore links by socket index/name. Any
    failure is fatal: swallowing it could commit a group with a silently
    truncated Bundle signature when an item happens to be unlinked.
    """
    for src_node in src_group.nodes:
        if getattr(src_node, "bl_idname", None) not in {"NodeCombineBundle", "NodeSeparateBundle"}:
            continue
        dst_node = node_map.get(src_node.name)
        if dst_node is None:
            raise CompileError(f"Bundle cutover lost destination node {src_node.name!r}")
        if not hasattr(src_node, "bundle_items") or not hasattr(dst_node, "bundle_items"):
            raise CompileError(
                f"Bundle node {src_node.name!r} does not expose bundle_items during transactional update"
            )
        try:
            dst_node.bundle_items.clear()
        except Exception:
            try:
                for item in list(dst_node.bundle_items):
                    dst_node.bundle_items.remove(item)
            except Exception as exc:
                raise CompileError(
                    f"Failed to reset Bundle items for node {src_node.name!r}: {exc}"
                ) from exc

        for src_item in list(src_node.bundle_items):
            try:
                dst_item = dst_node.bundle_items.new(src_item.socket_type, src_item.name)
                if hasattr(src_item, "structure_type") and hasattr(dst_item, "structure_type"):
                    dst_item.structure_type = src_item.structure_type
            except Exception as exc:
                raise CompileError(
                    f"Failed to recreate Bundle item {src_item.name!r} on node {src_node.name!r}: {exc}"
                ) from exc

        # Items rebuild their input/output sockets after generic RNA copying.
        # Restore their defaults now, otherwise successful updates silently
        # replace unlinked constant values with Blender's initial zero values.
        for direction in ("inputs", "outputs"):
            source = getattr(src_node, direction)
            target = getattr(dst_node, direction)
            if len(source) != len(target):
                raise CompileError(f"Bundle cutover changed {direction} count on {src_node.name!r}")
            for src_socket, dst_socket in zip(source, target):
                if (src_socket.name != dst_socket.name
                        or src_socket.bl_idname != dst_socket.bl_idname):
                    raise CompileError(f"Bundle cutover changed {direction} socket type/name on {src_node.name!r}")
                if not hasattr(src_socket, "default_value"):
                    continue
                _copy_socket_default(src_socket, dst_socket)
                before = src_socket.default_value
                after = dst_socket.default_value
                if hasattr(before, 'as_pointer'):
                    equal = hasattr(after, 'as_pointer') and before.as_pointer() == after.as_pointer()
                elif isinstance(before, (str, int, float, bool)):
                    equal = before == after
                else:
                    try:
                        old, new = tuple(before), tuple(after)
                    except (TypeError, ValueError):
                        equal = before == after
                    else:
                        equal = len(old) == len(new) and all(
                            abs(float(a) - float(b)) <= 1e-6 for a, b in zip(old, new)
                        )
                if not equal:
                    raise CompileError(
                        f"Bundle cutover lost {direction} default {src_socket.name!r} "
                        f"on {src_node.name!r}"
                    )

        for attr in ("active_index", "define_signature"):
            if not hasattr(src_node, attr) or not hasattr(dst_node, attr):
                continue
            try:
                setattr(dst_node, attr, getattr(src_node, attr))
            except Exception as exc:
                raise CompileError(
                    f"Failed to restore Bundle property {attr!r} on node {src_node.name!r}: {exc}"
                ) from exc


# Native capability boundary. These identifiers describe RNA collection families,
# not wrapper node layouts. Repeat and Bundle retain their existing copy owners.
_LOCAL_COLLECTION_FAMILIES = frozenset({
    "NodeGeometryCaptureAttributeItems", "NodeMenuSwitchItems",
    "NodeIndexSwitchItems", "NodeFunctionFormatStringItems",
})
_DEDICATED_COLLECTION_FAMILIES = frozenset({
    "NodeGeometryRepeatOutputItems", "NodeCombineBundleItems", "NodeSeparateBundleItems",
})


def _local_collection_property(node):
    """Admit one local native item collection or reject unsupported ownership."""
    candidates = []
    for prop in getattr(getattr(node, "bl_rna", None), "properties", ()):
        if prop.type != "COLLECTION" or prop.srna is None:
            continue
        family = prop.srna.identifier
        if family in {"NodeInputs", "NodeOutputs"}:
            continue
        methods = prop.srna.functions
        if (family in _LOCAL_COLLECTION_FAMILIES or family in _DEDICATED_COLLECTION_FAMILIES
                or any(name in methods for name in ("new", "remove", "clear"))):
            candidates.append(prop)
    if not candidates:
        return None
    if len(candidates) != 1:
        raise CompileError(f"{node.bl_idname} copy: ambiguous native collection ownership")
    prop = candidates[0]
    family = prop.srna.identifier
    if family in _DEDICATED_COLLECTION_FAMILIES:
        return None
    if (family not in _LOCAL_COLLECTION_FAMILIES
            or not all(name in prop.srna.functions for name in ("new", "remove", "clear"))):
        raise CompileError(f"{node.bl_idname} copy: unsupported native collection {family}")
    return prop


def _has_local_collection(node):
    """Return whether the native collection has an admitted local owner."""
    return _local_collection_property(node) is not None


def _item_fields(item):
    """Read writable native item scalars, refusing unhandled field shapes."""
    fields = []
    for prop in item.bl_rna.properties:
        if prop.is_readonly:
            continue
        if prop.type not in {"STRING", "ENUM", "INT", "FLOAT", "BOOLEAN"} or getattr(prop, "is_array", False):
            raise CompileError(f"Collection copy: unsupported writable item field {prop.identifier!r}")
        fields.append((prop.identifier, getattr(item, prop.identifier)))
    return tuple(fields)


def _standalone_items(node):
    """Read the ordered logical item state without physical socket IDs."""
    prop = _local_collection_property(node)
    if prop is None:
        raise CompileError(f"{node.bl_idname} copy: missing local collection")
    return tuple(_item_fields(item) for item in getattr(node, prop.identifier))


def _physical_layout(node):
    """Read the complete ordered physical socket identity and flags."""
    result = []
    for output, sockets in ((False, node.inputs), (True, node.outputs)):
        rows = tuple((s.identifier, s.name, s.bl_idname, bool(s.is_output), bool(s.enabled),
                      bool(s.hide), bool(s.is_multi_input)) for s in sockets)
        if len({row[0] for row in rows}) != len(rows) or any(row[3] != output for row in rows):
            raise CompileError(f"{node.bl_idname} copy: invalid native socket identity")
        result.append(rows)
    return tuple(result)


def _standalone_socket_layout(node):
    """Derive item ownership from ordered socket removals in a private tree."""
    layout = _physical_layout(node)
    items = _standalone_items(node)
    prop = _local_collection_property(node)
    probe = node.id_data.copy()
    failure = None
    try:
        mark_transaction_private(probe)
        if not is_authority_ineligible_group(probe) or probe == node.id_data:
            raise CompileError("Collection copy: native probe is not private")
        copied = probe.nodes.get(node.name)
        if copied is None or _physical_layout(copied) != layout or _standalone_items(copied) != items:
            raise CompileError(f"{node.bl_idname} copy: native probe differs from source")
        collection = getattr(copied, prop.identifier)
        before = layout
        owners = [{}, {}]
        for ordinal in range(len(collection) - 1, -1, -1):
            collection.remove(collection[ordinal])
            after = _physical_layout(copied)
            if _standalone_items(copied) != items[:ordinal]:
                raise CompileError(f"{node.bl_idname} copy: removal changed remaining items")
            deltas = []
            for old, new in zip(before, after):
                ids = {row[0] for row in new}
                if tuple(row for row in old if row[0] in ids) != new:
                    raise CompileError(f"{node.bl_idname} copy: removal changed surviving layout")
                deltas.append(tuple(row[0] for row in old if row[0] not in ids))
            if not any(deltas):
                raise CompileError(f"{node.bl_idname} copy: item has no independently observed sockets")
            for owner, removed in zip(owners, deltas):
                if any(ident in owner for ident in removed):
                    raise CompileError(f"{node.bl_idname} copy: overlapping item ownership")
                owner.update((ident, ("item", ordinal, index)) for index, ident in enumerate(removed))
            before = after
        if _physical_layout(node) != layout or _standalone_items(node) != items:
            raise CompileError(f"{node.bl_idname} copy: native probe changed its source")
        return tuple(tuple((owner.get(row[0], row[0]), *row[1:]) for row in rows)
                     for owner, rows in zip(owners, layout))
    except Exception as exc:
        failure = exc
        raise
    finally:
        cleanup_failures = []
        if not _remove_node_group_if_live(probe, failures=cleanup_failures):
            detail = f"Collection copy: native probe cleanup failed: {cleanup_failures!r}"
            if failure is not None:
                raise CompileError(f"{failure}; {detail}") from failure
            raise CompileError(detail)


def _proved_layout(node, layout, proofs):
    """Reuse native slot correspondence only within one exact-copy invocation."""
    if proofs is None:
        proofs = {}
    # Hide may be restored separately; it does not identify a collection item.
    state = (_collection_config(node), _standalone_items(node),
             tuple(tuple(row[:5] + row[6:] for row in rows) for rows in layout))
    entry = proofs.get(node)
    if entry is None or entry[0] != state:
        observed = _standalone_socket_layout(node)
        if len(observed) != len(layout) or any(
                len(rows) != len(actual) or any(row[1:] != value[1:] for row, value in zip(rows, actual))
                for rows, actual in zip(observed, layout)):
            raise CompileError("Collection copy: incomplete or changed ownership proof")
        roles = tuple(tuple(row[0] for row in rows) for rows in observed)
        if any(len(set(keys)) != len(keys) for keys in roles):
            raise CompileError("Collection copy: non-bijective ownership proof")
        owned = set()
        for keys, actual in zip(roles, layout):
            next_slot = {}
            for role, row in zip(keys, actual):
                if isinstance(role, str) and role == row[0]:
                    continue
                if (not isinstance(role, tuple) or len(role) != 3 or role[0] != "item"
                        or type(role[1]) is not int or not 0 <= role[1] < len(state[1])
                        or role[2] != next_slot.get(role[1], 0)):
                    raise CompileError("Collection copy: invalid item ownership proof")
                owned.add(role[1])
                next_slot[role[1]] = role[2] + 1
        if owned != set(range(len(state[1]))):
            raise CompileError("Collection copy: missing item ownership proof")
        proofs[node] = state, roles
    else:
        roles = entry[1]
    return tuple(tuple((role, *row[1:]) for role, row in zip(keys, rows)) for keys, rows in zip(roles, layout))


def _collection_config(node):
    """Read writable node-specific scalar configuration, including active items."""
    generic = {prop.identifier for prop in bpy.types.Node.bl_rna.properties}
    return tuple((prop.identifier, getattr(node, prop.identifier))
                 for prop in node.bl_rna.properties
                 if not prop.is_readonly and prop.type in {"STRING", "ENUM", "INT", "FLOAT", "BOOLEAN"}
                 and not getattr(prop, "is_array", False)
                 and (prop.identifier not in generic or prop.type == "ENUM"))


def _verify_standalone_copy(src, dst, *, ignore_hide=False, proofs=None):
    """Prove equal ordered ownership before using any corresponding sockets."""
    if (src.bl_idname != dst.bl_idname
            or _collection_config(src) != _collection_config(dst)
            or _standalone_items(src) != _standalone_items(dst)):
        raise CompileError(f"{src.bl_idname} copy: restored collection contract differs on {src.name!r}")
    a, b = _physical_layout(src), _physical_layout(dst)
    if tuple(tuple(row[0] for row in rows) for rows in a) != tuple(tuple(row[0] for row in rows) for rows in b):
        a, b = _proved_layout(src, a, proofs), _proved_layout(dst, b, proofs)
    if ignore_hide:
        def without_hide(layout):
            return tuple(tuple(row[:5] + row[6:] for row in rows) for rows in layout)
        a, b = without_hide(a), without_hide(b)
    if a != b:
        raise CompileError(f"{src.bl_idname} copy: restored socket layout differs on {src.name!r}")


def _standalone_default(value):
    """Detach RNA array defaults for exact readback, retaining scalar and ID values."""
    if value is None or isinstance(value, (str, int, float, bool, bpy.types.ID)):
        return value
    return tuple(value)


def _verify_standalone_defaults(src, dst):
    """Check exact RNA defaults after copying and after link construction."""
    for source_sockets, target_sockets in ((src.inputs, dst.inputs), (src.outputs, dst.outputs)):
        for source, target in zip(source_sockets, target_sockets):
            if (hasattr(source, "default_value")
                    and _standalone_default(source.default_value) != _standalone_default(target.default_value)):
                raise CompileError(f"{src.bl_idname} copy: default readback differs at {source.identifier!r}")


class _CollectionDefaultError(CompileError):
    """A native default assignment/readback failed after verified reconstruction."""


def _reconstruct_collection(src, dst, *, retain_prefix, proofs):
    """Restore logical item fields and configuration, invalidating obsolete proof."""
    items = _standalone_items(src)
    config = _collection_config(src)
    existing = _standalone_items(dst)
    prop = _local_collection_property(src)
    destination = getattr(dst, prop.identifier)
    params = tuple(p for p in prop.srna.functions["new"].parameters if not p.is_output)
    prefix = 0
    if retain_prefix:
        for old, new in zip(existing, items):
            if old != new:
                break
            prefix += 1
    if proofs is not None:
        proofs.pop(dst, None)
    while len(destination) > prefix:
        destination.remove(destination[-1])
    for fields in items[prefix:]:
        values = dict(fields)
        args = {}
        for param in params:
            if param.identifier in values:
                args[param.identifier] = values[param.identifier]
            elif param.type == "ENUM" and len(param.enum_items):
                # Constructor initialization only; actual item fields are restored below.
                args[param.identifier] = param.enum_items[0].identifier
            else:
                raise CompileError(f"{src.bl_idname} copy: unsupported constructor argument {param.identifier!r}")
        item = destination.new(**args)
        for name, value in fields:
            if getattr(item, name) != value:
                setattr(item, name, value)
        if _item_fields(item) != fields:
            raise CompileError(f"{src.bl_idname} copy: item readback differs")
    for name, value in config:
        if getattr(dst, name) != value:
            setattr(dst, name, value)
    return prefix


def _restore_collection_defaults(src, dst, *, proofs):
    """Restore defaults/flags after proof; distinguish native value failure from proof failure."""
    _verify_standalone_copy(src, dst, ignore_hide=True, proofs=proofs)
    for source_sockets, target_sockets in ((src.inputs, dst.inputs), (src.outputs, dst.outputs)):
        for source, target in zip(source_sockets, target_sockets):
            if source.hide != target.hide:
                target.hide = source.hide
    _verify_standalone_copy(src, dst, proofs=proofs)
    for source_sockets, target_sockets in ((src.inputs, dst.inputs), (src.outputs, dst.outputs)):
        for source, target in zip(source_sockets, target_sockets):
            if not hasattr(source, "default_value"):
                continue
            expected = _standalone_default(source.default_value)
            if _standalone_default(target.default_value) == expected:
                continue
            try:
                target.default_value = expected
            except (TypeError, ValueError) as exc:
                raise _CollectionDefaultError(f"{src.bl_idname} copy: cannot assign {source.identifier!r}: {exc}") from exc
            if _standalone_default(target.default_value) != expected:
                raise _CollectionDefaultError(f"{src.bl_idname} copy: default readback differs at {source.identifier!r}")
    _verify_standalone_defaults(src, dst)


def _copy_standalone_collection(src, dst, *, proofs=None):
    """Reconstruct a new node, allowing one full rebuild for native default state."""
    if proofs is None:
        proofs = {}
    try:
        prefix = _reconstruct_collection(src, dst, retain_prefix=True, proofs=proofs)
        try:
            _restore_collection_defaults(src, dst, proofs=proofs)
        except _CollectionDefaultError as first:
            if not prefix:
                raise
            # Some native state (e.g. unset enum selection) depends on how the
            # collection was constructed. Use the same constructor, not a weaker copier.
            try:
                _reconstruct_collection(src, dst, retain_prefix=False, proofs=proofs)
                _restore_collection_defaults(src, dst, proofs=proofs)
            except Exception as final:
                raise CompileError(f"{first}; rebuild failed: {final}") from final
    except Exception as exc:
        raise CompileError(f"{src.bl_idname} copy: failed to restore {src.name!r}: {exc}") from exc


def _resolve_standalone_copy_socket(src, socket, dst, *, output, proofs=None):
    """Resolve a collection endpoint only after checked exact-copy correspondence."""
    _verify_standalone_copy(src, dst, proofs=proofs)
    source_sockets = src.outputs if output else src.inputs
    target_sockets = dst.outputs if output else dst.inputs
    return target_sockets[list(source_sockets).index(socket)]


def _validate_collection_copy_source(tree):
    """Admit a complete source and validate raw contracts before destination edits."""
    from . import raw_nodes
    admitted = set()
    for node in tree.nodes:
        if _has_local_collection(node):
            admitted.add(node)
            if raw_nodes.is_raw_node(node):
                raw_nodes.validate_raw_node_after_cutover(node, tree)
    if admitted and any(not link.is_valid for link in tree.links
                        if link.from_node in admitted or link.to_node in admitted):
        raise CompileError("Collection copy: invalid source link")
    return bool(admitted)


def _verify_collection_edges(tree, copied_edges, nodes):
    """Verify final collection topology and restore native multi-input order.

    Records include ordinary edges too: a multi-input receiving a collection
    output may also have ordinary producers, whose relative order matters.
    """
    node_set = set(nodes)
    if not node_set:
        return
    expected = [(left, right) for _, left, right in copied_edges
                if left.node in node_set or right.node in node_set]
    actual = [link for link in tree.links
              if link.from_node in node_set or link.to_node in node_set]
    if Counter((link.from_socket, link.to_socket) for link in actual) != Counter(expected):
        raise CompileError("Collection copy: final linked endpoints differ")
    if any(not link.is_valid for link in actual):
        raise CompileError("Collection copy: invalid final link")
    for socket in {right for _, right in expected if right.is_multi_input}:
        desired = [left for _, left, right in sorted(copied_edges, key=lambda row: row[0].multi_input_sort_id)
                   if right == socket]
        ordered = sorted(socket.links, key=lambda link: link.multi_input_sort_id)
        if Counter(link.from_socket for link in ordered) != Counter(desired):
            raise CompileError("Collection copy: multi-input topology differs")
        # Blender insertion order need not equal the source's native sort order
        # (for example after a manual reorder or a partial rollback).
        for index, left in enumerate(desired):
            if ordered[index].from_socket != left:
                match = next(i for i in range(index + 1, len(ordered)) if ordered[i].from_socket == left)
                ordered[index].swap_multi_input_sort_id(ordered[match])
                ordered[index], ordered[match] = ordered[match], ordered[index]
        if [link.from_socket for link in sorted(socket.links, key=lambda link: link.multi_input_sort_id)] != desired:
            raise CompileError("Collection copy: multi-input order differs")


def _copy_link_endpoint(src, socket, dst, *, output, proofs=None):
    """Preserve the raw/collection/ordinary copy policy of one link endpoint."""
    from . import raw_nodes
    if _has_local_collection(src):
        return _resolve_standalone_copy_socket(src, socket, dst, output=output, proofs=proofs)
    if raw_nodes.is_raw_node(src):
        return raw_nodes.resolve_cutover_socket(src, socket, dst, direction="output" if output else "input")
    source_sockets = src.outputs if output else src.inputs
    target_sockets = dst.outputs if output else dst.inputs
    try:
        return target_sockets[list(source_sockets).index(socket)]
    except (ValueError, IndexError):
        return target_sockets[socket.name]


def _sync_repeat_zone_dynamic_items(src_group, node_map):
    """Recreate Repeat Zone pairings/items after node copy and before links.

    Blender Repeat Zone state sockets are not ordinary writable node
    properties. A freshly created GeometryNodeRepeatInput/Output pair only has
    system/default sockets, so links to copied state sockets such as
    ``instance_points`` fail unless the output node's repeat_items collection is
    rebuilt before link restoration.
    """
    for src_input in src_group.nodes:
        if getattr(src_input, "bl_idname", None) != "GeometryNodeRepeatInput":
            continue
        src_output = getattr(src_input, "paired_output", None)
        if src_output is None:
            continue
        dst_input = node_map.get(src_input.name)
        dst_output = node_map.get(src_output.name)
        if dst_input is None or dst_output is None:
            continue
        try:
            dst_input.pair_with_output(dst_output)
        except Exception:
            pass
    for src_output in src_group.nodes:
        if getattr(src_output, "bl_idname", None) != "GeometryNodeRepeatOutput":
            continue
        dst_output = node_map.get(src_output.name)
        if dst_output is None or not hasattr(src_output, "repeat_items") or not hasattr(dst_output, "repeat_items"):
            continue
        try:
            for item in list(dst_output.repeat_items):
                dst_output.repeat_items.remove(item)
        except Exception:
            pass
        for item in list(src_output.repeat_items):
            try:
                dst_output.repeat_items.new(item.socket_type, item.name)
            except Exception:
                # Keep cutover failure transactional; link restoration below will
                # raise if the required socket was not recreated.
                pass
        try:
            dst_output.active_index = getattr(src_output, "active_index", dst_output.active_index)
        except Exception:
            pass
        try:
            dst_output.inspection_index = getattr(src_output, "inspection_index", dst_output.inspection_index)
        except Exception:
            pass

def _remove_node_group_if_live(group, *, failures=None):
    """Remove the exact live group; optionally aggregate physical cleanup failure."""
    if group is None or not _node_group_is_live(group):
        return True
    try:
        bpy.data.node_groups.remove(group, do_unlink=True)
        return True
    except Exception as exc:
        if failures is not None:
            failures.append(exc)
        return False


def _node_group_is_live(group):
    """Return True when *group* is still present in ``bpy.data.node_groups``."""
    if group is None:
        return False
    try:
        pointer = int(group.as_pointer())
    except Exception:
        pointer = id(group)
    for candidate in bpy.data.node_groups:
        try:
            if int(candidate.as_pointer()) == pointer:
                return True
        except Exception:
            if candidate is group:
                return True
    return False


def _transaction_owns_group(transaction, group) -> bool:
    """Return True for temporary groups owned by the active transaction."""
    return bool(transaction is not None and hasattr(transaction, "owns_group") and transaction.owns_group(group))


def _validate_root_owner_id_uniqueness(root_id, existing_group=None, transaction=None):
    """Reject duplicate live root-owner identities without allocating a replacement."""
    root_id = validate_root_owner_id(root_id)
    duplicates = []
    for group in bpy.data.node_groups:
        if existing_group is not None and group is existing_group:
            continue
        if _transaction_owns_group(transaction, group):
            continue
        if is_authority_ineligible_group(group):
            continue
        try:
            candidate = group.get(FUNCTION_ROOT_OWNER_ID_PROP)
        except Exception:
            candidate = None
        if candidate and validate_root_owner_id(candidate) == root_id:
            duplicates.append(getattr(group, "name", "<unnamed>"))
    if duplicates:
        raise CompileError(f"Multiple root node groups share NodeForge owner ID {root_id}")
    return root_id


def _root_owner_id_for_build(existing_group, transaction=None):
    """Return the persistent or candidate root owner ID using the established rules."""
    if existing_group is not None:
        try:
            existing = existing_group.get(FUNCTION_ROOT_OWNER_ID_PROP)
        except Exception:
            existing = None
        root_id = validate_root_owner_id(existing) if existing else new_root_owner_id()
    else:
        root_id = new_root_owner_id()
    return _validate_root_owner_id_uniqueness(root_id, existing_group, transaction)


def resolve_root_group_compilation_identity(existing_group=None, transaction=None):
    """Resolve one final durable root identity before semantic group preparation."""
    root_id = _root_owner_id_for_build(existing_group, transaction)
    owner_scope = make_function_group_owner_scope("ROOT", root_id)
    return GroupCompilationIdentity(root_id, owner_scope, owner_scope, owner_scope)



class BlenderGroupBuildTransaction:
    """Own nested GeometryNodeTree mutations for one outer build attempt."""

    def __init__(self):
        self.created_groups = []
        self.updated_groups = []
        self._updated_by_identity = {}
        self._mutation_journal = []
        self._cache_owners = []
        self._closed = False
        self.retirement_failures = []
        # Exact modifier/runtime side effects must share the group PONR.
        self._external_rollback = []
        self._binding_commit = []

    def register_external_cutover(self, *, rollback, commit):
        """Journal a prepared runtime installation in the existing group transaction.

        `commit` must only change prevalidated process-local references and cannot
        perform Blender operations. `rollback` restores the modifier and live
        resources before candidate node groups are destroyed.
        """
        if self._closed:
            raise RuntimeError("Cannot register runtime cutover after group commit")
        self._external_rollback.append(rollback)
        self._binding_commit.append(commit)

    @staticmethod
    def _group_key(group):
        """Return process-local physical identity for one Blender group."""
        try:
            return int(group.as_pointer())
        except Exception:
            return id(group)

    def owns_group(self, group) -> bool:
        """Return True when *group* is physical state owned by this attempt."""
        if group is None:
            return False
        key = self._group_key(group)
        if any(item[0] is not None and self._group_key(item[0]) == key for item in self.created_groups):
            return True
        return any(
            event.get("backup") is not None and self._group_key(event["backup"]) == key
            for event in self._mutation_journal
        )

    def register_cache(self, cache):
        """Track a build-local materialization cache for exact savepoint rollback."""
        if cache is not None and cache not in self._cache_owners:
            self._cache_owners.append(cache)

    def register_created(self, group, resource_transaction):
        """Track one fresh provisional canonical group and its generated resources."""
        if not self._closed:
            self.created_groups.append((group, resource_transaction))

    def register_updated(
        self,
        group,
        backup,
        external_state,
        resource_transaction,
        old_manifest,
        new_manifest,
        *,
        original_name=None,
        captured_resources=(),
    ):
        """Journal one mutation of an existing authoritative group."""
        if self._closed:
            _remove_node_group_if_live(backup)
            return
        key = self._group_key(group)
        identity_record = self._updated_by_identity.get(key)
        if identity_record is None:
            identity_record = {"group": group, "mutations": []}
            self._updated_by_identity[key] = identity_record
            self.updated_groups.append(identity_record)
        event = {
            "group": group,
            "backup": backup,
            "external_state": external_state,
            "resource_transaction": resource_transaction,
            "old_manifest": old_manifest,
            "new_manifest": new_manifest,
            "captured_resources": captured_resources,
            "original_name": getattr(group, "name", None) if original_name is None else original_name,
        }
        identity_record["mutations"].append(event)
        self._mutation_journal.append(event)
        return event

    def discard_abandoned_tree(self, tree):
        """Retire obligations for an exact, intentionally discarded build stage."""
        if not _node_group_is_live(tree):
            raise CompileError("Cannot retire rollback records for an unexpectedly absent stage")
        if self.owns_group(tree) or not is_authority_ineligible_group(tree):
            raise CompileError("Cannot discard authoritative or recovery state as a stage")
        # Select while RNA is live. Never dereference the tree after removal.
        selected = []
        for event in self._mutation_journal:
            for records in event["external_state"].values():
                kept = [record for record in records if record["tree"] != tree]
                if len(kept) != len(records):
                    selected.append((records, kept))
        failures = []
        try:
            removed = _remove_node_group_if_live(tree, failures=failures)
        finally:
            if not _node_group_is_live(tree):
                for records, kept in selected:
                    records[:] = kept
        if not removed or _node_group_is_live(tree):
            detail = failures[0] if failures else "datablock remains live"
            raise CompileError(f"Abandoned stage disposal failed: {detail}")

    def savepoint(self):
        """Return a checkpoint for exact journal/cache restoration."""
        cache_snapshots = [
            (cache, dict(cache))
            for cache in self._cache_owners
            if hasattr(cache, "keys") and hasattr(cache, "clear") and hasattr(cache, "update")
        ]
        return {
            "created": len(self.created_groups),
            "mutations": len(self._mutation_journal),
            "external": len(self._external_rollback),
            "cache_snapshots": cache_snapshots,
        }

    @staticmethod
    def _rollback_resource_transaction(tx, failures):
        """Rollback one generated-resource transaction and collect failures."""
        if tx is None:
            return
        try:
            tx.rollback()
        except Exception as exc:
            failures.append(exc)

    def _restore_mutation(self, event, failures):
        """Restore one existing-group mutation from its immediate backup."""
        group = event["group"]
        backup = event["backup"]
        try:
            if group is not None and backup is not None and _node_group_is_live(group):
                _copy_group_contents(backup, group)
                original_name = event.get("original_name")
                if original_name is not None:
                    group.name = original_name
                _restore_group_external_state(group, event["external_state"], strict=True)
        except Exception as exc:
            failures.append(exc)
        self._rollback_resource_transaction(event.get("resource_transaction"), failures)
        _remove_node_group_if_live(backup, failures=failures)

    def _drop_mutation_from_identity(self, event):
        """Remove a rolled-back event and retire an empty identity record."""
        group = event.get("group")
        key = self._group_key(group)
        record = self._updated_by_identity.get(key)
        if record is None:
            return
        mutations = record.get("mutations", [])
        if event in mutations:
            mutations.remove(event)
        if mutations:
            return
        self._updated_by_identity.pop(key, None)
        try:
            self.updated_groups.remove(record)
        except ValueError:
            pass

    def rollback_to_savepoint(self, savepoint):
        """Undo work after *savepoint* while preserving earlier accepted mutations."""
        if self._closed:
            return
        failures = []
        external_cursor = int(savepoint["external"])
        for action in reversed(self._external_rollback[external_cursor:]):
            try:
                action()
            except Exception as exc:
                failures.append(exc)
        del self._external_rollback[external_cursor:]
        del self._binding_commit[external_cursor:]
        cursor = int(savepoint["mutations"])
        for event in reversed(self._mutation_journal[cursor:]):
            self._restore_mutation(event, failures)
            self._drop_mutation_from_identity(event)
        del self._mutation_journal[cursor:]

        created_cursor = int(savepoint["created"])
        for group, tx in reversed(self.created_groups[created_cursor:]):
            self._rollback_resource_transaction(tx, failures)
            if _remove_node_group_if_live(group, failures=failures):
                forget_provisional(group)
        del self.created_groups[created_cursor:]

        for cache, snapshot in savepoint.get("cache_snapshots", []):
            try:
                cache.clear()
                cache.update(snapshot)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise RuntimeError(f"Function-group savepoint rollback failed in {len(failures)} operation(s)") from failures[0]

    def rollback(self):
        """Restore all groups/resources to the outer-attempt state."""
        if self._closed:
            return
        failures = []
        for action in reversed(self._external_rollback):
            try:
                action()
            except Exception as exc:
                failures.append(exc)
        self._external_rollback.clear()
        self._binding_commit.clear()
        for event in reversed(self._mutation_journal):
            self._restore_mutation(event, failures)
        for group, tx in reversed(self.created_groups):
            self._rollback_resource_transaction(tx, failures)
            if _remove_node_group_if_live(group, failures=failures):
                forget_provisional(group)
        self._mutation_journal.clear()
        self.updated_groups.clear()
        self._updated_by_identity.clear()
        self.created_groups.clear()
        self._closed = True
        if failures:
            raise RuntimeError(f"Function-group rollback failed in {len(failures)} operation(s)") from failures[0]

    def commit(self):
        """Commit ownership at one PONR, then retire old state best-effort."""
        if self._closed:
            return

        # GeneratedResourceTransaction.mark_committed() is intentionally an
        # infallible in-memory flag flip. No Blender deletion occurs here.
        resource_transactions = []
        seen = set()
        for event in self._mutation_journal:
            tx = event.get("resource_transaction")
            if tx is not None and id(tx) not in seen:
                seen.add(id(tx))
                resource_transactions.append(tx)
        for _group, tx in self.created_groups:
            if tx is not None and id(tx) not in seen:
                seen.add(id(tx))
                resource_transactions.append(tx)

        # POINT OF NO RETURN: all operations below until _closed are infallible
        # Python bookkeeping. Retirement begins only after authoritative state.
        for tx in resource_transactions:
            tx.mark_committed()
        publish_provisional([group for group, _tx in self.created_groups])
        # Prepared callbacks change only in-memory bindings. They cannot fail or
        # access bpy; old resources are retired only after the new binding is live.
        for action in self._binding_commit:
            action()
        self._closed = True
        self._external_rollback.clear()
        self._binding_commit.clear()

        failures = []
        for event in self._mutation_journal:
            old_manifest = event.get("old_manifest")
            new_manifest = event.get("new_manifest")
            if old_manifest is not None:
                # Remove the transaction-private rollback group before
                # retirement so its old ObjectInfo pointers are not counted as
                # live external users of the exact old generated objects.
                _remove_node_group_if_live(event.get("backup"), failures=failures)
                try:
                    captured = event.get("captured_resources", ())
                    kwargs = {"captured_ids": captured} if captured else {}
                    generated_resources.cleanup_previous_after_commit(
                        old_manifest,
                        new_manifest or generated_resources.build_manifest(old_manifest["owner_group_uuid"], []),
                        **kwargs,
                    )
                except Exception as exc:
                    failures.append(exc)
            else:
                _remove_node_group_if_live(event.get("backup"), failures=failures)
        # Retire only exact session-created extension groups that lost every
        # physical user during this root/source-group publication. Their
        # generated resources use the existing manifest provenance checks.
        try:
            from .extension_group_materializer import retire_unused_extension_groups
            retire_unused_extension_groups()
        except Exception as exc:
            failures.append(exc)
        self.retirement_failures.extend(failures)
        for exc in failures:
            warnings.warn(f"NodeForge committed with cleanup warning: {exc}", RuntimeWarning, stacklevel=2)

        self._mutation_journal.clear()
        self.updated_groups.clear()
        self._updated_by_identity.clear()
        self.created_groups.clear()


class BlenderGroupBackend:
    """Own physical GeometryNodeTree publication for prepared semantic compilations."""

    def __init__(self, *, populate_candidate: Callable[..., object], prepare_compilation: Callable[..., object] | None = None):
        """Bind physical population and optional public raw-source orchestration."""
        self._populate_candidate = populate_candidate
        self._prepare_compilation = prepare_compilation

    def resolve_root_compilation_identity(self, existing_group=None, transaction=None):
        """Resolve the final root identity before semantic preparation."""
        return resolve_root_group_compilation_identity(existing_group, transaction)

    def prepare_source_compilation(self, source: str, *, compilation_identity, **kwargs):
        """Invoke the compiler-owned pure preparation callback for one source snapshot."""
        if self._prepare_compilation is None:
            raise CompileError("Internal error: Blender group backend has no semantic preparation callback")
        return self._prepare_compilation(source, compilation_identity=compilation_identity, **kwargs)

    def compile_group_callback(self, source: str, name: str = "NodeForge Group", **kwargs):
        """Preserve the public raw-source API as prepare-then-publish orchestration."""
        existing_group = kwargs.pop("existing_group", None)
        preserve_if_equivalent = bool(kwargs.pop("preserve_if_equivalent", False))
        local_functions = kwargs.pop("local_functions", None)
        imported_library_functions = kwargs.pop("imported_library_functions", None)
        helper_namespace = kwargs.pop("helper_namespace", None) or name
        source_callable_session = kwargs.pop("source_callable_session", None)
        function_group_cache = kwargs.pop("function_group_cache", None)
        function_tx = kwargs.pop("function_group_transaction", None)
        local_tx = kwargs.pop("local_helper_transaction", None)
        function_tx = function_tx or local_tx
        function_compilation_trace = kwargs.pop("function_compilation_trace", None)
        function_compilation_inputs = kwargs.pop("function_compilation_inputs", None)
        function_instance_key = kwargs.pop("function_instance_key", None)
        if kwargs:
            unknown = ", ".join(sorted(kwargs))
            raise TypeError(f"Unsupported Blender group build option(s): {unknown}")
        identity = self.resolve_root_compilation_identity(existing_group, function_tx)
        prepared = self.prepare_source_compilation(
            source,
            compilation_identity=identity,
            helper_namespace=helper_namespace,
            inherited_local_functions=local_functions,
            inherited_imported_library_functions=imported_library_functions,
            source_callable_session=source_callable_session,
        )
        request = BlenderGroupBuildRequest(
            prepared_compilation=prepared,
            name=name,
            existing_group=existing_group,
            helper_namespace=helper_namespace,
            function_group_cache=function_group_cache,
            function_group_transaction=function_tx,
            function_compilation_trace=function_compilation_trace,
            function_compilation_inputs=function_compilation_inputs,
            function_instance_key=function_instance_key,
            source_callable_session=source_callable_session,
            preserve_if_equivalent=preserve_if_equivalent,
        )
        return self.create_or_update(request)

    def create_or_update(
        self,
        request: BlenderGroupBuildRequest,
        *,
        finalize_before_commit: Callable[[object], None] | None = None,
    ):
        """Publish one already-prepared semantic compilation atomically."""
        if not isinstance(request, BlenderGroupBuildRequest):
            raise TypeError("request must be BlenderGroupBuildRequest")
        identity = request.prepared_compilation.identity
        runtime_owners = [owner for owner, _fingerprint in request.prepared_compilation.extension_dependencies
                          if owner and owner[0] in {"local-runtime", "examples-runtime"}]
        if runtime_owners and request.mesh_compute_modifier_target is None:
            raise CompileError(
                "Mesh Compute requires a root modifier; nested GeometryNodeGroup publication is unsupported"
            )
        if request.mesh_compute_modifier_target is not None and not runtime_owners:
            raise CompileError("Mesh Compute modifier requires one imported Mesh Compute stage")
        outermost = request.function_group_transaction is None
        build_tx = request.function_group_transaction or BlenderGroupBuildTransaction()
        if identity.root_owner_id is not None:
            # Fresh Mesh Compute Reload is the sole case with one still-live
            # authoritative root bearing this owner ID. Its exact replacement
            # is validated against the target modifier before this build.
            replaced = request.mesh_compute_replaced_group
            if replaced is not None:
                if request.existing_group is not None or request.mesh_compute_modifier_target is None:
                    raise CompileError("Mesh Compute fresh replacement requires a root modifier target")
                owner = request.mesh_compute_modifier_target
                if (not owner.modifiers or owner.modifiers[0].type != "NODES" or
                        owner.modifiers[0].node_group is not replaced or
                        getattr(replaced, "get", lambda key: None)(FUNCTION_ROOT_OWNER_ID_PROP) != identity.root_owner_id):
                    raise CompileError("Mesh Compute replacement no longer owns the original root")
            _validate_root_owner_id_uniqueness(identity.root_owner_id,
                                               request.existing_group or replaced, build_tx)
        active_cache = request.function_group_cache
        if active_cache is None:
            active_cache = {}
        build_tx.register_cache(active_cache)
        active_trace = request.function_compilation_trace or FunctionCompilationTrace()
        request = replace(
            request,
            function_group_cache=active_cache,
            function_group_transaction=build_tx,
            function_compilation_trace=active_trace,
        )

        return self.publish_candidate(
            name=request.name,
            existing_group=request.existing_group,
            build_tx=build_tx,
            populate=lambda candidate, resources: self._populate(candidate, request, resources),
            preserve_if_equivalent=request.preserve_if_equivalent,
            outermost=outermost,
            finalize_before_commit=finalize_before_commit,
        )

    def publish_candidate(
        self, *, name: str, existing_group, build_tx: BlenderGroupBuildTransaction,
        populate: Callable[[object, object], None], preserve_if_equivalent: bool = False,
        outermost: bool = False, finalize_before_commit: Callable[[object], None] | None = None,
    ):
        """Publish a populated source or extension group through one shared lifecycle.

        The caller resolves unique authority and supplies its metadata builder;
        only this method creates private candidates and cutover/rollback state.
        Nested extension builders pass the active transaction with outermost=False.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("Candidate group name must be a non-empty string")
        if not isinstance(build_tx, BlenderGroupBuildTransaction) or build_tx._closed:
            raise ValueError("Candidate publication requires one open group transaction")
        if not callable(populate):
            raise TypeError("Candidate publication requires a populate callback")
        if existing_group is None:
            return self._create_fresh(
                name, build_tx, populate,
                outermost=outermost,
                finalize_before_commit=finalize_before_commit,
            )
        if getattr(existing_group, "bl_idname", None) != "GeometryNodeTree":
            raise CompileError("Selected node group is not a GeometryNodeTree")
        return self._update_existing(
            name, existing_group, build_tx, populate,
            outermost=outermost,
            preserve_if_equivalent=preserve_if_equivalent,
            finalize_before_commit=finalize_before_commit,
        )

    def _populate(self, group, request, resource_tx):
        """Populate an owned candidate from prepared semantics only."""
        return self._populate_candidate(
            group,
            request=request,
            generated_resource_transaction=resource_tx,
            group_backend=self,
        )

    def _create_fresh(self, name, build_tx, populate, *, outermost, finalize_before_commit):
        """Create, populate, and publish one fresh canonical group."""
        tx = generated_resources.GeneratedResourceTransaction(owner_group_uuid=uuid.uuid4().hex)
        savepoint = build_tx.savepoint()
        group = bpy.data.node_groups.new(name, "GeometryNodeTree")
        mark_provisional(group)
        build_tx.register_created(group, tx)
        try:
            populate(group, tx)
            if tx.resources:
                generated_resources.write_group_manifest(group, tx.manifest())
            if finalize_before_commit is not None:
                finalize_before_commit(group)
            if outermost:
                build_tx.commit()
            return group
        except Exception as exc:
            try:
                if outermost:
                    build_tx.rollback()
                else:
                    build_tx.rollback_to_savepoint(savepoint)
            except Exception as rollback_exc:
                try:
                    exc.add_note(f"NodeForge transaction cleanup also failed: {rollback_exc}")
                except Exception:
                    pass
                raise exc from rollback_exc
            raise

    def _new_private_backup(self, existing_group, name):
        """Create a private rollback backup with exception-safe physical ownership."""
        backup = bpy.data.node_groups.new("NodeForge.rollback." + name, "GeometryNodeTree")
        mark_transaction_private(backup)
        try:
            _copy_group_contents(existing_group, backup, copy_role="backup")
        except Exception as copy_exc:
            cleanup_failures = []
            removed = _remove_node_group_if_live(backup, failures=cleanup_failures)
            if not removed and cleanup_failures:
                try:
                    copy_exc.add_note(
                        "NodeForge rollback-backup cleanup also failed; "
                        "the remaining datablock is transaction-private: "
                        f"{cleanup_failures[0]}"
                    )
                except Exception:
                    pass
                raise copy_exc from cleanup_failures[0]
            raise
        return backup

    def _rehearse_collection_cutover(self, *sources):
        """Verify each required reconstruction before resetting the live group."""
        sources = [source for source in sources if _validate_collection_copy_source(source)]
        if not sources:
            return
        scratch = bpy.data.node_groups.new("NodeForge.copy-rehearsal", "GeometryNodeTree")
        try:
            mark_transaction_private(scratch)
            for source in sources:
                _copy_group_contents(source, scratch)
        except Exception as error:
            failures = []
            _remove_node_group_if_live(scratch, failures=failures)
            for failure in failures:
                error.add_note(f"Collection rehearsal cleanup also failed: {failure}")
            raise
        else:
            failures = []
            if not _remove_node_group_if_live(scratch, failures=failures):
                raise CompileError("Collection rehearsal cleanup failed") from failures[0]

    def _update_existing(
        self,
        name, existing_group, build_tx, populate,
        *,
        outermost,
        preserve_if_equivalent,
        finalize_before_commit,
    ):
        """Build a private replacement, then cut over the authoritative datablock."""
        admission_error = None
        try:
            _validate_collection_copy_source(existing_group)
        except CompileError as exc:
            if not preserve_if_equivalent or finalize_before_commit is not None:
                raise
            # Equivalence is established only after population. A genuinely
            # no-copy path remains legal; a later copy must reject the old state.
            admission_error = exc
        old_manifest = generated_resources.read_group_manifest(existing_group)
        captured_resources = generated_resources.capture_owned_resource_ids(existing_group, old_manifest)
        owner_uuid = old_manifest["owner_group_uuid"] if old_manifest is not None else uuid.uuid4().hex
        tx = generated_resources.GeneratedResourceTransaction(owner_group_uuid=owner_uuid)
        tx_registered = False
        savepoint = build_tx.savepoint()
        replacement = bpy.data.node_groups.new("NodeForge.replacement." + name, "GeometryNodeTree")
        mark_transaction_private(replacement)
        backup = None
        primary_error = None
        try:
            populate(replacement, tx)
            new_manifest = tx.manifest(empty=not bool(tx.resources)) if (old_manifest is not None or tx.resources) else None
            if new_manifest is not None:
                generated_resources.write_group_manifest(replacement, new_manifest)

            if preserve_if_equivalent:
                old_fp = stored_fingerprint(existing_group)
                new_fp = stored_fingerprint(replacement)
                old_contract = stored_interface_contract(existing_group)
                live_contract = interface_contract(existing_group)
                new_contract = interface_contract(replacement)
                if (old_fp and new_fp and old_fp == new_fp and old_contract
                        and old_contract == live_contract == new_contract
                        and has_named_results(existing_group) == has_named_results(replacement)):
                    tx.rollback()
                    build_tx.rollback_to_savepoint(savepoint)
                    if finalize_before_commit is not None:
                        backup = self._new_private_backup(existing_group, name)
                        self._rehearse_collection_cutover(backup)
                        external_state = _capture_group_external_state(existing_group)
                        build_tx.register_updated(
                            existing_group,
                            backup,
                            external_state,
                            None,
                            None,
                            None,
                            original_name=getattr(existing_group, "name", None),
                        )
                        backup = None
                        try:
                            finalize_before_commit(existing_group)
                        except Exception:
                            build_tx.rollback_to_savepoint(savepoint)
                            raise
                    build_tx.discard_abandoned_tree(replacement)
                    replacement = None
                    if outermost:
                        build_tx.commit()
                    return existing_group

            if admission_error is not None:
                raise admission_error
            backup = self._new_private_backup(existing_group, name)
            self._rehearse_collection_cutover(backup, replacement)
            external_state = _capture_group_external_state(existing_group)
            _validate_group_external_state_for_replacement(replacement, external_state)
            original_name = getattr(existing_group, "name", None)
            try:
                _copy_group_contents(replacement, existing_group, copy_role="cutover")
                if new_manifest is not None:
                    generated_resources.write_group_manifest(existing_group, new_manifest)
                else:
                    generated_resources.clear_group_manifest(existing_group)
                _restore_group_external_state(existing_group, external_state, strict=False)
            except Exception as cutover_exc:
                rollback_exc = None
                try:
                    _copy_group_contents(backup, existing_group)
                    existing_group.name = original_name
                    _restore_group_external_state(existing_group, external_state, strict=True)
                except Exception as exc:
                    rollback_exc = exc
                if rollback_exc is not None:
                    try:
                        cutover_exc.add_note(f"NodeForge rollback restoration also failed: {rollback_exc}")
                    except Exception:
                        pass
                    raise cutover_exc from rollback_exc
                raise

            build_tx.register_updated(
                existing_group,
                backup,
                external_state,
                tx,
                old_manifest,
                new_manifest,
                original_name=original_name,
                captured_resources=captured_resources,
            )
            tx_registered = True
            backup = None
            if finalize_before_commit is not None:
                finalize_before_commit(existing_group)
            # Child updates during population may have captured this replacement.
            # It is abandoned once the authoritative copy has been installed.
            build_tx.discard_abandoned_tree(replacement)
            replacement = None
            if outermost:
                build_tx.commit()
            return existing_group
        except Exception as update_exc:
            primary_error = update_exc
            rollback_failures = []
            try:
                if outermost:
                    build_tx.rollback()
                else:
                    build_tx.rollback_to_savepoint(savepoint)
            except Exception as exc:
                rollback_failures.append(exc)
            if not tx_registered:
                try:
                    tx.rollback()
                except Exception as exc:
                    rollback_failures.append(exc)
            if rollback_failures:
                try:
                    update_exc.add_note(
                        f"NodeForge transaction cleanup also failed: {rollback_failures[0]}"
                    )
                except Exception:
                    pass
                raise update_exc from rollback_failures[0]
            raise
        finally:
            cleanup_failures = []
            _remove_node_group_if_live(replacement, failures=cleanup_failures)
            _remove_node_group_if_live(backup, failures=cleanup_failures)
            if cleanup_failures:
                if primary_error is None:
                    raise CompileError("NodeForge private group cleanup failed") from cleanup_failures[0]
                for failure in cleanup_failures:
                    primary_error.add_note(f"NodeForge private group cleanup also failed: {failure}")


__all__ = [
    "BlenderGroupBackend",
    "BlenderGroupBuildRequest",
    "BlenderGroupBuildTransaction",
    "_copy_group_contents",
    "_node_group_is_live",
    "_root_owner_id_for_build",
    "resolve_root_group_compilation_identity",
]
