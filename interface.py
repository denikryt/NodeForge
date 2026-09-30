"""Group interface socket creation, defaults, and persistence helpers."""

INPUT_DEFAULTS_PROP = "gn_script_mvp_input_defaults"
INPUT_DECLARATIONS_PROP = "gn_script_mvp_input_declarations"
INPUT_DECLARATIONS_SCHEMA_VERSION = 1
from .errors import CompileError
from .blender_socket_types import socket_type_for_nf_type
from .compiler_identities import InputDeclarationId
from .nf_types import NFType, serialize_nf_type
from .values import make_value


def _json_safe_default(value):
    """Return an IDProperty-safe representation of one script default."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (tuple, list)):
        return [float(v) for v in value]
    return value


def _set_socket_default(socket, value):
    """Set a Blender socket/interface default robustly across socket classes."""
    if socket is None or value is None:
        return False
    try:
        if isinstance(value, (tuple, list)):
            try:
                socket.default_value = (float(value[0]), float(value[1]), float(value[2]))
            except Exception:
                socket.default_value[0] = float(value[0])
                socket.default_value[1] = float(value[1])
                socket.default_value[2] = float(value[2])
        elif isinstance(value, str):
            socket.default_value = value
        elif isinstance(value, bool):
            socket.default_value = bool(value)
        elif isinstance(value, int):
            socket.default_value = int(value)
        else:
            socket.default_value = float(value)
        return True
    except Exception:
        return False


def _interface_socket_type_name(item, typ: NFType | None = None) -> str:
    """Return the physical Blender socket-class token for an interface item."""
    for attr in ("bl_socket_idname", "socket_type"):
        value = getattr(item, attr, None)
        if isinstance(value, str) and value:
            return value
    if typ is not None:
        return socket_type_for_nf_type(typ)
    return ""


def _same_interface_item(candidate, item) -> bool:
    """Return whether two Blender RNA proxies identify the same interface item."""
    candidate_identifier = getattr(candidate, "identifier", None)
    item_identifier = getattr(item, "identifier", None)
    if candidate_identifier and item_identifier:
        return candidate_identifier == item_identifier
    try:
        return candidate.as_pointer() == item.as_pointer()
    except Exception:
        return candidate is item


def _interface_socket_key(group, item, typ: NFType | None = None):
    """Return ``(name, socket_type, occurrence)`` for one input interface item."""
    name = str(getattr(item, "name", ""))
    socket_type = _interface_socket_type_name(item, typ)
    occurrence = 0
    for candidate in getattr(group.interface, "items_tree", []):
        if getattr(candidate, "item_type", None) != "SOCKET" or getattr(candidate, "in_out", None) != "INPUT":
            continue
        same_item = _same_interface_item(candidate, item)
        candidate_name = str(getattr(candidate, "name", ""))
        candidate_type = _interface_socket_type_name(candidate, typ if same_item else None)
        if same_item:
            return (name, socket_type, occurrence)
        if candidate_name == name and candidate_type == socket_type:
            occurrence += 1
    raise CompileError(f'Internal error: input interface item {name!r} is not attached to the group')


def _set_interface_socket_default(group, name, in_out, value):
    """Apply a default to every matching legacy interface item by display name."""
    if value is None:
        return False
    ok = False
    try:
        for item in group.interface.items_tree:
            if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "name", None) == name and getattr(item, "in_out", None) == in_out:
                ok = _set_socket_default(item, value) or ok
    except Exception:
        pass
    return ok


def _record_group_input_default(group, name, typ: NFType, default, *, interface_item=None):
    """Persist one exact input default using occurrence-aware physical identity."""
    if not isinstance(typ, NFType):
        raise TypeError("typ must be an NFType")
    if default is None:
        return
    if interface_item is not None:
        key = _interface_socket_key(group, interface_item, typ)
    else:
        interface = getattr(group, "interface", None)
        matching = [
            item
            for item in getattr(interface, "items_tree", [])
            if getattr(item, "item_type", None) == "SOCKET"
            and getattr(item, "in_out", None) == "INPUT"
            and getattr(item, "name", None) == name
            and _interface_socket_type_name(item, typ) == socket_type_for_nf_type(typ)
        ]
        if not matching:
            # Unit tests use lightweight stand-ins without a Blender interface.
            key = (name, socket_type_for_nf_type(typ), 0)
        else:
            key = _interface_socket_key(group, matching[-1], typ)
    try:
        existing = _get_group_input_defaults(group)
    except Exception:
        existing = {}
    records = []
    for (entry_name, socket_type, occurrence), entry in sorted(existing.items(), key=lambda item: item[0][2]):
        if (entry_name, socket_type, occurrence) == key:
            continue
        records.append({
            "name": entry_name,
            "nf_type": entry.get("nf_type") or entry.get("type"),
            "socket_type": socket_type,
            "occurrence": occurrence,
            "default": _json_safe_default(entry.get("default")),
        })
    records.append({
        "name": key[0],
        "nf_type": serialize_nf_type(typ),
        "socket_type": key[1],
        "occurrence": key[2],
        "default": _json_safe_default(default),
    })
    data = {str(index): record for index, record in enumerate(records)}
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
        if not hasattr(value, "items"):
            return [_idprop_to_plain(v) for v in value]
    except Exception:
        pass
    try:
        return {str(k): _idprop_to_plain(v) for k, v in value.items()}
    except Exception:
        return value


def _serialize_input_declaration_id(declaration_id: InputDeclarationId) -> dict:
    """Return one deterministic IDProperty-safe declaration identity record."""
    if not isinstance(declaration_id, InputDeclarationId):
        raise TypeError("declaration_id must be an InputDeclarationId")
    return {
        "owner_scope": declaration_id.owner_scope,
        "target_name": declaration_id.target_name,
        "declaration_ordinal": declaration_id.declaration_ordinal,
    }


def _deserialize_input_declaration_id(value) -> InputDeclarationId:
    """Parse one persisted declaration identity or raise a controlled metadata error."""
    value = _idprop_to_plain(value)
    if not isinstance(value, dict):
        raise CompileError("Malformed NodeForge input declaration identity metadata")
    try:
        return InputDeclarationId(
            str(value["owner_scope"]),
            str(value["target_name"]),
            int(value["declaration_ordinal"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CompileError("Malformed NodeForge input declaration identity metadata") from exc


def _write_group_input_declarations(group, records) -> None:
    """Persist deterministic versioned explicit-input declaration metadata."""
    ordered = sorted(records, key=lambda record: record["declaration_id"].stable_key())
    payload = {
        "schema_version": INPUT_DECLARATIONS_SCHEMA_VERSION,
        "records": {
            str(index): {
                "declaration_id": _serialize_input_declaration_id(record["declaration_id"]),
                "display_name": record["display_name"],
                "nf_type": record["nf_type"],
                "socket_type": record["socket_type"],
                "occurrence": int(record["occurrence"]),
                "has_default": bool(record["has_default"]),
                "default": _json_safe_default(record.get("default")),
            }
            for index, record in enumerate(ordered)
        },
    }
    try:
        group[INPUT_DECLARATIONS_PROP] = payload
    except Exception as exc:
        raise CompileError("Failed to persist NodeForge input declaration metadata") from exc


def _get_group_input_declarations(group):
    """Return validated declaration records keyed by stable declaration identity."""
    if group is None:
        return {}
    try:
        raw = group.get(INPUT_DECLARATIONS_PROP, None)
    except Exception:
        raw = None
    if raw is None:
        return {}
    data = _idprop_to_plain(raw)
    if not isinstance(data, dict):
        raise CompileError("Malformed NodeForge input declaration metadata")
    try:
        schema_version = int(data["schema_version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CompileError("Malformed NodeForge input declaration metadata") from exc
    if schema_version != INPUT_DECLARATIONS_SCHEMA_VERSION:
        raise CompileError(f"Unsupported NodeForge input declaration metadata schema: {schema_version}")
    records_data = data.get("records")
    if not isinstance(records_data, dict):
        raise CompileError("Malformed NodeForge input declaration metadata")

    result = {}
    physical_keys = set()
    for raw_record in records_data.values():
        if not isinstance(raw_record, dict):
            raise CompileError("Malformed NodeForge input declaration metadata")
        declaration_id = _deserialize_input_declaration_id(raw_record.get("declaration_id"))
        stable_key = declaration_id.stable_key()
        if stable_key in result:
            raise CompileError("Duplicate NodeForge InputDeclarationId metadata")
        try:
            display_name = str(raw_record["display_name"])
            nf_type = str(raw_record["nf_type"])
            socket_type = str(raw_record["socket_type"])
            occurrence = int(raw_record["occurrence"])
            has_default = bool(raw_record["has_default"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CompileError("Malformed NodeForge input declaration metadata") from exc
        if not display_name or not socket_type or occurrence < 0:
            raise CompileError("Malformed NodeForge input declaration metadata")
        physical_key = (display_name, socket_type, occurrence)
        if physical_key in physical_keys:
            raise CompileError("Duplicate physical input locator in NodeForge declaration metadata")
        physical_keys.add(physical_key)
        result[stable_key] = {
            "declaration_id": declaration_id,
            "display_name": display_name,
            "nf_type": nf_type,
            "socket_type": socket_type,
            "occurrence": occurrence,
            "has_default": has_default,
            "default": raw_record.get("default"),
        }
    return result


def _record_group_input_declaration(
    group,
    declaration_id: InputDeclarationId,
    display_name: str,
    typ: NFType,
    default,
    *,
    interface_item,
) -> None:
    """Persist one explicit input's durable identity and current physical locator."""
    if not isinstance(declaration_id, InputDeclarationId):
        raise TypeError("declaration_id must be an InputDeclarationId")
    if not isinstance(typ, NFType):
        raise TypeError("typ must be an NFType")
    key = _interface_socket_key(group, interface_item, typ)
    existing = list(_get_group_input_declarations(group).values())
    stable_key = declaration_id.stable_key()
    if any(record["declaration_id"].stable_key() == stable_key for record in existing):
        raise CompileError("Duplicate NodeForge InputDeclarationId metadata")
    existing.append({
        "declaration_id": declaration_id,
        "display_name": key[0],
        "nf_type": serialize_nf_type(typ),
        "socket_type": key[1],
        "occurrence": key[2],
        "has_default": default is not None,
        "default": default,
    })
    _write_group_input_declarations(group, existing)


