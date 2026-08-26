"""Helpers for preserving Group-node links and visible input values during updates."""

import bpy

from .errors import CompileError
from .interface import _get_group_input_defaults, _set_socket_default



def _apply_group_defaults_to_node(node, *, preserve_existing=None):
    """Apply stored script defaults to a GeometryNodeGroup instance.

    preserve_existing is an optional dict {socket_name: value}; those values win
    when the socket still exists after update, so user-entered values survive.
    """
    group = getattr(node, "node_tree", None)
    if group is None:
        return
    defaults = _get_group_input_defaults(group)
    preserve_existing = preserve_existing or {}
    for sock in getattr(node, "inputs", []):
        if not hasattr(sock, "default_value"):
            continue
        if sock.name in preserve_existing:
            _set_socket_default(sock, preserve_existing[sock.name])
            continue
        entry = defaults.get(sock.name)
        if isinstance(entry, dict) and "default" in entry:
            _set_socket_default(sock, entry.get("default"))


def _socket_type_name(socket):
    """Return the Blender socket class name used for transient update matching."""
    return str(getattr(socket, "bl_idname", None) or getattr(socket, "bl_socket_idname", None) or "")


def _socket_keys(sockets, direction):
    """Return ordered ``(socket, key)`` pairs for one node socket collection."""
    counts = {}
    result = []
    for socket in sockets:
        signature = (str(direction), str(getattr(socket, "name", "")), _socket_type_name(socket))
        ordinal = counts.get(signature, 0)
        counts[signature] = ordinal + 1
        result.append((socket, signature + (ordinal,)))
    return result


def _resolve_socket(node, direction, key):
    """Resolve a current group-node socket from a pre-cutover structural key."""
    sockets = node.inputs if direction == "INPUT" else node.outputs
    for socket, candidate in _socket_keys(sockets, direction):
        if candidate == key:
            return socket
    return None


def _copy_socket_default_value(socket):
    """Function `_copy_socket_default_value` used by the NodeForge addon."""
    if socket is None or not hasattr(socket, "default_value"):
        return None
    try:
        val = socket.default_value
        if isinstance(val, (float, int, bool)):
            return val
        try:
            return tuple(float(v) for v in val)
        except Exception:
            return val
    except Exception:
        return None


def _set_captured_socket_default(socket, value):
    """Restore a captured socket value, including Blender ID pointer values."""
    if socket is None or not hasattr(socket, "default_value"):
        return False
    try:
        socket.default_value = value
        return True
    except Exception:
        return _set_socket_default(socket, value)


def _defaults_equal(a, b, eps=1e-6):
    """Function `_defaults_equal` used by the NodeForge addon."""
    if a is None or b is None:
        return False
    if isinstance(a, (tuple, list)) or isinstance(b, (tuple, list)):
        try:
            if len(a) != len(b):
                return False
            return all(abs(float(x) - float(y)) <= eps for x, y in zip(a, b))
        except Exception:
            return False
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    try:
        return abs(float(a) - float(b)) <= eps
    except Exception:
        return a == b


