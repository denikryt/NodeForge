"""Invocation-local direct root staging and local cutover helpers.

Ordinary nodes are staged into the authoritative root; nested transactions
remain caller-owned until successful cutover.
"""

from __future__ import annotations
from dataclasses import dataclass
import bpy
from ..errors import CompileError
from .group_authority import mark_transaction_private
from .group_backend import (
    BlenderGroupBuildTransaction, _copy_node_properties, _sync_curve_mapping_state,
    _sync_capture_attribute_dynamic_items, _sync_repeat_zone_dynamic_items,
    _sync_bundle_dynamic_items,
)
from . import raw_nodes

_IO_TYPES = frozenset({'NodeGroupInput', 'NodeGroupOutput'})


class RootUpdateError(CompileError):
    """Failed root update; `may_have_mutated` tells the UI whether Undo is needed."""

    def __init__(self, message, *, may_have_mutated: bool):
        """Retain whether a failed attempt needs Blender Undo history."""
        super().__init__(message)
        self.may_have_mutated = may_have_mutated


@dataclass(frozen=True)
class RootUpdateResult:
    """Successful in-place update and the count of discarded old I/O edges."""

    target: object
    skipped_boundary_links: int


@dataclass
class RootUpdateStage:
    """Invocation-owned snapshot, native backup and uncommitted nested transaction."""

    target: object
    old: tuple
    old_ptrs: set
    preexisting_ptrs: set
    io_nodes: tuple
    io_positions: dict
    io_active: dict
    interface_before: tuple
    root_pointer: int
    root_name: str
    boundary: tuple
    transaction: object
    backup: object
    new: tuple


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


def _boundary(tree, old):
    """Record old I/O-facing edges without retaining mutable node/socket handles."""
    old_ptrs = {node.as_pointer() for node in old}
    edges = []
    for link in tree.links:
        source, dest = link.from_node, link.to_node
        if source.as_pointer() in old_ptrs and dest.bl_idname in _IO_TYPES:
            edges.append((source.as_pointer(), _socket_ref(source, link.from_socket, output=True),
                          dest.as_pointer(), _socket_ref(dest, link.to_socket, output=False), True))
        elif dest.as_pointer() in old_ptrs and source.bl_idname in _IO_TYPES:
            edges.append((dest.as_pointer(), _socket_ref(dest, link.to_socket, output=False),
                          source.as_pointer(), _socket_ref(source, link.from_socket, output=True), False))
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


def _copy_missing_old_nodes(tree, backup, old_ptrs):
    """Best-effort local reconstruction, retaining surviving structural nodes."""
    # Old nodes that survived a partial cutover are deliberately not duplicated.
    survived = {node.name: node for node in _ordinary(tree)
                if node.as_pointer() in old_ptrs}
    mapped = dict(survived)
    for source in backup.nodes:
        if source.bl_idname in _IO_TYPES or source.name in mapped:
            continue
        target = tree.nodes.new(source.bl_idname)
        if raw_nodes.is_raw_node(source):
            raw_nodes.copy_raw_node_properties(source, target)
        else:
            _copy_node_properties(source, target)
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
    _sync_capture_attribute_dynamic_items(backup, mapped)
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
        left = _resolve_socket(source, _socket_ref(link.from_node, link.from_socket, output=True), output=True)
        right = _resolve_socket(dest, _socket_ref(link.to_node, link.to_socket, output=False), output=False)
        if left is None or right is None:
            raise RuntimeError(f'Cannot restore root link {link.from_node.name} -> {link.to_node.name}')
        if any(edge.from_socket == left and edge.to_socket == right for edge in tree.links):
            continue
        tree.links.new(left, right)
    return mapped


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


