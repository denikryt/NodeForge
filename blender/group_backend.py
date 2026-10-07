"""Blender GeometryNodeTree publication and rollback backend.

The compiler supplies population of an already-created fresh group. This module
owns physical datablock creation, replacement/cutover, nested transaction
journaling, and generated-resource commit/rollback coordination.
"""

from __future__ import annotations

import uuid
import warnings
from dataclasses import replace
from typing import Callable

import bpy

from ..errors import CompileError
from ..compiler_identities import GroupCompilationIdentity
from .group_build_request import BlenderGroupBuildRequest
from .storage import _reset_node_group
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


def _copy_node_properties(src_node, dst_node):
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
    for src_node in src_group.nodes:
        dst_node = dst_group.nodes.new(src_node.bl_idname)
        if _raw_nodes.is_raw_node(src_node):
            _raw_nodes.copy_raw_node_properties(src_node, dst_node)
        else:
            _copy_node_properties(src_node, dst_node)
        node_map[src_node.name] = dst_node
    _sync_repeat_zone_dynamic_items(src_group, node_map)
    _sync_capture_attribute_dynamic_items(src_group, node_map)
    _sync_bundle_dynamic_items(src_group, node_map)

    for src_link in src_group.links:
        from_node = node_map.get(src_link.from_node.name)
        to_node = node_map.get(src_link.to_node.name)
        if from_node is None or to_node is None:
            continue
        if _raw_nodes.is_raw_node(src_link.from_node) or _raw_nodes.is_raw_node(src_link.to_node):
            from_socket = _raw_nodes.resolve_cutover_socket(src_link.from_node, src_link.from_socket, from_node, direction="output")
            to_socket = _raw_nodes.resolve_cutover_socket(src_link.to_node, src_link.to_socket, to_node, direction="input")
            dst_group.links.new(from_socket, to_socket)
            continue
        try:
            from_index = list(src_link.from_node.outputs).index(src_link.from_socket)
            to_index = list(src_link.to_node.inputs).index(src_link.to_socket)
            dst_group.links.new(from_node.outputs[from_index], to_node.inputs[to_index])
        except Exception:
            # Name fallback for dynamic sockets.
            try:
                dst_group.links.new(from_node.outputs[src_link.from_socket.name], to_node.inputs[src_link.to_socket.name])
            except Exception:
                raise
    for dst_node in node_map.values():
        if _raw_nodes.is_raw_node(dst_node):
            _raw_nodes.validate_raw_node_after_cutover(dst_node, dst_group)
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

        for attr in ("active_index", "define_signature"):
            if not hasattr(src_node, attr) or not hasattr(dst_node, attr):
                continue
            try:
                setattr(dst_node, attr, getattr(src_node, attr))
            except Exception as exc:
                raise CompileError(
                    f"Failed to restore Bundle property {attr!r} on node {src_node.name!r}: {exc}"
                ) from exc


