"""Invocation-local direct root staging and local cutover helpers.

Ordinary nodes are staged into the authoritative root; nested transactions
remain caller-owned until successful cutover.
"""

from __future__ import annotations
from dataclasses import dataclass
import bpy
from ..errors import CompileError
from .group_authority import mark_transaction_private
from .group_backend import BlenderGroupBuildTransaction

_IO_TYPES = frozenset({'NodeGroupInput', 'NodeGroupOutput'})


class RootUpdateError(CompileError):
    """Failed root update; `may_have_mutated` tells the UI whether Undo is needed."""

    def __init__(self, message, *, may_have_mutated: bool):
        """Retain whether a failed attempt needs Blender Undo history."""
        super().__init__(message)
        self.may_have_mutated = may_have_mutated


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


def _socket_ref(node, socket, *, output):
    """Detach physical socket identity, display name, type and socket index."""
    sockets = node.outputs if output else node.inputs
    return (socket.identifier, socket.name, socket.bl_idname,
            next(i for i, candidate in enumerate(sockets) if candidate == socket))


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