def stage_prepared_root(prepared, target, backend, session, *, source_module):
    """Stage ordinary nodes once alongside the old graph without publishing it.

    On failure, only staged nodes are removed and nested rollback is attempted.
    A successful result owns an uncommitted transaction and private root copy;
    its caller must complete cutover or explicitly clean these resources.
    """
    from .direct_composition import compose_prepared_initial, validate_prepared_direct

    validate_prepared_direct(prepared, target)
    old = _ordinary(target)
    old_ptrs = {node.as_pointer() for node in old}
    preexisting_ptrs = {node.as_pointer() for node in target.nodes}
    io_nodes = tuple(node for node in target.nodes if node.bl_idname in _IO_TYPES)
    io_positions = {node.as_pointer(): _point(node) for node in io_nodes}
    io_active = {node.as_pointer(): getattr(node, 'is_active_output', False) for node in io_nodes
                 if node.bl_idname == 'NodeGroupOutput'}
    state = RootUpdateStage(
        target=target, old=old, old_ptrs=old_ptrs, preexisting_ptrs=preexisting_ptrs,
        io_nodes=io_nodes, io_positions=io_positions, io_active=io_active,
        interface_before=_interface_signature(target), root_pointer=target.as_pointer(),
        root_name=target.name, boundary=_boundary(target, old),
        transaction=BlenderGroupBuildTransaction(), backup=None, new=(),
    )
    try:
        state.backup = target.copy()
        mark_transaction_private(state.backup)
        compose_prepared_initial(prepared, target, backend, session,
                                 source_module=source_module, transaction=state.transaction)
        state.new = tuple(node for node in target.nodes if node.as_pointer() not in preexisting_ptrs)
        if any(node.bl_idname in _IO_TYPES for node in state.new):
            raise CompileError('Direct Update cannot insert new structural Group Input/Output nodes')
        return state
    except Exception as exc:
        possible_change = bool(state.transaction.updated_groups)
        errors = _remove_staged(target, preexisting_ptrs)
        try:
            state.transaction.rollback()
        except Exception as rollback_error:
            errors.append(f'nested-group rollback: {rollback_error}')
        try:
            _remove_backup(state.backup)
        except Exception as cleanup_error:
            errors.append(f'backup cleanup: {cleanup_error}')
        _fail(exc, errors, possible_change=possible_change)


def cutover_staged_root(stage):
    """Publish a previously staged root without guessing I/O links or positions.

    The complete compatibility transfer is introduced by the public Update API
    at the next checkpoint. This primitive owns final cleanup and commit.
    """
    tree = stage.target
    cutover_started = False
    backup = stage.backup
    try:
        for io in stage.io_nodes:
            if io.parent is not None and io.parent.as_pointer() in stage.old_ptrs:
                absolute = stage.io_positions[io.as_pointer()]
                io.parent = None
                _place_absolute(io, absolute)
        for node in stage.old:
            cutover_started = True
            tree.nodes.remove(node)
        if (tree.as_pointer() != stage.root_pointer or tree.name != stage.root_name
                or _interface_signature(tree) != stage.interface_before
                or any(not any(n.as_pointer() == io.as_pointer() for n in tree.nodes)
                       for io in stage.io_nodes)
                or any(_point(n) != stage.io_positions[n.as_pointer()] for n in stage.io_nodes)
                or any(getattr(n, 'is_active_output', False) != stage.io_active[n.as_pointer()]
                       for n in stage.io_nodes if n.bl_idname == 'NodeGroupOutput')):
            raise RuntimeError('Update changed root identity, interface or structural I/O')
        if {node.as_pointer() for node in _ordinary(tree)} != {node.as_pointer() for node in stage.new}:
            raise RuntimeError('Update left old ordinary nodes or lost staged nodes')
        _remove_backup(backup)
        backup = None
        stage.transaction.commit()
        return RootUpdateResult(tree, len(stage.boundary))
    except Exception as exc:
        possible_change = cutover_started or bool(stage.transaction.updated_groups)
        errors = _remove_staged(tree, stage.preexisting_ptrs)
        try:
            stage.transaction.rollback()
        except Exception as rollback_error:
            errors.append(f'nested-group rollback: {rollback_error}')
        if cutover_started and backup is not None:
            try:
                _copy_missing_old_nodes(tree, backup, stage.old_ptrs)
                for io in stage.io_nodes:
                    _place_absolute(io, stage.io_positions[io.as_pointer()])
                    if io.bl_idname == 'NodeGroupOutput':
                        io.is_active_output = stage.io_active[io.as_pointer()]
            except Exception as restore_error:
                errors.append(f'root restoration: {restore_error}')
        elif cutover_started:
            errors.append('root restoration: native root backup unavailable')
        try:
            _remove_backup(backup)
        except Exception as cleanup_error:
            errors.append(f'backup cleanup: {cleanup_error}')
        _fail(exc, errors, possible_change=possible_change)
