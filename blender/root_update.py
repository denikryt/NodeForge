"""Invocation-local, one-pass replacement of ordinary base Geometry Nodes nodes.

The authoritative root and its structural I/O are never copied on success.
The one native root backup is used only for best-effort late failure recovery.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import bpy

from ..errors import CompileError
from ..function_instances import FUNCTION_ROOT_OWNER_ID_PROP
from .group_authority import is_authority_ineligible_group, mark_transaction_private
from .group_backend import (
    BlenderGroupBuildTransaction, _copy_node_properties, _sync_curve_mapping_state,
    _has_local_collection, _resolve_standalone_copy_socket, _sync_repeat_zone_dynamic_items,
    _verify_standalone_copy, _verify_standalone_defaults,
    _sync_bundle_dynamic_items,
)
from . import raw_nodes


_IO_TYPES = frozenset({'NodeGroupInput', 'NodeGroupOutput'})


@dataclass(frozen=True)
class RootUpdateResult:
    """Successful in-place update and the count of discarded old I/O edges."""

    target: object
    skipped_boundary_links: int


class RootUpdateError(CompileError):
    """Failed root update; `may_have_mutated` tells the UI whether Undo is needed."""

    def __init__(self, message, *, may_have_mutated: bool):
        """Retain whether a failed attempt needs Blender Undo history."""
        super().__init__(message)
        self.may_have_mutated = may_have_mutated


def validate_base_target(target):
    """Reject ineligible roots at the earliest boundary shared by UI/API."""
    if getattr(target, 'bl_idname', None) != 'GeometryNodeTree':
        raise CompileError('Open a base Geometry Nodes tree in the Node Editor')
    if (getattr(target, 'library', None) is not None
            or getattr(target, 'is_library_indirect', False)
            or not getattr(target, 'is_editable', True)):
        raise CompileError('The base Geometry Nodes tree is linked or read-only')
    if is_authority_ineligible_group(target):
        raise CompileError('Transaction-private or provisional node groups cannot be used as a base tree')
    if target.get(FUNCTION_ROOT_OWNER_ID_PROP) or target.get('nodeforge_function_definition_owner'):
        raise CompileError('An authoritative NodeForge function group cannot be used as a base tree')
    if target.get('nodeforge_mesh_compute_stage_v1'):
        raise CompileError('Mesh Compute-managed private roots cannot be used as a base tree')
    return target


def _action_paths(action):
    """Yield FCurve paths from a legacy or layered Blender Action."""
    # Blender 5.2 actions may store curves in slotted channelbags.
    for curve in getattr(action, 'fcurves', ()):
        yield curve.data_path
    for layer in getattr(action, 'layers', ()):
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', ()):
                for curve in bag.fcurves:
                    yield curve.data_path


def _animation_paths(tree):
    """Yield driver and active/NLA Action paths, including inactive NLA strips."""
    animation = tree.animation_data
    if animation is None:
        return
    for curve in animation.drivers:
        yield curve.data_path
    if animation.action is not None:
        yield from _action_paths(animation.action)
    # NLA strips can own Actions even when no active Action is assigned.
    for track in animation.nla_tracks:
        for strip in track.strips:
            if strip.action is not None:
                yield from _action_paths(strip.action)


def reject_node_local_animation(tree):
    """Disallow ordinary-node FCurves and drivers before any root mutation."""
    paths = tuple(_animation_paths(tree))
    if not paths:
        return
    for node in _ordinary(tree):
        # Blender owns RNA escaping; the separator prevents partial name matches.
        prefix = node.path_from_id()
        if any(path.startswith((prefix + '.', prefix + '[')) for path in paths):
            raise CompileError(
                f'Root node animation/driver path targets ordinary node {node.name!r}'
            )


def _ordinary(tree):
    """Return physical root nodes other than structural Group Input/Output."""
    return tuple(node for node in tree.nodes if node.bl_idname not in _IO_TYPES)


def _point(node):
    """Capture an absolute editor location independent of any parent Frame."""
    position = getattr(node, 'location_absolute', None)
    if position is not None:
        return tuple(position)
    position = tuple(node.location)
    parent = node.parent
    while parent is not None:
        position = tuple(a + b for a, b in zip(position, parent.location))
        parent = parent.parent
    return position


def _place_absolute(node, position):
    """Position a node in root coordinates even when nested under a Frame."""
    parent = node.parent
    if parent is not None:
        origin = _point(parent)
        node.location = tuple(a - b for a, b in zip(position, origin))
    else:
        node.location = position



def _parent_depth(node):
    """Return Frame nesting depth so layout parents move before children."""
    depth = 0
    parent = node.parent
    while parent is not None:
        depth += 1
        parent = parent.parent
    return depth


def _shape(node):
    """Identify a conservative physical node/function counterpart for layout."""
    group = getattr(node, 'node_tree', None) if node.bl_idname == 'GeometryNodeGroup' else None
    if group is not None:
        function = (group.get('nodeforge_function_definition_owner'),
                    group.get('nodeforge_module_name'),
                    group.get('nodeforge_function_instance_key'))
        key = function if any(function) else ('manual', group.as_pointer())
    else:
        key = None
    return (node.bl_idname, key, getattr(node, 'operation', None),
            getattr(node, 'data_type', None))


def _matches(old, new):
    """Match ordered identical shapes, otherwise only individually unique shapes."""
    old_shapes = [_shape(node) for node in old]
    new_shapes = [_shape(node) for node in new]
    if old_shapes == new_shapes:
        return {a.as_pointer(): b for a, b in zip(old, new)}
    old_counts, new_counts = Counter(old_shapes), Counter(new_shapes)
    unique = {shape: node for node, shape in zip(new, new_shapes) if new_counts[shape] == 1}
    return {node.as_pointer(): unique[shape]
            for node, shape in zip(old, old_shapes)
            if old_counts[shape] == 1 and shape in unique}


def _socket_ref(node, socket, *, output):
    """Detach physical socket identity, display name, type and socket index."""
    sockets = node.outputs if output else node.inputs
    return (socket.identifier, socket.name, socket.bl_idname,
            next(i for i, candidate in enumerate(sockets) if candidate == socket))


def _resolve_socket(node, ref, *, output):
    """Resolve only a unique compatible socket, never guess between duplicates."""
    sockets = node.outputs if output else node.inputs
    identifier, name, kind, index = ref
    candidates = [s for s in sockets if s.identifier == identifier and s.bl_idname == kind]
    if len(candidates) == 1 and candidates[0].name == name:
        return candidates[0]
    candidates = [s for s in sockets if s.name == name and s.bl_idname == kind]
    if len(candidates) == 1 and list(sockets).index(candidates[0]) == index:
        return candidates[0]
    return None


def _boundary(tree):
    """Record pre-stage I/O-facing links in native output-to-input direction."""
    edges = []
    for link in tree.links:
        source, dest = link.from_node, link.to_node
        if (source.bl_idname in _IO_TYPES) == (dest.bl_idname in _IO_TYPES):
            continue
        edges.append((source.as_pointer(), _socket_ref(source, link.from_socket, output=True),
                      dest.as_pointer(), _socket_ref(dest, link.to_socket, output=False)))
    return tuple(edges)


def _interface_signature(tree):
    """Capture immutable interface ordering, identities and defaults for validation."""
    signature = []
    for item in tree.interface.items_tree:
        default = getattr(item, 'default_value', None)
        if default is not None and not isinstance(default, (int, float, bool, str)):
            try:
                default = tuple(default)
            except TypeError:
                default = repr(default)
        signature.append((item.item_type, item.name, getattr(item, 'identifier', None),
                          getattr(item, 'in_out', None), getattr(item, 'socket_type', None),
                          default))
    return tuple(signature)


def _reconnect_boundary(tree, records, matched):
    """Restore compatible, unambiguous pre-stage I/O links in original direction."""
    skipped = 0
    io_by_pointer = {node.as_pointer(): node for node in tree.nodes if node.bl_idname in _IO_TYPES}
    for source_pointer, output_ref, target_pointer, input_ref in records:
        source = matched.get(source_pointer) or io_by_pointer.get(source_pointer)
        target = matched.get(target_pointer) or io_by_pointer.get(target_pointer)
        if source is None or target is None:
            skipped += 1
            continue
        output = _resolve_socket(source, output_ref, output=True)
        input_ = _resolve_socket(target, input_ref, output=False)
        if output is None or input_ is None:
            skipped += 1
            continue
        # Never replace a newly staged input or guess between competing old edges.
        if not getattr(input_, 'is_multi_input', False) and input_.is_linked:
            skipped += 1
            continue
        tree.links.new(output, input_)
    return skipped


def _copy_missing_old_nodes(tree, backup, old_ptrs):
    """Best-effort local reconstruction, retaining surviving structural nodes."""
    # Old nodes that survived a partial cutover are deliberately not duplicated.
    survived = {node.name: node for node in _ordinary(tree)
                if node.as_pointer() in old_ptrs}
    mapped = dict(survived)
    proofs = {}
    for source in backup.nodes:
        if source.bl_idname in _IO_TYPES or source.name in mapped:
            continue
        target = tree.nodes.new(source.bl_idname)
        if raw_nodes.is_raw_node(source):
            raw_nodes.copy_raw_node_properties(source, target, proofs=proofs)
        else:
            _copy_node_properties(source, target, proofs=proofs)
        mapped[source.name] = target
    # Assign parent references in a separate pass after all Frames exist.
    for source in backup.nodes:
        if source.bl_idname in _IO_TYPES:
            continue
        target = mapped.get(source.name)
        if target is None:
            continue
        parent = source.parent
        target.parent = mapped.get(parent.name) if parent is not None else None
        target.location = tuple(source.location)
    _sync_repeat_zone_dynamic_items(backup, mapped)
    _sync_bundle_dynamic_items(backup, mapped)
    _sync_curve_mapping_state(backup, mapped)
    io = {n.name: n for n in tree.nodes if n.bl_idname in _IO_TYPES}
    for link in backup.links:
        source = mapped.get(link.from_node.name) or io.get(link.from_node.name)
        dest = mapped.get(link.to_node.name) or io.get(link.to_node.name)
        if source is None or dest is None:
            continue
        if link.from_node.bl_idname in _IO_TYPES and link.to_node.bl_idname in _IO_TYPES:
            continue  # Physical I/O-only edges were never deleted.
        def endpoint(original, socket, target, *, output):
            if _has_local_collection(original):
                if raw_nodes.is_raw_node(original):
                    return raw_nodes.resolve_cutover_socket(original, socket, target, direction='output' if output else 'input')
                return _resolve_standalone_copy_socket(original, socket, target, output=output, proofs=proofs)
            return _resolve_socket(target, _socket_ref(original, socket, output=output), output=output)

        left = endpoint(link.from_node, link.from_socket, source, output=True)
        right = endpoint(link.to_node, link.to_socket, dest, output=False)
        if left is None or right is None:
            raise RuntimeError(f'Cannot restore root link {link.from_node.name} -> {link.to_node.name}')
        if any(edge.from_socket == left and edge.to_socket == right for edge in tree.links):
            continue
        restored = tree.links.new(left, right)
        if (_has_local_collection(link.from_node)
                or _has_local_collection(link.to_node)):
            if restored.from_socket != left or restored.to_socket != right or not restored.is_valid:
                raise CompileError('Collection copy: Blender did not preserve the restored root link')
    for original in backup.nodes:
        if not _has_local_collection(original):
            continue
        target = mapped[original.name]
        _verify_standalone_copy(original, target, proofs=proofs)
        if raw_nodes.is_raw_node(original):
            raw_nodes.validate_raw_node_after_cutover(target, tree)
        else:
            _verify_standalone_defaults(original, target)
    return mapped


def _restore_structural_io(tree, io_nodes, positions, active_outputs, parent_names):
    """Restore physical I/O Frame parenting and locations after failed cutover."""
    for node in io_nodes:
        parent_name = parent_names.get(node.as_pointer())
        if parent_name is not None:
            parent = tree.nodes.get(parent_name)
            if parent is None or parent.bl_idname != 'NodeFrame':
                raise RuntimeError(f'Cannot restore structural I/O Frame {parent_name!r}')
            node.parent = parent
        _place_absolute(node, positions[node.as_pointer()])
        if node.bl_idname == 'NodeGroupOutput':
            node.is_active_output = active_outputs[node.as_pointer()]


def _remove_staged(tree, preexisting_ptrs):
    """Remove all attempt-created nodes, including unexpected structural ones."""
    failures = []
    for node in tuple(tree.nodes):
        if node.as_pointer() not in preexisting_ptrs:
            try:
                tree.nodes.remove(node)
            except Exception as exc:
                failures.append(f'new node cleanup: {exc}')
    return failures


def _remove_backup(backup):
    """Dispose of the sole private copy, including on failed attempts."""
    if backup is not None and any(group is backup for group in bpy.data.node_groups):
        bpy.data.node_groups.remove(backup, do_unlink=True)


def _fail(error, failures, *, possible_change):
    """Describe cleanup failures independently and retain Undo if state may differ."""
    summary = str(error)
    notes = getattr(error, '__notes__', ())
    if notes:
        summary += '; ' + '; '.join(notes)
    if failures:
        summary += '; ' + '; '.join(failures)
    raise RootUpdateError(summary, may_have_mutated=possible_change or bool(failures)) from error


def replace_prepared_root(prepared, target, backend, session, *, root_owner_uuid=None):
    """Publish one privately lowered root graph with one rollback/commit owner."""
    import warnings

    from ..function_instances import (
        COMPOSITION_ROOT_ID_PROP, new_root_owner_id, validate_root_owner_id,
    )
    from .direct_composition import validate_prepared_direct
    from .generated_resources import (
        GROUP_MANIFEST_PROP, capture_owned_resource_ids, cleanup_previous_after_commit,
        read_group_manifest, write_group_manifest,
    )
    from .root_candidate import build_private_root_candidate, discard_private_root_candidate
    from .root_subgraph_transfer import transfer_private_root_subgraph
    validate_base_target(target)
    reject_node_local_animation(target)
    validate_prepared_direct(prepared, target)
    root_owner_uuid = validate_root_owner_id(root_owner_uuid or target.get(COMPOSITION_ROOT_ID_PROP)
                                            or new_root_owner_id())
    root_pointer, root_name = target.as_pointer(), target.name
    interface_before = _interface_signature(target)
    old = _ordinary(target)
    old_ptrs = {node.as_pointer() for node in old}
    preexisting_ptrs = {node.as_pointer() for node in target.nodes}
    old_positions = {node.as_pointer(): _point(node) for node in old}
    io_nodes = tuple(node for node in target.nodes if node.bl_idname in _IO_TYPES)
    io_positions = {node.as_pointer(): _point(node) for node in io_nodes}
    io_active = {node.as_pointer(): getattr(node, 'is_active_output', False) for node in io_nodes
                 if node.bl_idname == 'NodeGroupOutput'}
    io_parent_names = {node.as_pointer(): node.parent.name for node in io_nodes
                       if node.parent is not None and node.parent.as_pointer() in old_ptrs}
    boundary = _boundary(target)
    original_node_order = tuple(node.as_pointer() for node in target.nodes)
    def link_contracts():
        return tuple((link.from_node.as_pointer(), _socket_ref(link.from_node, link.from_socket, output=True),
                      link.to_node.as_pointer(), _socket_ref(link.to_node, link.to_socket, output=False))
                     for link in target.links)

    original_link_endpoints = link_contracts()
    old_manifest = read_group_manifest(target)
    old_resource_ids = capture_owned_resource_ids(target, old_manifest) if old_manifest else ()
    old_ownership = {key: target.get(key) for key in (COMPOSITION_ROOT_ID_PROP, GROUP_MANIFEST_PROP)}
    transaction = BlenderGroupBuildTransaction()
    backup = None
    candidate = None
    resource_tx = None
    cutover_started = False
    io_detach_started = False
    try:
        backup = target.copy()
        mark_transaction_private(backup)
        candidate = build_private_root_candidate(
            prepared, target, backend, session, transaction=transaction,
            root_owner_uuid=root_owner_uuid,
        )
        resource_tx = candidate.resource_transaction

        def restore_resource_ownership():
            """Restore pre-attempt root metadata and remove exact new IDs on rollback."""
            for key, value in old_ownership.items():
                if value is None:
                    if key in target:
                        del target[key]
                else:
                    target[key] = value
            resource_tx.rollback()

        # Root resource publication shares the nested-group PONR. This callback
        # only restores state before commit; it never starts another transaction.
        transaction.register_external_cutover(
            rollback=restore_resource_ownership, commit=lambda: None,
        )

        # A Python backend only sees its private candidate. Nevertheless,
        # refuse to overwrite unexplained changes to authoritative root state.
        # Updating a nested authoritative group can reallocate the native
        # Socket_N IDs on existing manual Group Nodes even without adding links.
        # That Blender-side change is permitted; unexplained root mutations are not.
        updated_group_ids = {record['group'].as_pointer() for record in transaction.updated_groups}
        updated_nodes = {
            node.as_pointer() for node in target.nodes
            if node.bl_idname == 'GeometryNodeGroup' and node.node_tree is not None
            and node.node_tree.as_pointer() in updated_group_ids
        }

        def stable_link(contract):
            source_id, from_socket, destination_id, to_socket = contract
            if source_id in updated_nodes:
                from_socket = ('<updated-group>',) + from_socket[1:]
            if destination_id in updated_nodes:
                to_socket = ('<updated-group>',) + to_socket[1:]
            return source_id, from_socket, destination_id, to_socket

        original_stable = tuple(stable_link(link) for link in original_link_endpoints)
        current_stable = tuple(stable_link(link) for link in link_contracts())
        removed_links = [link for link in original_stable if link not in current_stable]
        added_links = [link for link in current_stable if link not in original_stable]
        valid_blender_removal = all(link[0] in updated_nodes or link[2] in updated_nodes
                                    for link in removed_links)
        if (_interface_signature(target) != interface_before
                or tuple(n.as_pointer() for n in target.nodes) != original_node_order
                or added_links or not valid_blender_removal):
            raise CompileError('Root backend inserted unapproved structural or other nodes/links')
        transferred = transfer_private_root_subgraph(
            candidate, target, interface_signature=interface_before,
        )
        new = tuple(node for node in transferred.created_nodes if node.bl_idname not in _IO_TYPES)
        matched = _matches(old, new)
        for node in io_nodes:
            if node.parent is not None and node.parent.as_pointer() in old_ptrs:
                absolute = io_positions[node.as_pointer()]
                io_detach_started = True
                node.parent = None
                _place_absolute(node, absolute)
        for node in old:
            cutover_started = True
            target.nodes.remove(node)
        skipped = _reconnect_boundary(target, boundary, matched)
        for pointer, node in sorted(matched.items(), key=lambda item: _parent_depth(item[1])):
            _place_absolute(node, old_positions[pointer])
        if (target.as_pointer() != root_pointer or target.name != root_name
                or _interface_signature(target) != interface_before
                or any(not any(n.as_pointer() == io.as_pointer() for n in target.nodes) for io in io_nodes)
                or any(_point(n) != io_positions[n.as_pointer()] for n in io_nodes)
                or any(getattr(n, 'is_active_output', False) != io_active[n.as_pointer()]
                       for n in io_nodes if n.bl_idname == 'NodeGroupOutput')):
            raise RuntimeError('Update changed root identity, interface or structural I/O')
        if {node.as_pointer() for node in _ordinary(target)} != {node.as_pointer() for node in new}:
            raise RuntimeError('Update left old ordinary nodes or lost staged nodes')

        # No other Blender ID is mutated after the nested group commit begins.
        # Dispose both private copies first (their pointers could otherwise keep
        # old resources and groups artificially alive during retirement).
        discard_private_root_candidate(candidate)
        candidate = None
        target[COMPOSITION_ROOT_ID_PROP] = root_owner_uuid
        new_manifest = resource_tx.manifest()
        write_group_manifest(target, new_manifest)
        _remove_backup(backup)
        backup = None
        transaction.commit()
        resource_tx.mark_committed()
        if old_manifest is not None:
            try:
                cleanup_previous_after_commit(old_manifest, new_manifest, captured_ids=old_resource_ids)
            except Exception as cleanup_error:
                warnings.warn(f'NodeForge committed with root resource cleanup warning: {cleanup_error}',
                              RuntimeWarning, stacklevel=2)
        return RootUpdateResult(target, skipped)
    except Exception as exc:
        if transaction._closed:
            # An exception from best-effort retirement cannot roll back groups
            # or the already-published root. Preserve UI Undo explicitly.
            if resource_tx is not None:
                resource_tx.mark_committed()
            raise RootUpdateError(
                f'Root graph committed, but postcommit cleanup failed: {exc}',
                may_have_mutated=True,
            ) from exc
        possible_change = cutover_started or bool(transaction.updated_groups)
        errors = _remove_staged(target, preexisting_ptrs)
        if candidate is not None:
            try:
                discard_private_root_candidate(candidate)
            except Exception as cleanup_error:
                errors.append(f'candidate cleanup: {cleanup_error}')
        try:
            transaction.rollback()
        except Exception as rollback_error:
            errors.append(f'nested-group/resource rollback: {rollback_error}')
        if cutover_started and backup is not None:
            try:
                _copy_missing_old_nodes(target, backup, old_ptrs)
                _restore_structural_io(target, io_nodes, io_positions, io_active, io_parent_names)
            except Exception as restore_error:
                errors.append(f'root restoration: {restore_error}')
        elif cutover_started:
            errors.append('root restoration: native root backup unavailable')
        elif io_detach_started:
            try:
                _restore_structural_io(target, io_nodes, io_positions, io_active, io_parent_names)
            except Exception as restore_error:
                errors.append(f'structural I/O restoration: {restore_error}')
                possible_change = True
        try:
            _remove_backup(backup)
        except Exception as cleanup_error:
            errors.append(f'backup cleanup: {cleanup_error}')
        if any(node.as_pointer() not in preexisting_ptrs for node in target.nodes):
            possible_change = True
        _fail(exc, errors, possible_change=possible_change)