def _legacy_socket_type(token: str) -> str:
    """Map one pre-0.51 NF type token to its physical socket class."""
    try:
        typ = NFType(str(token))
    except Exception:
        typ = NFType.FLOAT
    return socket_type_for_nf_type(typ)


def _get_group_input_defaults(group):
    """Return merged implicit and explicit defaults keyed by physical socket identity."""
    if group is None:
        return {}
    try:
        data = group.get(INPUT_DEFAULTS_PROP, {})
    except Exception:
        data = {}
    data = _idprop_to_plain(data)
    if not isinstance(data, dict):
        data = {}

    normalized = {}
    new_schema = bool(data) and all(
        isinstance(entry, dict) and {"name", "socket_type", "occurrence"}.issubset(entry)
        for entry in data.values()
    )
    if new_schema:
        for entry in data.values():
            key = (str(entry["name"]), str(entry["socket_type"]), int(entry["occurrence"]))
            normalized[key] = dict(entry)
    else:
        # INPUT_DECLARATION_METADATA_LEGACY_COMPAT: Groups created before durable input-declaration
        # metadata may expose only name/type/occurrence defaults. Keep that data available for
        # conservative update migration, but never treat occurrence as cross-version logical identity.
        # Remove this reader when support for groups without versioned InputDeclarationId metadata is
        # intentionally dropped or all supported groups are guaranteed to have been rewritten.
        for display_name, entry in data.items():
            if not isinstance(entry, dict) or "default" not in entry:
                continue
            token = entry.get("nf_type") or entry.get("type") or "FLOAT"
            socket_type = _legacy_socket_type(token)
            normalized[(str(display_name), socket_type, 0)] = {
                "name": str(display_name),
                "nf_type": str(token),
                "socket_type": socket_type,
                "occurrence": 0,
                "default": entry.get("default"),
            }

    # Explicit declarations and ordinary implicit inputs are persisted separately. Merge both
    # stores into one physical-key view so update can compare every socket against its script
    # default. Declaration metadata is authoritative for any exact physical-key collision.
    declarations = _get_group_input_declarations(group)
    for record in declarations.values():
        key = (record["display_name"], record["socket_type"], record["occurrence"])
        normalized[key] = {
            "name": record["display_name"],
            "nf_type": record["nf_type"],
            "socket_type": record["socket_type"],
            "occurrence": record["occurrence"],
            "default": record["default"],
            "has_default": record["has_default"],
            "declaration_id": record["declaration_id"],
        }
    return normalized

