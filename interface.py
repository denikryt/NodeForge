"""Group interface socket defaults and default-value persistence helpers."""

from .storage import INPUT_DEFAULTS_PROP



def _json_safe_default(value):
    """Function `_json_safe_default` used by the NodeForge addon."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, (tuple, list)):
        return [float(v) for v in value]
    return value

def _set_socket_default(socket, value):
    """Set a Blender socket/interface default robustly across socket classes."""
    if socket is None or value is None:
        return False
    try:
        if isinstance(value, (tuple, list)):
            # Vector sockets usually expose a mutable default_value sequence.
            try:
                socket.default_value = (float(value[0]), float(value[1]), float(value[2]))
            except Exception:
                socket.default_value[0] = float(value[0])
                socket.default_value[1] = float(value[1])
                socket.default_value[2] = float(value[2])
        elif isinstance(value, bool):
            socket.default_value = bool(value)
        elif isinstance(value, int):
            socket.default_value = int(value)
        else:
            socket.default_value = float(value)
        return True
    except Exception:
        return False

def _set_interface_socket_default(group, name, in_out, value):
    """Function `_set_interface_socket_default` used by the NodeForge addon."""
    if value is None:
        return False
    ok = False
    try:
        for item in group.interface.items_tree:
            if getattr(item, "item_type", None) == 'SOCKET' and getattr(item, "name", None) == name and getattr(item, "in_out", None) == in_out:
                ok = _set_socket_default(item, value) or ok
    except Exception:
        pass
    return ok

def _record_group_input_default(group, name, typ, default):
    """Function `_record_group_input_default` used by the NodeForge addon."""
    if default is None:
        return
    try:
        data = dict(group.get(INPUT_DEFAULTS_PROP, {}))
    except Exception:
        data = {}
    data[name] = {"type": typ, "default": _json_safe_default(default)}
    try:
        group[INPUT_DEFAULTS_PROP] = data
    except Exception:
        pass

def _idprop_to_plain(value):
    """Convert Blender IDProperty groups/arrays into ordinary Python values."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (tuple, list)):
        return [_idprop_to_plain(v) for v in value]
    try:
        # IDPropertyArray and bpy_prop_array are iterable but not dict-like.
        if not hasattr(value, "items"):
            return [_idprop_to_plain(v) for v in value]
    except Exception:
        pass
    try:
        return {str(k): _idprop_to_plain(v) for k, v in value.items()}
    except Exception:
        return value

def _get_group_input_defaults(group):
    """Function `_get_group_input_defaults` used by the NodeForge addon."""
    if group is None:
        return {}
    try:
        data = group.get(INPUT_DEFAULTS_PROP, {})
    except Exception:
        data = {}
    data = _idprop_to_plain(data)
    return data if isinstance(data, dict) else {}

__all__ = ['_json_safe_default', '_set_socket_default', '_set_interface_socket_default', '_record_group_input_default', '_idprop_to_plain', '_get_group_input_defaults']
