"""Strictly transfer a private root candidate without deleting existing root nodes.

This is a copier/validator, never a second transaction owner. The outer root
replacement orchestrator must remove partially transferred nodes on failure.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import CompileError
from . import raw_nodes
from .group_backend import (
    _copy_node_properties,
    _sync_bundle_dynamic_items,
    _STANDALONE_COLLECTION_NODES, _verify_standalone_copy,
    _resolve_standalone_copy_socket, _verify_standalone_defaults,
    _sync_repeat_zone_dynamic_items,
)
from .root_update import _interface_signature


_UNSUPPORTED_ZONE_NODES = frozenset({
    'GeometryNodeSimulationInput', 'GeometryNodeSimulationOutput',
})


@dataclass(frozen=True)
class RootSubgraphTransfer:
    """New physical nodes and the newly created canonical input, if any."""

    created_nodes: tuple
    new_group_input: object | None


def _socket_contract(node, *, outputs):
    sockets = node.outputs if outputs else node.inputs
    return tuple((socket.identifier, socket.name, socket.bl_idname,
                  bool(socket.is_output), bool(getattr(socket, 'is_multi_input', False)),
                  bool(getattr(socket, 'enabled', True)), bool(getattr(socket, 'hide', False)))
                 for socket in sockets)


def _raw_defaults(node, *, outputs):
    sockets = node.outputs if outputs else node.inputs
    values = []
    for socket in sockets:
        if not hasattr(socket, 'default_value'):
            values.append(None)
            continue
        value = socket.default_value
        if hasattr(value, 'as_pointer'):
            values.append(('ID', value.as_pointer()))
        elif isinstance(value, (bool, int, float, str)):
            values.append(value)
        else:
            try:
                values.append(tuple(value))
            except (TypeError, ValueError):
                values.append(repr(value))
    return tuple(values)


def _default_equal(a, b):
    if type(a) in (int, float) and type(b) in (int, float):
        return abs(float(a) - float(b)) <= 1e-6
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(_default_equal(x, y) for x, y in zip(a, b))
    return a == b


def _strict_rna_equal(src, dst):
    """Verify supported writable properties after the existing node copier."""
    ignored = {
        'rna_type', 'type', 'dimensions', 'inputs', 'outputs', 'internal_links',
        'select', 'width_hidden', 'height', 'name', 'location', 'location_absolute', 'parent',
        'mapping', 'active_item', 'capture_items', 'repeat_items',
        'bundle_items', 'paired_output',  # Their structure is checked below.
    }
    for prop in src.bl_rna.properties:
        name = getattr(prop, 'identifier', '')
        if not name or name in ignored or getattr(prop, 'is_readonly', False):
            continue
        try:
            original = getattr(src, name)
            copied = getattr(dst, name)
        except Exception as exc:
            raise CompileError(f'Cannot inspect copied node property {src.bl_idname}.{name}') from exc
        if hasattr(original, 'as_pointer'):
            if original is None or copied is None:
                if original is not copied:
                    raise CompileError(f'Node property {src.bl_idname}.{name} lost datablock reference')
            elif not hasattr(copied, 'as_pointer') or original.as_pointer() != copied.as_pointer():
                raise CompileError(f'Node property {src.bl_idname}.{name} changed datablock reference')
        elif isinstance(original, (str, int, float, bool)):
            if not _default_equal(original, copied):
                raise CompileError(f'Node property {src.bl_idname}.{name} changed during transfer')
        else:
            try:
                old, new = tuple(original), tuple(copied)
            except TypeError:
                if original != copied:
                    raise CompileError(f'Cannot preserve node property {src.bl_idname}.{name}')
            else:
                if not _default_equal(old, new):
                    raise CompileError(f'Node property {src.bl_idname}.{name} changed during transfer')

    if set(src.keys()) != set(dst.keys()):
        raise CompileError(f'Node custom metadata differs after copying {src.bl_idname}')
    for key in src.keys():
        if src[key] != dst[key]:
            raise CompileError(f'Node custom metadata {key!r} differs after copying')


def _dynamic_contract(node):
    kind = node.bl_idname
    if kind in {'NodeSeparateBundle', 'NodeCombineBundle'}:
        return tuple((item.name, item.socket_type, getattr(item, 'structure_type', None))
                     for item in node.bundle_items)
    if kind == 'GeometryNodeRepeatOutput':
        return tuple((item.name, item.socket_type) for item in node.repeat_items)
    return ()


def _curve_contract(node):
    mapping = getattr(node, 'mapping', None)
    if mapping is None:
        return None
    return tuple(tuple((tuple(point.location), point.handle_type) for point in curve.points)
                 for curve in mapping.curves)


def _strict_node_parity(src, dst):
    if src.bl_idname != dst.bl_idname:
        raise CompileError('Root transfer changed a node type')
    if src.bl_idname in _STANDALONE_COLLECTION_NODES:
        _verify_standalone_copy(src, dst)
    elif (_socket_contract(src, outputs=False) != _socket_contract(dst, outputs=False)
            or _socket_contract(src, outputs=True) != _socket_contract(dst, outputs=True)):
        raise CompileError(f'Root transfer socket parity failed for {src.bl_idname} ({src.name})')
    if _dynamic_contract(src) != _dynamic_contract(dst):
        raise CompileError(f'Root transfer lost dynamic items on {src.bl_idname}')
    if _curve_contract(src) != _curve_contract(dst):
        raise CompileError(f'Root transfer lost CurveMapping on {src.bl_idname}')
    _strict_rna_equal(src, dst)
    if src.bl_idname in _STANDALONE_COLLECTION_NODES and not raw_nodes.is_raw_node(src):
        _verify_standalone_defaults(src, dst)
        return
    for direction in (False, True):
        before = _raw_defaults(src, outputs=direction)
        after = _raw_defaults(dst, outputs=direction)
        if not _default_equal(before, after):
            raise CompileError(f'Root transfer lost socket default on {src.bl_idname}')


def _strict_preflight(stage, target, endpoint, interface_signature):
    if (_interface_signature(target) != interface_signature
            or _interface_signature(stage) != interface_signature):
        raise CompileError('Root interface changed before strict transfer')
    group_inputs = [node for node in stage.nodes if node.bl_idname == 'NodeGroupInput']
    if any(node.bl_idname == 'NodeGroupOutput' for node in stage.nodes):
        raise CompileError('Root backend inserted unapproved structural Group Output nodes')
    expected_inputs = [] if endpoint.node is None else [endpoint.node]
    if group_inputs != expected_inputs:
        raise CompileError('Root stage contains an unauthorized Group Input')
    first_input = next((node for node in target.nodes if node.bl_idname == 'NodeGroupInput'), None)
    if first_input is not None and group_inputs and endpoint.created_by_attempt:
        raise CompileError('Root stage tried to create a second Group Input')
    for node in stage.nodes:
        if node.bl_idname in _UNSUPPORTED_ZONE_NODES:
            raise CompileError(f'Root transfer does not support {node.bl_idname} zones')
        if node.bl_idname == 'GeometryNodeGroup' and node.node_tree in (stage, target):
            raise CompileError('Root transfer cannot embed the root or private stage as a child group')
        if node.bl_idname == 'GeometryNodeRepeatInput' and getattr(node, 'paired_output', None) is None:
            raise CompileError('Unpaired Repeat Input in private root graph')
        if node.bl_idname == 'GeometryNodeRepeatOutput' and not any(
            n.bl_idname == 'GeometryNodeRepeatInput' and getattr(n, 'paired_output', None) == node
            for n in stage.nodes
        ):
            raise CompileError('Unpaired Repeat Output in private root graph')
        if node.parent is not None and node.parent.id_data is not stage:
            raise CompileError('Node in private root stage has an external parent')
    for link in stage.links:
        if (link.from_node.id_data is not stage or link.to_node.id_data is not stage
                or not link.from_socket.is_output or link.to_socket.is_output
                or not getattr(link, 'is_valid', True)):
            raise CompileError(
                f'Root transfer received an invalid or cross-tree link: '
                f'{link.from_node.bl_idname}.{link.from_socket.name} -> '
                f'{link.to_node.bl_idname}.{link.to_socket.name}'
            )
    return first_input


def transfer_private_root_subgraph(candidate, target, *, interface_signature):
    """Validate the private stage and materialize only its new graph in ``target``.

    This function never removes old root nodes. If an exception is raised after
    allocation, the sole root transaction owner removes attempt-created nodes.
    """
    stage, endpoint = candidate.stage, candidate.endpoint
    original_input = _strict_preflight(stage, target, endpoint, interface_signature)
    nodes = {}
    created = []
    new_input = None
    for src in stage.nodes:
        if src.bl_idname == 'NodeGroupInput' and original_input is not None:
            nodes[src.name] = original_input
            # Comparison must be read-only: do not apply candidate RNA to a user Input.
            if (_socket_contract(src, outputs=True) != _socket_contract(original_input, outputs=True)
                    or _socket_contract(src, outputs=False) != _socket_contract(original_input, outputs=False)):
                raise CompileError('Canonical root Group Input sockets differ from the private copy')
            _strict_rna_equal(src, original_input)
            continue
        try:
            dst = target.nodes.new(src.bl_idname)
        except Exception as exc:
            raise CompileError(f'Cannot transfer Blender node {src.bl_idname}') from exc
        nodes[src.name] = dst
        created.append(dst)
        if src.bl_idname == 'NodeGroupInput':
            new_input = dst
        if raw_nodes.is_raw_node(src):
            raw_nodes.copy_raw_node_properties(src, dst)
        else:
            _copy_node_properties(src, dst)
    # Dynamic sockets must exist before copying their defaults and rebuilding links.
    # A temporary name index points at destination objects despite Blender's name
    # disambiguation against still-live root nodes during staging.
    _sync_repeat_zone_dynamic_items(stage, nodes)
    _sync_bundle_dynamic_items(stage, nodes)
    from .group_backend import _copy_socket_default, _copy_curve_mapping
    for src in stage.nodes:
        dst = nodes[src.name]
        if dst is original_input or src.bl_idname in _STANDALONE_COLLECTION_NODES:
            continue
        for direction in ('inputs', 'outputs'):
            original_sockets = getattr(src, direction)
            copied_sockets = getattr(dst, direction)
            for index, src_socket in enumerate(original_sockets):
                if index >= len(copied_sockets):
                    raise CompileError(f'Root transfer lost {direction} on {src.bl_idname}')
                _copy_socket_default(src_socket, copied_sockets[index])
    for src in stage.nodes:
        parent = src.parent
        dst = nodes[src.name]
        if dst is original_input:
            continue
        if parent is not None:
            mapped_parent = nodes.get(parent.name)
            if mapped_parent is None or mapped_parent.bl_idname != 'NodeFrame':
                raise CompileError('Root transfer lost a Frame parent')
            dst.parent = mapped_parent
        dst.location = tuple(src.location)
    # Validate dynamic items before wiring links; mapping nodes are checked after
    # linking because Blender may reset their mapping state during link creation.
    for src in stage.nodes:
        dst = nodes[src.name]
        if dst is not original_input and not hasattr(src, 'mapping'):
            _strict_node_parity(src, dst)
    source_links = []
    for link in stage.links:
        src_from, src_to = link.from_node, link.to_node
        dst_from, dst_to = nodes[src_from.name], nodes[src_to.name]
        def endpoint(src, socket, dst, *, output):
            if raw_nodes.is_raw_node(src):
                return raw_nodes.resolve_cutover_socket(src, socket, dst, direction='output' if output else 'input')
            if src.bl_idname in _STANDALONE_COLLECTION_NODES:
                return _resolve_standalone_copy_socket(src, socket, dst, output=output)
            original = src.outputs if output else src.inputs
            copied = dst.outputs if output else dst.inputs
            try:
                index = list(original).index(socket)
                result = copied[index]
            except (ValueError, IndexError) as exc:
                raise CompileError('Root transfer cannot resolve an exact linked socket') from exc
            if _socket_contract(src, outputs=output)[index] != _socket_contract(dst, outputs=output)[index]:
                raise CompileError('Root transfer linked socket identity differs from candidate')
            return result

        left = endpoint(src_from, link.from_socket, dst_from, output=True)
        right = endpoint(src_to, link.to_socket, dst_to, output=False)
        # Preserve Blender's multi-input order by copying input-link insertion order.
        source_links.append((left, right))
    for left, right in source_links:
        target.links.new(left, right)
    # Blender may reset mapping state while links are rebuilt. Restore it last.
    for src in stage.nodes:
        dst = nodes[src.name]
        if dst is not original_input and hasattr(src, 'mapping') and hasattr(dst, 'mapping'):
            _copy_curve_mapping(src.mapping, dst.mapping)
            _strict_node_parity(src, dst)
        if src.bl_idname in _STANDALONE_COLLECTION_NODES:
            _verify_standalone_copy(src, dst)
            if not raw_nodes.is_raw_node(src):
                _verify_standalone_defaults(src, dst)
        if raw_nodes.is_raw_node(src) and dst is not original_input:
            raw_nodes.validate_raw_node_after_cutover(dst, target)
    if _interface_signature(target) != interface_signature:
        raise CompileError('Root transfer modified an authoritative interface')
    for left, right in source_links:
        if not any(link.from_socket == left and link.to_socket == right for link in target.links):
            raise CompileError('Root transfer lost a private-candidate link')
    return RootSubgraphTransfer(tuple(created), new_input)


__all__ = ['RootSubgraphTransfer', 'transfer_private_root_subgraph']