def _is_zero_like_default(value):
    """Function `_is_zero_like_default` used by the NodeForge addon."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value is False
    if isinstance(value, (tuple, list)):
        try:
            return all(abs(float(v)) <= 1e-6 for v in value)
        except Exception:
            return False
    try:
        return abs(float(value)) <= 1e-6
    except Exception:
        return False


def _captured_node_input_overrides(node):
    """Yield supported input sockets whose values are real user overrides."""
    old_defaults = _get_group_input_defaults(getattr(node, "node_tree", None))
    for socket in getattr(node, "inputs", []):
        value = _copy_socket_default_value(socket)
        if value is None:
            continue
        entry = old_defaults.get(socket.name) if isinstance(old_defaults, dict) else None
        if isinstance(entry, dict) and "default" in entry:
            if _defaults_equal(value, entry.get("default")):
                continue
            yield socket, value
        elif not _is_zero_like_default(value):
            # Preserve legacy behavior for groups compiled before defaults were stored.
            yield socket, value


def _capture_node_external_state(tree, node):
    """Capture links and only real user-overridden input values before recompiling.

    If a visible Group-node value is still equal to the previous script default,
    it is not preserved; after update it should receive the new script default.
    If it differs, preserve it as a user override.
    """
    state = {"input_defaults": {}, "incoming": [], "outgoing": []}
    for socket, value in _captured_node_input_overrides(node):
        state["input_defaults"][socket.name] = value
    for link in list(getattr(tree, "links", [])):
        if link.to_node == node:
            state["incoming"].append({"to_name": link.to_socket.name, "from_socket": link.from_socket})
        elif link.from_node == node:
            state["outgoing"].append({"from_name": link.from_socket.name, "to_socket": link.to_socket})
    return state

def _find_socket_by_name(sockets, name):
    """Function `_find_socket_by_name` used by the NodeForge addon."""
    for sock in sockets:
        if sock.name == name:
            return sock
    return None

def _restore_node_external_state(tree, node, state):
    """Function `_restore_node_external_state` used by the NodeForge addon."""
    _apply_group_defaults_to_node(node, preserve_existing=state.get("input_defaults", {}))
    restored = 0
    for item in state.get("incoming", []):
        to_socket = _find_socket_by_name(node.inputs, item.get("to_name"))
        from_socket = item.get("from_socket")
        if to_socket is None or from_socket is None:
            continue
        try:
            if not any(l.from_socket == from_socket and l.to_socket == to_socket for l in tree.links):
                tree.links.new(from_socket, to_socket)
                restored += 1
        except Exception:
            pass
    for item in state.get("outgoing", []):
        from_socket = _find_socket_by_name(node.outputs, item.get("from_name"))
        to_socket = item.get("to_socket")
        if from_socket is None or to_socket is None:
            continue
        try:
            if not any(l.from_socket == from_socket and l.to_socket == to_socket for l in tree.links):
                tree.links.new(from_socket, to_socket)
                restored += 1
        except Exception:
            pass
    return restored


def _find_group_node_users(group):
    """Return every GeometryNodeGroup instance that references ``group``."""
    users = []
    for tree in bpy.data.node_groups:
        if getattr(tree, "bl_idname", None) != "GeometryNodeTree":
            continue
        for node in getattr(tree, "nodes", []):
            if getattr(node, "bl_idname", None) == "GeometryNodeGroup" and getattr(node, "node_tree", None) == group:
                users.append((tree, node))
    return users


def _affected_endpoint(node, socket, direction):
    """Describe one endpoint whose group-node socket will be recreated."""
    sockets = node.inputs if direction == "INPUT" else node.outputs
    for candidate, key in _socket_keys(sockets, direction):
        if candidate == socket:
            return {
                "kind": "group_node_socket",
                "node": node,
                "direction": direction,
                "socket_key": key,
            }
    raise CompileError(f"Failed to identify {direction.lower()} socket {getattr(socket, 'name', '<unnamed>')!r}")


def _capture_group_external_state(group):
    """Capture supported values and links for every group-node user of ``group``."""
    users = _find_group_node_users(group)
    affected_by_tree = {}
    group_nodes = []
    for tree, node in users:
        affected_by_tree.setdefault(tree, set()).add(node)
        overrides = {}
        input_keys = dict((socket.as_pointer(), key) for socket, key in _socket_keys(node.inputs, "INPUT"))
        for socket, value in _captured_node_input_overrides(node):
            key = input_keys.get(socket.as_pointer())
            if key is None:
                raise CompileError(f"Failed to identify input socket {socket.name!r} on group node {node.name!r}")
            overrides[key] = value
        group_nodes.append({"tree": tree, "node": node, "input_overrides": overrides})

    links = []
    for tree, affected_nodes in affected_by_tree.items():
        for link in list(tree.links):
            if link.from_node not in affected_nodes and link.to_node not in affected_nodes:
                continue
            from_endpoint = (
                _affected_endpoint(link.from_node, link.from_socket, "OUTPUT")
                if link.from_node in affected_nodes
                else {"kind": "stable_socket", "socket": link.from_socket}
            )
            to_endpoint = (
                _affected_endpoint(link.to_node, link.to_socket, "INPUT")
                if link.to_node in affected_nodes
                else {"kind": "stable_socket", "socket": link.to_socket}
            )
            links.append({"tree": tree, "from": from_endpoint, "to": to_endpoint})
    return {"group_nodes": group_nodes, "links": links}


def _resolve_endpoint(endpoint, *, strict):
    """Resolve a captured link endpoint under success or strict rollback policy."""
    if endpoint.get("kind") == "stable_socket":
        return endpoint.get("socket")
    node = endpoint.get("node")
    direction = endpoint.get("direction")
    socket = _resolve_socket(node, direction, endpoint.get("socket_key"))
    if socket is None and strict:
        raise CompileError(
            f"Rollback could not resolve {direction.lower()} socket on group node {getattr(node, 'name', '<unnamed>')!r}"
        )
    return socket


def _restore_group_external_state(group, state, *, strict=False):
    """Restore captured group-node state after cutover or strict rollback."""
    for item in state.get("group_nodes", []):
        node = item.get("node")
        if node is None or getattr(node, "node_tree", None) != group:
            if strict:
                raise CompileError("Rollback lost a captured GeometryNodeGroup instance")
            continue
        _apply_group_defaults_to_node(node)
        for key, value in item.get("input_overrides", {}).items():
            socket = _resolve_socket(node, "INPUT", key)
            if socket is None:
                if strict:
                    raise CompileError(f"Rollback could not resolve captured input on group node {node.name!r}")
                continue
            if not _set_captured_socket_default(socket, value):
                raise CompileError(f"Failed to restore input {socket.name!r} on group node {node.name!r}")

    restored = 0
    for item in state.get("links", []):
        tree = item.get("tree")
        from_socket = _resolve_endpoint(item.get("from", {}), strict=strict)
        to_socket = _resolve_endpoint(item.get("to", {}), strict=strict)
        if from_socket is None or to_socket is None:
            continue
        try:
            exists = any(link.from_socket == from_socket and link.to_socket == to_socket for link in tree.links)
            if not exists:
                tree.links.new(from_socket, to_socket)
                restored += 1
        except Exception as exc:
            raise CompileError("Failed to restore a compatible external group-node link") from exc
    return restored

__all__ = [
    '_apply_group_defaults_to_node', '_copy_socket_default_value', '_defaults_equal',
    '_is_zero_like_default', '_capture_node_external_state', '_find_socket_by_name',
    '_restore_node_external_state', '_find_group_node_users', '_capture_group_external_state',
    '_restore_group_external_state',
]