def _runtime_output_for_interface_item(group_input, group, interface_item):
    """Resolve the Group Input output for one newly created interface item without name matching."""
    identifier = getattr(interface_item, "identifier", None)
    if identifier:
        for socket in getattr(group_input, "outputs", []):
            if getattr(socket, "identifier", None) == identifier:
                return socket

    input_items = [
        item
        for item in getattr(group.interface, "items_tree", [])
        if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "in_out", None) == "INPUT"
    ]
    try:
        index = next(i for i, item in enumerate(input_items) if _same_interface_item(item, interface_item))
    except StopIteration as exc:
        raise CompileError("Internal error: newly created input interface item is missing") from exc
    outputs = list(getattr(group_input, "outputs", []))
    if 0 <= index < len(outputs):
        return outputs[index]
    raise CompileError("Internal error: newly created input socket has no Group Input output")


def _create_group_input_socket(
    group,
    group_input,
    display_name: str,
    typ: NFType,
    default,
    *,
    declaration_id: InputDeclarationId | None = None,
):
    """Create one physical input socket and return its interface item and runtime output."""
    if not isinstance(display_name, str) or not display_name:
        raise CompileError("Input display name must be a non-empty string")
    if not isinstance(typ, NFType):
        raise TypeError("typ must be an NFType")
    socket_type = socket_type_for_nf_type(typ)
    iface = group.interface.new_socket(name=display_name, in_out="INPUT", socket_type=socket_type)
    if default is not None:
        _set_socket_default(iface, default)
    if declaration_id is not None:
        _record_group_input_declaration(
            group,
            declaration_id,
            display_name,
            typ,
            default,
            interface_item=iface,
        )
    elif default is not None:
        _record_group_input_default(group, display_name, typ, default, interface_item=iface)
    socket = _runtime_output_for_interface_item(group_input, group, iface)
    return make_value(socket, typ), iface


