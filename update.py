"""Helpers for preserving Group-node links and visible input values during updates."""

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

def _capture_node_external_state(tree, node):
    """Capture links and only real user-overridden input values before recompiling.

    If a visible Group-node value is still equal to the previous script default,
    it is not preserved; after update it should receive the new script default.
    If it differs, preserve it as a user override.
    """
    state = {"input_defaults": {}, "incoming": [], "outgoing": []}
    old_defaults = _get_group_input_defaults(getattr(node, "node_tree", None))
    for sock in getattr(node, "inputs", []):
        val = _copy_socket_default_value(sock)
        if val is None:
            continue
        entry = old_defaults.get(sock.name) if isinstance(old_defaults, dict) else None
        if isinstance(entry, dict) and "default" in entry:
            # Not a user override: let the newly compiled default show in UI.
            if _defaults_equal(val, entry.get("default")):
                continue
            state["input_defaults"][sock.name] = val
        else:
            # Old files may lack stored defaults. Avoid preserving Blender's
            # implicit zero values over newly declared script defaults.
            if not _is_zero_like_default(val):
                state["input_defaults"][sock.name] = val
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

__all__ = ['_apply_group_defaults_to_node', '_copy_socket_default_value', '_defaults_equal', '_is_zero_like_default', '_capture_node_external_state', '_find_socket_by_name', '_restore_node_external_state']