def _sync_capture_attribute_dynamic_items(src_group, node_map):
    """Recreate Capture Attribute items before restoring copied links.

    Capture items define dynamic input/output sockets and are not copied by
    ordinary RNA property assignment. Rebuild them on the destination node so
    anonymous-attribute links survive transactional group cutover.
    """
    for src_node in src_group.nodes:
        if getattr(src_node, "bl_idname", None) != "GeometryNodeCaptureAttribute":
            continue
        dst_node = node_map.get(src_node.name)
        if dst_node is None or not hasattr(src_node, "capture_items") or not hasattr(dst_node, "capture_items"):
            continue
        try:
            for item in list(dst_node.capture_items):
                dst_node.capture_items.remove(item)
        except Exception:
            pass
        for item in list(src_node.capture_items):
            try:
                dst_node.capture_items.new(item.data_type, item.name)
            except Exception:
                # Link restoration below remains the transactional correctness
                # boundary if Blender cannot recreate a required socket.
                pass


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
            "original_name": getattr(group, "name", None) if original_name is None else original_name,
        }
        identity_record["mutations"].append(event)
        self._mutation_journal.append(event)
        return event

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
        self._closed = True

        failures = []
        for event in self._mutation_journal:
            old_manifest = event.get("old_manifest")
            new_manifest = event.get("new_manifest")
            if old_manifest is not None:
                try:
                    generated_resources.cleanup_previous_after_commit(
                        old_manifest,
                        new_manifest or generated_resources.build_manifest(old_manifest["owner_group_uuid"], []),
                    )
                except Exception as exc:
                    failures.append(exc)
            _remove_node_group_if_live(event.get("backup"), failures=failures)
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
        outermost = request.function_group_transaction is None
        build_tx = request.function_group_transaction or BlenderGroupBuildTransaction()
        if identity.root_owner_id is not None:
            _validate_root_owner_id_uniqueness(identity.root_owner_id, request.existing_group, build_tx)
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

        if request.existing_group is None:
            return self._create_fresh(
                request,
                build_tx,
                outermost=outermost,
                finalize_before_commit=finalize_before_commit,
            )
        if getattr(request.existing_group, "bl_idname", None) != "GeometryNodeTree":
            raise CompileError("Selected node group is not a GeometryNodeTree")
        return self._update_existing(
            request,
            build_tx,
            outermost=outermost,
            preserve_if_equivalent=request.preserve_if_equivalent,
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

    def _create_fresh(self, request, build_tx, *, outermost, finalize_before_commit):
        """Create, populate, and publish one fresh canonical group."""
        tx = generated_resources.GeneratedResourceTransaction(owner_group_uuid=uuid.uuid4().hex)
        savepoint = build_tx.savepoint()
        group = bpy.data.node_groups.new(request.name, "GeometryNodeTree")
        mark_provisional(group)
        build_tx.register_created(group, tx)
        try:
            self._populate(group, request, tx)
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

    def _update_existing(
        self,
        request,
        build_tx,
        *,
        outermost,
        preserve_if_equivalent,
        finalize_before_commit,
    ):
        """Build a private replacement, then cut over the authoritative datablock."""
        existing_group = request.existing_group
        name = request.name
        old_manifest = generated_resources.read_group_manifest(existing_group)
        owner_uuid = old_manifest["owner_group_uuid"] if old_manifest is not None else uuid.uuid4().hex
        tx = generated_resources.GeneratedResourceTransaction(owner_group_uuid=owner_uuid)
        tx_registered = False
        savepoint = build_tx.savepoint()
        replacement = bpy.data.node_groups.new("NodeForge.replacement." + name, "GeometryNodeTree")
        mark_transaction_private(replacement)
        backup = None
        try:
            self._populate(replacement, request, tx)
            new_manifest = tx.manifest(empty=not bool(tx.resources)) if (old_manifest is not None or tx.resources) else None
            if new_manifest is not None:
                generated_resources.write_group_manifest(replacement, new_manifest)

            if preserve_if_equivalent:
                old_fp = stored_fingerprint(existing_group)
                new_fp = stored_fingerprint(replacement)
                old_contract = stored_interface_contract(existing_group)
                live_contract = interface_contract(existing_group)
                new_contract = interface_contract(replacement)
                if old_fp and new_fp and old_fp == new_fp and old_contract and old_contract == live_contract and old_contract == new_contract:
                    tx.rollback()
                    build_tx.rollback_to_savepoint(savepoint)
                    if finalize_before_commit is not None:
                        backup = self._new_private_backup(existing_group, name)
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
                    if outermost:
                        build_tx.commit()
                    return existing_group

            backup = self._new_private_backup(existing_group, name)
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
            )
            tx_registered = True
            backup = None
            if finalize_before_commit is not None:
                finalize_before_commit(existing_group)
            if outermost:
                build_tx.commit()
            return existing_group
        except Exception as update_exc:
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
            _remove_node_group_if_live(replacement)
            _remove_node_group_if_live(backup)


__all__ = [
    "BlenderGroupBackend",
    "BlenderGroupBuildRequest",
    "BlenderGroupBuildTransaction",
    "_copy_group_contents",
    "_node_group_is_live",
    "_root_owner_id_for_build",
    "resolve_root_group_compilation_identity",
]