def _is_root_interface_item(item):
    """Return whether a Blender interface item is attached to the implicit root panel."""
    parent = getattr(item, "parent", None)
    if parent is None:
        return True
    return getattr(parent, "name", "") == ""


def interface_item_for_group_input_value(group, group_input, value):
    """Resolve a backend Value only when it is this group's exact Group Input output."""
    from .values import Value

    if not isinstance(value, Value):
        return None
    socket = getattr(value, "socket", None)
    if socket is None:
        return None
    socket_node = getattr(socket, "node", None)
    if socket_node is None:
        return None

    # Blender RNA wrappers are not required to preserve Python object identity.  Compare the
    # underlying RNA pointer when available, and fall back to object identity for test doubles.
    def _same_rna_object(left, right):
        if left is right:
            return True
        left_pointer = getattr(left, "as_pointer", None)
        right_pointer = getattr(right, "as_pointer", None)
        if callable(left_pointer) and callable(right_pointer):
            try:
                return left_pointer() == right_pointer()
            except (ReferenceError, RuntimeError):
                return False
        return False

    if not _same_rna_object(socket_node, group_input):
        return None
    identifier = getattr(socket, "identifier", None)
    if identifier:
        for item in getattr(group.interface, "items_tree", []):
            if (
                getattr(item, "item_type", None) == "SOCKET"
                and getattr(item, "in_out", None) == "INPUT"
                and getattr(item, "identifier", None) == identifier
            ):
                return item
    matches = [
        item
        for item in getattr(group.interface, "items_tree", [])
        if getattr(item, "item_type", None) == "SOCKET"
        and getattr(item, "in_out", None) == "INPUT"
        and getattr(item, "name", None) == getattr(socket, "name", None)
    ]
    return matches[0] if len(matches) == 1 else None


def _create_interface_panel(group, sockets, name, *, collapsed):
    """Create one root native interface panel and move validated input sockets into it."""
    sockets = list(sockets)
    if not sockets:
        raise CompileError("panel() requires at least one input")
    if not isinstance(name, str) or not name:
        raise CompileError("panel() name must be a non-empty string")

    for item in getattr(group.interface, "items_tree", []):
        if (
            getattr(item, "item_type", None) == "PANEL"
            and _is_root_interface_item(item)
            and getattr(item, "name", None) == name
        ):
            raise CompileError(f'panel() duplicate panel name: {name}')

    for socket in sockets:
        if getattr(socket, "item_type", None) != "SOCKET" or getattr(socket, "in_out", None) != "INPUT":
            raise CompileError("panel() can contain only group input sockets")
        if not _is_root_interface_item(socket):
            raise CompileError(f'panel() input {getattr(socket, "name", "<unnamed>")} already belongs to a panel')

    panel = group.interface.new_panel(name=name, description="", default_closed=bool(collapsed))
    for index, socket in enumerate(sockets):
        group.interface.move_to_parent(socket, panel, index)
    return panel


__all__ = [
    "_json_safe_default",
    "_set_socket_default",
    "_set_interface_socket_default",
    "_record_group_input_default",
    "_record_group_input_declaration",
    "_idprop_to_plain",
    "_get_group_input_defaults",
    "_get_group_input_declarations",
    "_create_group_input_socket",
    "_interface_socket_key",
    "_create_interface_panel",
    "interface_item_for_group_input_value",
]
