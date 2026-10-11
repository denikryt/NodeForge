"""Controlled raw Blender node construction for the NodeForge DSL."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..semantic.constants import (
    TYPE_BOOL,
    TYPE_BUNDLE,
    TYPE_FLOAT,
    TYPE_GEOMETRY,
    TYPE_INT,
    TYPE_MATERIAL,
    TYPE_OBJECT,
    TYPE_ROTATION,
    TYPE_STRING,
    TYPE_VECTOR,
)
from ..errors import CompileError
from .socket_types import runtime_socket_nf_type
from .nodes import _new_node
from .values import Value, make_value


RAW_NODE_PROP = "nodeforge_raw_node"
RAW_BL_IDNAME_PROP = "nodeforge_raw_bl_idname"
RAW_PROPS_JSON_PROP = "nodeforge_raw_props_json"
RAW_INPUTS_JSON_PROP = "nodeforge_raw_inputs_json"
RAW_OUTPUTS_JSON_PROP = "nodeforge_raw_output_sockets_json"
RAW_MODE_PROP = "nodeforge_raw_mode"
RAW_SCHEMA_VERSION_PROP = "nodeforge_raw_schema_version"
RAW_SCHEMA_VERSION = 2
RAW_SINGLE_MODE = "single_output"
RAW_MULTI_MODE = "multi_output"
INPUT_LITERAL = "literal_default"
INPUT_SINGLE_LINK = "single_link"
INPUT_MULTI_LINK = "multi_link"

_SUPPORTED_TYPES = {
    TYPE_FLOAT,
    TYPE_INT,
    TYPE_BOOL,
    TYPE_VECTOR,
    TYPE_GEOMETRY,
    TYPE_MATERIAL,
    TYPE_OBJECT,
    TYPE_STRING,
    TYPE_BUNDLE,
    TYPE_ROTATION,
}
_INPUT_MODES = {INPUT_LITERAL, INPUT_SINGLE_LINK, INPUT_MULTI_LINK}


@dataclass(frozen=True)
class RawPhysicalSocketRef:
    """Persist one durable Blender raw-socket identity for schema v2.

    ``identifier`` is Blender's non-empty mapping identity. ``socket_type`` is
    retained only as a fail-closed consistency assertion during reconstruction.
    Identifier-less declared sockets are intentionally unsupported by schema v2.
    """

    identifier: str
    socket_type: str

    def __post_init__(self) -> None:
        """Require the complete schema-v2 durable identity contract."""
        if type(self.identifier) is not str or not self.identifier:
            raise ValueError("raw physical socket identifier must be a non-empty string")
        if type(self.socket_type) is not str or not self.socket_type:
            raise ValueError("raw physical socket type must be a non-empty string")


def build_materialized_raw_node(
    group,
    *,
    bl_idname,
    props,
    inputs,
    output,
    typ,
    outputs,
    x=0,
    y=0,
    context="raw node",
):
    """Build a raw Blender node after full selector/contract preflight.

    ``inputs`` is an ordered iterable of ``(selector, value_spec)`` pairs.
    ``outputs`` is ``None`` for single-output mode or an ordered iterable of
    ``(alias, selector, NFType)`` triples. Source selectors never become
    persisted physical identity.
    """
    if not isinstance(bl_idname, str) or not bl_idname:
        raise CompileError(f"{context}: bl_idname must be a non-empty string")
    if not isinstance(props, dict):
        raise CompileError(f"{context}: props must be normalized mapping data")
    if not isinstance(inputs, (list, tuple)):
        raise CompileError(f"{context}: inputs must be normalized ordered selector data")
    _validate_public_type(typ, f"{context}: typ") if typ is not None else None
    if outputs is not None:
        if not isinstance(outputs, (list, tuple)) or not outputs:
            raise CompileError(f"{context}: outputs cannot be empty")
        for item in outputs:
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise CompileError(f"{context}: outputs must contain (alias, selector, type) triples")
            alias, _selector, out_typ = item
            if not isinstance(alias, str) or not alias:
                raise CompileError(f"{context}: output aliases must be non-empty strings")
            _validate_public_type(out_typ, f"{context}: output type")
    elif output is None or typ is None:
        raise CompileError(f"{context}: raw node requires single-output or multi-output declaration")

    try:
        node = _new_node(group, bl_idname, x, y)
    except Exception as exc:
        raise CompileError(f"{context}: could not create Blender node {bl_idname!r}: {exc}") from exc

    for prop_name, prop_value in props.items():
        _set_node_property(node, prop_name, prop_value, context)

    # Phase B: resolve every declared socket and validate every contract before
    # any defaults or links are applied to the graph.
    prepared_inputs = []
    resolved_input_sockets = []
    for selector, value_spec in inputs:
        selector_label = _selector_label(selector)
        socket = resolve_socket(node.inputs, selector, direction="input", context=context)
        socket_ref = _capture_physical_socket_ref(
            socket,
            direction="input",
            context=f"{context}: input {selector_label}",
        )
        _reject_duplicate_physical_socket(
            resolved_input_sockets,
            socket,
            context=f"{context}: two input selectors resolve to the same physical socket",
        )
        resolved_input_sockets.append(socket)

        if isinstance(value_spec, list):
            if not value_spec:
                raise CompileError(f"{context}: multi-input {selector_label} items cannot be empty")
            _require_multi_input(socket, context, selector_label)
            for value in value_spec:
                _validate_materialized_value(value, socket, context, selector_label)
            prepared_inputs.append((socket, socket_ref, INPUT_MULTI_LINK, tuple(value_spec)))
        elif isinstance(value_spec, Value):
            _validate_materialized_value(value_spec, socket, context, selector_label)
            prepared_inputs.append((socket, socket_ref, INPUT_SINGLE_LINK, value_spec))
        else:
            normalized = _normalize_input_literal(value_spec, context, selector_label)
            if not hasattr(socket, "default_value"):
                raise CompileError(f"{context}: input {selector_label!r} does not accept literal defaults")
            prepared_inputs.append((socket, socket_ref, INPUT_LITERAL, normalized))

    prepared_outputs = []
    resolved_output_sockets = []
    if outputs is not None:
        for alias, selector, out_typ in outputs:
            selector_label = _selector_label(selector)
            socket = resolve_socket(node.outputs, selector, direction="output", context=context)
            _validate_runtime_socket_type(
                socket,
                out_typ,
                direction="output",
                context=context,
                socket_name=selector_label,
            )
            socket_ref = _capture_physical_socket_ref(
                socket,
                direction="output",
                context=f"{context}: output {alias!r}",
            )
            _reject_duplicate_physical_socket(
                resolved_output_sockets,
                socket,
                context=f"{context}: two output selectors resolve to the same physical socket",
            )
            resolved_output_sockets.append(socket)
            prepared_outputs.append((alias, socket, socket_ref, out_typ))
    else:
        selector_label = _selector_label(output)
        socket = resolve_socket(node.outputs, output, direction="output", context=context)
        _validate_runtime_socket_type(
            socket,
            typ,
            direction="output",
            context=context,
            socket_name=selector_label,
        )
        socket_ref = _capture_physical_socket_ref(
            socket,
            direction="output",
            context=f"{context}: output {selector_label}",
        )
        prepared_outputs.append((None, socket, socket_ref, typ))

    # Phase C: all semantic/socket contracts are known valid. Blender mutation
    # may still fail physically; the existing generated-group transaction owns
    # rollback for such failures.
    input_contracts = []
    for socket, socket_ref, mode, payload in prepared_inputs:
        if mode == INPUT_MULTI_LINK:
            for value in payload:
                _link_materialized_value(group, value, socket, context, socket_ref.identifier)
            input_contracts.append(
                {"socket": _serialize_physical_socket_ref(socket_ref), "mode": mode, "links": len(payload)}
            )
        elif mode == INPUT_SINGLE_LINK:
            _link_materialized_value(group, payload, socket, context, socket_ref.identifier)
            input_contracts.append(
                {"socket": _serialize_physical_socket_ref(socket_ref), "mode": mode, "links": 1}
            )
        else:
            normalized = _assign_literal_default(socket, payload, context, socket_ref.identifier)
            input_contracts.append(
                {"socket": _serialize_physical_socket_ref(socket_ref), "mode": mode, "default": normalized}
            )

    output_refs = tuple(item[2] for item in prepared_outputs)
    mode = RAW_MULTI_MODE if outputs is not None else RAW_SINGLE_MODE
    _write_raw_metadata(node, bl_idname, props, input_contracts, output_refs, mode)

    if outputs is not None:
        return tuple(make_value(socket, out_typ) for _alias, socket, _ref, out_typ in prepared_outputs)
    _alias, socket, _ref, out_typ = prepared_outputs[0]
    return make_value(socket, out_typ)


def _selector_label(selector) -> str:
    """Return a user-facing diagnostic label for one normalized source selector."""
    if isinstance(selector, str):
        return repr(selector)
    if type(selector) is int:
        return f"position {selector}"
    if (
        isinstance(selector, tuple)
        and len(selector) == 2
        and selector[0] == "identifier"
        and isinstance(selector[1], str)
    ):
        return f"ID({selector[1]!r})"
    return repr(selector)


def _direction_matches(socket, direction: str) -> bool:
    """Return whether *socket* belongs to the requested input/output direction."""
    actual_is_output = bool(getattr(socket, "is_output", direction == "output"))
    return actual_is_output if direction == "output" else not actual_is_output


def _is_addressable_socket(socket, *, direction: str) -> bool:
    """Return whether a Blender socket is an ordinary addressable data socket."""
    if not _direction_matches(socket, direction):
        return False
    if getattr(socket, "bl_idname", "") == "NodeSocketVirtual":
        return False
    try:
        if bool(getattr(socket, "is_unavailable", False)):
            return False
    except Exception:
        pass
    return True


def _addressable_sockets(sockets, *, direction: str):
    """Return the ordered source-addressable view of one Blender socket collection."""
    return tuple(socket for socket in sockets if _is_addressable_socket(socket, direction=direction))


def resolve_socket(sockets, selector, *, direction, context):
    """Resolve one normalized source selector to an addressable Blender socket."""
    if isinstance(selector, str):
        addressable = _addressable_sockets(sockets, direction=direction)
        matches = [socket for socket in addressable if getattr(socket, "name", None) == selector]
        if not matches:
            available = ", ".join(repr(getattr(socket, "name", "")) for socket in addressable) or "<none>"
            raise CompileError(
                f"{context}: unknown {direction} socket name {selector!r}; available socket names: {available}"
            )
        if len(matches) > 1:
            positions = [index for index, socket in enumerate(addressable) if getattr(socket, "name", None) == selector]
            suggestion = " or ".join(str(index) for index in positions)
            raise CompileError(
                f"{context}: {direction} socket name {selector!r} is ambiguous; "
                f"use positional {direction} {suggestion}"
            )
        return matches[0]

    if type(selector) is int:
        if selector < 0:
            raise CompileError(f"{context}: {direction} socket position must be non-negative")
        addressable = _addressable_sockets(sockets, direction=direction)
        if selector >= len(addressable):
            raise CompileError(
                f"{context}: {direction} socket position {selector} is out of range "
                f"for {len(addressable)} addressable sockets"
            )
        return addressable[selector]

    if (
        isinstance(selector, tuple)
        and len(selector) == 2
        and selector[0] == "identifier"
        and isinstance(selector[1], str)
        and selector[1]
    ):
        identifier = selector[1]
        matches = [
            socket
            for socket in sockets
            if _direction_matches(socket, direction)
            and getattr(socket, "identifier", None) == identifier
        ]
        if not matches:
            raise CompileError(f"{context}: unknown {direction} socket identifier {identifier!r}")
        if len(matches) > 1:
            raise CompileError(
                f"{context}: duplicate {direction} socket identifier {identifier!r} in Blender node schema"
            )
        socket = matches[0]
        if not _is_addressable_socket(socket, direction=direction):
            raise CompileError(f"{context}: {direction} socket identifier {identifier!r} is not addressable")
        return socket

    raise CompileError(f"{context}: invalid normalized {direction} socket selector {_selector_label(selector)}")


def _capture_physical_socket_ref(socket, *, direction: str, context: str) -> RawPhysicalSocketRef:
    """Capture schema-v2 identity from one already-resolved declared socket."""
    if not _is_addressable_socket(socket, direction=direction):
        raise CompileError(f"{context}: resolved Blender socket is not addressable")
    identifier = getattr(socket, "identifier", "")
    if not isinstance(identifier, str) or not identifier:
        raise CompileError(
            f"{context}: Blender socket has no non-empty identifier and cannot be persisted safely"
        )
    socket_type = getattr(socket, "bl_idname", "")
    if not isinstance(socket_type, str) or not socket_type:
        raise CompileError(f"{context}: Blender socket type metadata is unavailable")
    return RawPhysicalSocketRef(identifier, socket_type)


def _resolve_physical_socket_ref(sockets, ref, *, direction: str, context: str):
    """Resolve one schema-v2 persisted physical reference exactly and fail closed."""
    if not isinstance(ref, RawPhysicalSocketRef):
        raise TypeError("persisted raw socket reference must be RawPhysicalSocketRef")
    matches = [
        socket
        for socket in sockets
        if _direction_matches(socket, direction)
        and getattr(socket, "identifier", None) == ref.identifier
    ]
    if not matches:
        raise CompileError(f"{context}: stored {direction} socket identifier {ref.identifier!r} is missing")
    if len(matches) > 1:
        raise CompileError(f"{context}: stored {direction} socket identifier {ref.identifier!r} is duplicated")
    socket = matches[0]
    if not _is_addressable_socket(socket, direction=direction):
        raise CompileError(f"{context}: stored {direction} socket identifier {ref.identifier!r} is not addressable")
    current_type = getattr(socket, "bl_idname", "")
    if current_type != ref.socket_type:
        raise CompileError(
            f"{context}: stored {direction} socket identifier {ref.identifier!r} changed type "
            f"from {ref.socket_type!r} to {current_type!r}"
        )
    return socket


def _reject_duplicate_physical_socket(existing, socket, *, context: str) -> None:
    """Reject a second declared contract for the same Blender RNA socket."""
    if any(_same_blender_ref(candidate, socket) for candidate in existing):
        raise CompileError(context)


def _validate_materialized_value(value, socket, context, socket_name):
    """Validate one runtime Value against a resolved input without linking it."""
    if not isinstance(value, Value):
        raise CompileError(f"{context}: input {socket_name!r} expects a runtime node value")
    _validate_runtime_socket_type(socket, value.typ, direction="input", context=context, socket_name=socket_name)


def _link_materialized_value(group, value, socket, context, socket_name):
    """Link one prevalidated runtime Value to a prevalidated raw-node input."""
    try:
        group.links.new(value.socket, socket)
    except Exception as exc:
        raise CompileError(f"{context}: failed to link input {socket_name!r}: {exc}") from exc


def _validate_public_type(typ, context):
    if typ not in _SUPPORTED_TYPES:
        raise CompileError(f"{context} must be a NodeForge type token")


def _validate_runtime_socket_type(socket, typ, *, direction, context, socket_name):
    """Reject raw-node runtime declarations/links for unsupported or mismatched sockets."""
    socket_type = runtime_socket_nf_type(socket)
    bl_idname = getattr(socket, "bl_idname", "<unknown>")
    if socket_type is None:
        raise CompileError(
            f"{context}: {direction} socket {socket_name!r} has unsupported Blender socket type {bl_idname!r}"
        )
    allowed = {socket_type}
    if direction == "input" and socket_type == TYPE_FLOAT:
        allowed.add(TYPE_INT)
    if typ not in allowed:
        expected = socket_type if len(allowed) == 1 else " or ".join(sorted(allowed))
        raise CompileError(f"{context}: {direction} socket {socket_name!r} expects {expected}, got {typ}")


def _set_node_property(node, prop_name, prop_value, context):
    if not isinstance(prop_name, str) or not prop_name:
        raise CompileError(f"{context}: property names must be non-empty strings")
    if prop_name.startswith("["):
        raise CompileError(f"{context}: custom properties are not supported in props=")
    try:
        rna_prop = node.bl_rna.properties.get(prop_name)
    except Exception:
        rna_prop = None
    if rna_prop is None and not hasattr(node, prop_name):
        raise CompileError(f"{context}: unknown property {prop_name!r} on {node.bl_idname}")
    if rna_prop is not None and getattr(rna_prop, "is_readonly", False):
        raise CompileError(f"{context}: property {prop_name!r} is read-only on {node.bl_idname}")
    try:
        setattr(node, prop_name, prop_value)
    except Exception as exc:
        raise CompileError(f"{context}: failed to set property {prop_name!r} on {node.bl_idname}: {exc}") from exc
    actual = _normalize_json_value(getattr(node, prop_name), f"{context}: property {prop_name!r}")
    expected = _normalize_json_value(prop_value, f"{context}: property {prop_name!r}")
    if not _normalized_equal(actual, expected):
        raise CompileError(f"{context}: property {prop_name!r} was not assigned equivalently")


def _require_multi_input(socket, context, socket_name):
    if hasattr(socket, "is_multi_input"):
        try:
            if not bool(socket.is_multi_input):
                raise CompileError(f"{context}: input {socket_name!r} is not a multi-input socket")
            return
        except CompileError:
            raise
        except Exception as exc:
            raise CompileError(f"{context}: could not verify multi-input socket {socket_name!r}") from exc
    raise CompileError(f"{context}: Blender did not expose multi-input metadata for socket {socket_name!r}")


def _assign_literal_default(socket, literal, context, socket_name):
    literal = _normalize_input_literal(literal, context, socket_name)
    if not hasattr(socket, "default_value"):
        raise CompileError(f"{context}: input {socket_name!r} does not accept literal defaults")
    try:
        socket.default_value = literal
    except Exception:
        try:
            for index, component in enumerate(literal):
                socket.default_value[index] = component
        except Exception as exc:
            raise CompileError(f"{context}: failed to assign literal default for input {socket_name!r}: {exc}") from exc
    try:
        normalized = _normalize_socket_default(socket.default_value, f"{context}: input {socket_name!r}")
    except Exception as exc:
        raise CompileError(f"{context}: could not verify literal default for input {socket_name!r}") from exc
    return normalized


def _normalize_input_literal(literal, context, socket_name):
    if isinstance(literal, tuple):
        if len(literal) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in literal):
            return tuple(float(v) for v in literal)
        raise CompileError(f"{context}: input {socket_name!r} tuple defaults must be numeric 3-tuples")
    if isinstance(literal, list):
        raise CompileError(f"{context}: input {socket_name!r} list syntax is reserved for multi-input fanout")
    if isinstance(literal, (bool, int, float, str)):
        if isinstance(literal, str) and not literal:
            raise CompileError(f"{context}: input {socket_name!r} string defaults must be non-empty")
        return literal
    raise CompileError(f"{context}: unsupported literal default for input {socket_name!r}")


def _normalize_socket_default(value, context):
    return _normalize_json_value(value, context)


def _normalize_json_value(value, context="value"):
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(v, context) for v in value]
    try:
        if not isinstance(value, (str, bytes)):
            return [_normalize_json_value(v, context) for v in value]
    except Exception:
        pass
    raise CompileError(f"{context}: value is not supported by raw-node metadata")


def _normalized_equal(a, b):
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= 1e-6
        except Exception:
            return False
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return all(_normalized_equal(x, y) for x, y in zip(a, b))
    return a == b


def _serialize_physical_socket_ref(ref: RawPhysicalSocketRef) -> dict[str, str]:
    """Serialize one validated schema-v2 raw socket reference."""
    if not isinstance(ref, RawPhysicalSocketRef):
        raise TypeError("raw socket ref serializer requires RawPhysicalSocketRef")
    return {"identifier": ref.identifier, "socket_type": ref.socket_type}


def _parse_physical_socket_ref(value, *, context: str) -> RawPhysicalSocketRef:
    """Parse one schema-v2 raw socket reference from JSON-compatible data."""
    if not isinstance(value, dict):
        raise CompileError(f"{context}: raw socket reference must be an object")
    try:
        return RawPhysicalSocketRef(value.get("identifier"), value.get("socket_type"))
    except (TypeError, ValueError) as exc:
        raise CompileError(f"{context}: invalid raw socket reference: {exc}") from exc


def _write_raw_metadata(node, bl_idname, props, inputs, outputs, mode):
    """Write one complete schema-v2 raw-node metadata record."""
    try:
        node[RAW_NODE_PROP] = True
        node[RAW_SCHEMA_VERSION_PROP] = RAW_SCHEMA_VERSION
        node[RAW_BL_IDNAME_PROP] = bl_idname
        node[RAW_PROPS_JSON_PROP] = _json_dumps(props)
        node[RAW_INPUTS_JSON_PROP] = _json_dumps(inputs)
        node[RAW_OUTPUTS_JSON_PROP] = _json_dumps([_serialize_physical_socket_ref(ref) for ref in outputs])
        node[RAW_MODE_PROP] = mode
    except Exception as exc:
        raise CompileError(f"raw node metadata could not be written for {bl_idname!r}: {exc}") from exc


def _json_dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_loads_node(node, key, default=None):
    try:
        raw = node.get(key, None)
    except Exception:
        raw = None
    if raw is None:
        if default is not None:
            return default
        raise CompileError(f"raw node metadata {key!r} is missing on {getattr(node, 'name', '<node>')}")
    try:
        return json.loads(raw)
    except Exception as exc:
        raise CompileError(f"raw node metadata {key!r} is invalid JSON") from exc


def is_raw_node(node):
    try:
        return bool(node.get(RAW_NODE_PROP, False))
    except Exception:
        return False


def _validate_raw_mode(mode, *, context: str) -> str:
    """Validate one persisted raw-node output mode."""
    if mode not in {RAW_SINGLE_MODE, RAW_MULTI_MODE}:
        raise CompileError(f"{context}: invalid raw output mode {mode!r}")
    return mode


def raw_node_spec(node):
    """Parse one versioned raw-node metadata record without mutating the node."""
    if not is_raw_node(node):
        raise CompileError("node is not marked as a NodeForge raw node")
    bl_idname = node.get(RAW_BL_IDNAME_PROP)
    if not isinstance(bl_idname, str) or not bl_idname:
        raise CompileError("raw node metadata has invalid bl_idname")
    props = _json_loads_node(node, RAW_PROPS_JSON_PROP, {})
    if not isinstance(props, dict):
        raise CompileError("raw node metadata props must be an object")
    inputs = _json_loads_node(node, RAW_INPUTS_JSON_PROP, [])
    outputs = _json_loads_node(node, RAW_OUTPUTS_JSON_PROP, [])
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        raise CompileError("raw node metadata inputs/outputs must be lists")
    mode = _validate_raw_mode(node.get(RAW_MODE_PROP), context="raw node metadata")

    raw_version = node.get(RAW_SCHEMA_VERSION_PROP, None)
    if raw_version is None:
        normalized_inputs = []
        for entry in inputs:
            if not isinstance(entry, dict):
                raise CompileError("raw node schema v1 input contract must be an object")
            name = entry.get("name")
            contract_mode = entry.get("mode")
            if not isinstance(name, str) or not name:
                raise CompileError("raw node schema v1 input name must be non-empty")
            if contract_mode not in _INPUT_MODES:
                raise CompileError("raw node schema v1 input mode is invalid")
            normalized_inputs.append(dict(entry))
        for name in outputs:
            if not isinstance(name, str) or not name:
                raise CompileError("raw node schema v1 output name must be non-empty")
        return {
            "schema_version": 1,
            "bl_idname": bl_idname,
            "props": props,
            "inputs": normalized_inputs,
            "outputs": tuple(outputs),
            "mode": mode,
        }

    if type(raw_version) is not int or raw_version != RAW_SCHEMA_VERSION:
        raise CompileError(f"unsupported raw node metadata schema version {raw_version!r}")

    normalized_inputs = []
    for entry in inputs:
        if not isinstance(entry, dict):
            raise CompileError("raw node schema v2 input contract must be an object")
        contract_mode = entry.get("mode")
        if contract_mode not in _INPUT_MODES:
            raise CompileError("raw node schema v2 input mode is invalid")
        ref = _parse_physical_socket_ref(entry.get("socket"), context="raw node schema v2 input")
        normalized = dict(entry)
        normalized["socket"] = ref
        normalized_inputs.append(normalized)
    normalized_outputs = tuple(
        _parse_physical_socket_ref(value, context="raw node schema v2 output")
        for value in outputs
    )
    return {
        "schema_version": RAW_SCHEMA_VERSION,
        "bl_idname": bl_idname,
        "props": props,
        "inputs": normalized_inputs,
        "outputs": normalized_outputs,
        "mode": mode,
    }


def _resolve_spec_socket(node, locator, *, schema_version: int, direction: str, context: str):
    """Resolve one declared persisted locator according to its schema generation."""
    collection = node.outputs if direction == "output" else node.inputs
    if schema_version == 1:
        return resolve_socket(collection, locator, direction=direction, context=context)
    if schema_version == RAW_SCHEMA_VERSION:
        return _resolve_physical_socket_ref(collection, locator, direction=direction, context=context)
    raise CompileError(f"{context}: unsupported raw metadata schema version {schema_version!r}")


def _copy_owned_raw_metadata(src_node, dst_node, *, schema_version: int) -> None:
    """Copy NodeForge-owned raw custom properties without changing schema generation."""
    required_keys = (
        RAW_NODE_PROP,
        RAW_BL_IDNAME_PROP,
        RAW_PROPS_JSON_PROP,
        RAW_INPUTS_JSON_PROP,
        RAW_OUTPUTS_JSON_PROP,
        RAW_MODE_PROP,
    )
    for key in required_keys:
        try:
            dst_node[key] = src_node[key]
        except Exception as exc:
            raise RuntimeError(f"raw node cutover failed to copy metadata {key!r}: {exc}") from exc
    if schema_version == RAW_SCHEMA_VERSION:
        try:
            dst_node[RAW_SCHEMA_VERSION_PROP] = src_node[RAW_SCHEMA_VERSION_PROP]
        except Exception as exc:
            raise RuntimeError(f"raw node cutover failed to copy metadata {RAW_SCHEMA_VERSION_PROP!r}: {exc}") from exc
    else:
        try:
            if RAW_SCHEMA_VERSION_PROP in dst_node:
                del dst_node[RAW_SCHEMA_VERSION_PROP]
        except Exception as exc:
            raise RuntimeError(
                f"raw node cutover failed to preserve v1 metadata generation: {exc}"
            ) from exc


def copy_raw_node_properties(src_node, dst_node):
    """Copy raw-owned properties/defaults while preserving metadata generation."""
    spec = raw_node_spec(src_node)
    if getattr(dst_node, "bl_idname", None) != spec["bl_idname"]:
        raise RuntimeError("raw node cutover bl_idname mismatch")
    for prop_name, value in spec["props"].items():
        _set_node_property(dst_node, prop_name, value, "raw-node cutover")
    try:
        dst_node.location = tuple(src_node.location)
        dst_node.name = src_node.name
        dst_node.label = src_node.label
    except Exception as exc:
        raise RuntimeError(f"raw node cutover failed to copy identity/layout: {exc}") from exc

    _copy_owned_raw_metadata(src_node, dst_node, schema_version=spec["schema_version"])

    for contract in spec["inputs"]:
        if contract.get("mode") != INPUT_LITERAL:
            continue
        locator = contract["name"] if spec["schema_version"] == 1 else contract["socket"]
        socket = _resolve_spec_socket(
            dst_node,
            locator,
            schema_version=spec["schema_version"],
            direction="input",
            context="raw-node cutover",
        )
        expected = contract.get("default")
        try:
            socket.default_value = expected
        except Exception:
            try:
                for index, component in enumerate(expected):
                    socket.default_value[index] = component
            except Exception as exc:
                raise RuntimeError(f"raw node cutover failed to assign literal default: {exc}") from exc
        actual = _normalize_socket_default(socket.default_value, "raw-node cutover input")
        if not _normalized_equal(actual, expected):
            raise RuntimeError("raw node cutover literal default mismatch")

    for locator in spec["outputs"]:
        _resolve_spec_socket(
            dst_node,
            locator,
            schema_version=spec["schema_version"],
            direction="output",
            context="raw-node cutover",
        )


def _physical_collection_index(collection, socket) -> int:
    """Return physical collection index using Blender-proxy identity semantics."""
    for index, candidate in enumerate(collection):
        if _same_blender_ref(candidate, socket):
            return index
    raise RuntimeError("raw node cutover source socket is not present in its physical collection")


def _declared_locators(spec, *, direction: str):
    """Yield persisted declared socket locators for one metadata generation."""
    if direction == "output":
        yield from spec["outputs"]
        return
    for contract in spec["inputs"]:
        if spec["schema_version"] == 1:
            yield contract["name"]
        else:
            yield contract["socket"]


def resolve_cutover_socket(src_node, src_socket, dst_node, *, direction):
    """Resolve one destination socket using raw metadata for declared contracts."""
    dst_collection = dst_node.outputs if direction == "output" else dst_node.inputs
    src_collection = src_node.outputs if direction == "output" else src_node.inputs
    if not is_raw_node(src_node):
        return dst_collection[_physical_collection_index(src_collection, src_socket)]

    spec = raw_node_spec(src_node)
    for locator in _declared_locators(spec, direction=direction):
        declared_src = _resolve_spec_socket(
            src_node,
            locator,
            schema_version=spec["schema_version"],
            direction=direction,
            context="raw-node cutover source",
        )
        if _same_blender_ref(declared_src, src_socket):
            return _resolve_spec_socket(
                dst_node,
                locator,
                schema_version=spec["schema_version"],
                direction=direction,
                context="raw-node cutover destination",
            )

    # Undeclared/manual/external raw links retain the generic physical-position
    # copy behavior and never become NodeForge-declared raw metadata.
    return dst_collection[_physical_collection_index(src_collection, src_socket)]


def _same_blender_ref(left, right):
    """Return true for distinct Python proxies wrapping the same Blender RNA object."""
    if left is right:
        return True
    try:
        if left == right:
            return True
    except Exception:
        pass
    try:
        return left.as_pointer() == right.as_pointer()
    except Exception:
        return False


def _incoming_links_to(group, node, socket):
    return [
        link
        for link in group.links
        if _same_blender_ref(link.to_node, node) and _same_blender_ref(link.to_socket, socket)
    ]


def _ensure_unique_resolved_contracts(sockets, *, context: str) -> None:
    """Reject duplicate persisted contracts resolving to one physical socket."""
    for index, socket in enumerate(sockets):
        if any(_same_blender_ref(socket, other) for other in sockets[:index]):
            raise RuntimeError(context)


def validate_raw_node_after_cutover(node, group):
    """Validate a destination raw node against its versioned declared metadata."""
    spec = raw_node_spec(node)
    if getattr(node, "bl_idname", None) != spec["bl_idname"]:
        raise RuntimeError("raw node cutover validation bl_idname mismatch")
    for prop_name, expected in spec["props"].items():
        actual = _normalize_json_value(getattr(node, prop_name), f"raw-node cutover property {prop_name!r}")
        if not _normalized_equal(actual, expected):
            raise RuntimeError(f"raw node cutover validation property mismatch for {prop_name!r}")

    resolved_outputs = [
        _resolve_spec_socket(
            node,
            locator,
            schema_version=spec["schema_version"],
            direction="output",
            context="raw-node cutover validation",
        )
        for locator in spec["outputs"]
    ]
    _ensure_unique_resolved_contracts(
        resolved_outputs,
        context="raw node cutover validation duplicate output contract",
    )

    resolved_inputs = []
    for contract in spec["inputs"]:
        locator = contract["name"] if spec["schema_version"] == 1 else contract["socket"]
        socket = _resolve_spec_socket(
            node,
            locator,
            schema_version=spec["schema_version"],
            direction="input",
            context="raw-node cutover validation",
        )
        resolved_inputs.append(socket)
        mode = contract.get("mode")
        incoming = _incoming_links_to(group, node, socket)
        if mode == INPUT_LITERAL:
            expected = contract.get("default")
            actual = _normalize_socket_default(socket.default_value, "raw-node cutover input")
            if not _normalized_equal(actual, expected):
                raise RuntimeError("raw node cutover validation literal default mismatch")
            if incoming:
                raise RuntimeError("raw node cutover validation expected no links for literal input")
        elif mode == INPUT_SINGLE_LINK:
            if len(incoming) != 1:
                raise RuntimeError("raw node cutover validation expected one link for input")
        elif mode == INPUT_MULTI_LINK:
            _require_multi_input(socket, "raw-node cutover validation", getattr(socket, "identifier", "<input>"))
            if len(incoming) != int(contract.get("links", -1)):
                raise RuntimeError("raw node cutover validation multi-link count mismatch for input")
        else:
            raise RuntimeError(f"raw node cutover validation unknown input mode {mode!r}")

    _ensure_unique_resolved_contracts(
        resolved_inputs,
        context="raw node cutover validation duplicate input contract",
    )


__all__ = [
    "build_materialized_raw_node",
    "resolve_socket",
    "is_raw_node",
    "raw_node_spec",
    "copy_raw_node_properties",
    "resolve_cutover_socket",
    "validate_raw_node_after_cutover",
    "RAW_NODE_PROP",
    "RAW_SCHEMA_VERSION_PROP",
    "RAW_SCHEMA_VERSION",
    "RAW_INPUTS_JSON_PROP",
]
