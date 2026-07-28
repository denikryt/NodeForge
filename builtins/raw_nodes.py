"""Controlled raw Blender node construction for the NodeForge DSL."""

from __future__ import annotations

import ast
import json
from collections import Counter

from ..compile_time import reject_compile_time_object
from ..consteval import _const_eval, _is_const_vector
from ..constants import (
    TYPE_BOOL,
    TYPE_FLOAT,
    TYPE_GEOMETRY,
    TYPE_INT,
    TYPE_MATERIAL,
    TYPE_OBJECT,
    TYPE_TOKEN_NAMES,
    TYPE_VECTOR,
)
from ..errors import CompileError
from ..nodes import _new_node, _is_number_type
from ..values import NodeResult, Value, make_value
from ..statements import _kw_dict

NAMES = {"node"}

RAW_NODE_PROP = "nodeforge_raw_node"
RAW_BL_IDNAME_PROP = "nodeforge_raw_bl_idname"
RAW_PROPS_JSON_PROP = "nodeforge_raw_props_json"
RAW_INPUTS_JSON_PROP = "nodeforge_raw_inputs_json"
RAW_OUTPUTS_JSON_PROP = "nodeforge_raw_output_sockets_json"
RAW_MODE_PROP = "nodeforge_raw_mode"
RAW_SINGLE_MODE = "single_output"
RAW_MULTI_MODE = "multi_output"
INPUT_LITERAL = "literal_default"
INPUT_SINGLE_LINK = "single_link"
INPUT_MULTI_LINK = "multi_link"

_SUPPORTED_TYPES = {TYPE_FLOAT, TYPE_INT, TYPE_BOOL, TYPE_VECTOR, TYPE_GEOMETRY, TYPE_MATERIAL, TYPE_OBJECT}


def compile_call(comp, expr, depth=0):
    """Compile a public node(...) call."""
    x = depth * 240
    y = -depth * 90
    spec = _parse_node_call(comp, expr)
    return build_raw_node(comp, x=x, y=y, context="node()", **spec)


def _parse_node_call(comp, expr):
    if len(expr.args) != 1:
        raise CompileError("node(...) expects exactly one positional bl_idname argument")
    if any(kw.arg is None for kw in expr.keywords):
        raise CompileError("node(...) does not support **kwargs")
    kws = _kw_dict(expr)
    allowed = {"props", "inputs", "output", "typ", "outputs"}
    extra = set(kws) - allowed
    if extra:
        raise CompileError("node(...) got unsupported keyword argument(s): " + ", ".join(sorted(extra)))

    output = _optional_literal_string(comp, kws.get("output"), "output=")
    typ = _optional_type_token(kws.get("typ"), "typ=")
    outputs = _parse_outputs(comp, kws.get("outputs"))
    has_single = output is not None or typ is not None
    has_multi = outputs is not None
    if has_single and has_multi:
        raise CompileError("node(...) accepts either output=/typ= or outputs=, not both")
    if has_single and (output is None or typ is None):
        raise CompileError("node(...) single-output mode requires both output= and typ=")
    if not has_single and not has_multi:
        raise CompileError("node(...) requires output=/typ= or outputs=")

    return {
        "bl_idname": _literal_string_node_arg(comp, expr.args[0], "bl_idname"),
        "props": _parse_literal_dict(comp, kws.get("props"), "props=") if "props" in kws else {},
        "inputs": _parse_inputs(comp, kws.get("inputs")) if "inputs" in kws else {},
        "output": output,
        "typ": typ,
        "outputs": outputs,
    }


def _literal_string_node_arg(comp, expr, context):
    """Return a node(...) string argument resolved from compile-time constants."""
    try:
        value = _const_eval(expr, comp.consts)
    except CompileError as exc:
        raise CompileError(f"node(...) {context} must be a non-empty compile-time string") from exc
    if isinstance(value, str) and value:
        return value
    raise CompileError(f"node(...) {context} must be a non-empty compile-time string")


def _optional_literal_string(comp, expr, context):
    if expr is None:
        return None
    return _literal_string_node_arg(comp, expr, context)


def _optional_type_token(expr, context):
    if expr is None:
        return None
    if not isinstance(expr, ast.Name) or expr.id not in TYPE_TOKEN_NAMES:
        raise CompileError(f"node(...) {context} must be one of: {', '.join(sorted(TYPE_TOKEN_NAMES))}")
    return TYPE_TOKEN_NAMES[expr.id]


def _parse_outputs(comp, expr):
    if expr is None:
        return None
    if not isinstance(expr, ast.Dict):
        raise CompileError("node(...) outputs= must be written as a literal dict")
    if not expr.keys:
        raise CompileError("node(...) outputs= cannot be empty")
    out = {}
    for key_expr, value_expr in zip(expr.keys, expr.values):
        key = _literal_string_node_arg(comp, key_expr, "outputs key")
        if key in out:
            raise CompileError(f"node(...) outputs= has duplicate socket {key!r}")
        out[key] = _optional_type_token(value_expr, f"outputs[{key!r}]")
    return out


def _parse_literal_dict(comp, expr, context):
    if expr is None:
        return {}
    if not isinstance(expr, ast.Dict):
        raise CompileError(f"node(...) {context} must be written as a literal dict")
    out = {}
    for key_expr, value_expr in zip(expr.keys, expr.values):
        key = _literal_string_node_arg(comp, key_expr, f"{context} key")
        if key in out:
            raise CompileError(f"node(...) {context} has duplicate key {key!r}")
        try:
            value = _const_eval(value_expr, comp.consts)
        except CompileError as exc:
            raise CompileError(f"node(...) {context} values must be compile-time literals") from exc
        out[key] = _normalize_json_value(value, f"node(...) {context}{key!r}")
    return out


def _parse_inputs(comp, expr):
    if expr is None:
        return {}
    if not isinstance(expr, ast.Dict):
        raise CompileError("node(...) inputs= must be written as a literal dict")
    out = {}
    for key_expr, value_expr in zip(expr.keys, expr.values):
        key = _literal_string_node_arg(comp, key_expr, "inputs key")
        if key in out:
            raise CompileError(f"node(...) inputs= has duplicate socket {key!r}")
        if isinstance(value_expr, ast.List):
            if not value_expr.elts:
                raise CompileError(f"node(...) input {key!r} list cannot be empty")
            out[key] = list(value_expr.elts)
        else:
            out[key] = value_expr
    return out


def build_raw_node(
    comp,
    *,
    bl_idname,
    props=None,
    inputs=None,
    output=None,
    typ=None,
    outputs=None,
    x=0,
    y=0,
    context="raw node",
):
    """Build a raw Blender node and return a Value or NodeResult."""
    props = dict(props or {})
    inputs = dict(inputs or {})
    if not isinstance(bl_idname, str) or not bl_idname:
        raise CompileError(f"{context}: bl_idname must be a non-empty string")
    _validate_public_type(typ, f"{context}: typ") if typ is not None else None
    if outputs is not None:
        if not outputs:
            raise CompileError(f"{context}: outputs cannot be empty")
        for out_typ in outputs.values():
            _validate_public_type(out_typ, f"{context}: output type")
    elif output is None or typ is None:
        raise CompileError(f"{context}: raw node requires single-output or multi-output declaration")

    try:
        node = _new_node(comp.group, bl_idname, x, y)
    except Exception as exc:
        raise CompileError(f"{context}: could not create Blender node {bl_idname!r}: {exc}") from exc

    for prop_name, prop_value in props.items():
        _set_node_property(node, prop_name, prop_value, context)

    input_contracts = []
    for socket_name, value_spec in inputs.items():
        socket = resolve_socket(node.inputs, socket_name, direction="input", context=context)
        if isinstance(value_spec, list):
            input_contracts.append(_wire_multi_input(comp, node, socket_name, socket, value_spec, context))
        else:
            input_contracts.append(_wire_or_default_input(comp, node, socket_name, socket, value_spec, context))

    if outputs is not None:
        values = {}
        for socket_name, out_typ in outputs.items():
            socket = resolve_socket(node.outputs, socket_name, direction="output", context=context)
            _validate_runtime_socket_type(socket, out_typ, direction="output", context=context, socket_name=socket_name)
            values[socket_name] = make_value(socket, out_typ)
        _write_raw_metadata(node, bl_idname, props, input_contracts, tuple(outputs.keys()), RAW_MULTI_MODE)
        return NodeResult(values)

    out_socket = resolve_socket(node.outputs, output, direction="output", context=context)
    _validate_runtime_socket_type(out_socket, typ, direction="output", context=context, socket_name=output)
    _write_raw_metadata(node, bl_idname, props, input_contracts, (output,), RAW_SINGLE_MODE)
    return make_value(out_socket, typ)


def _validate_public_type(typ, context):
    if typ not in _SUPPORTED_TYPES:
        raise CompileError(f"{context} must be a NodeForge type token")


def _socket_runtime_type(socket):
    """Return the NodeForge runtime type represented by a supported Blender socket."""
    bl_idname = getattr(socket, "bl_idname", "") or ""
    if bl_idname.startswith("NodeSocketFloat"):
        return TYPE_FLOAT
    if bl_idname.startswith("NodeSocketInt"):
        return TYPE_INT
    if bl_idname.startswith("NodeSocketBool"):
        return TYPE_BOOL
    if bl_idname.startswith("NodeSocketVector"):
        return TYPE_VECTOR
    if bl_idname.startswith("NodeSocketGeometry"):
        return TYPE_GEOMETRY
    if bl_idname.startswith("NodeSocketMaterial"):
        return TYPE_MATERIAL
    if bl_idname.startswith("NodeSocketObject"):
        return TYPE_OBJECT
    return None


def _validate_runtime_socket_type(socket, typ, *, direction, context, socket_name):
    """Reject raw-node runtime declarations/links for unsupported or mismatched sockets."""
    socket_type = _socket_runtime_type(socket)
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
        raise CompileError(
            f"{context}: {direction} socket {socket_name!r} expects {expected}, got {typ}"
        )


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


def resolve_socket(sockets, socket_name, *, direction, context):
    """Resolve one enabled socket by exact name."""
    matches = []
    enabled_names = []
    for socket in sockets:
        try:
            enabled = bool(getattr(socket, "enabled", True))
        except Exception:
            enabled = True
        if enabled:
            try:
                enabled_names.append(socket.name)
            except Exception:
                pass
        try:
            name = socket.name
        except Exception:
            continue
        if name == socket_name and enabled:
            matches.append(socket)
    if not matches:
        available = ", ".join(repr(n) for n in enabled_names) or "<none>"
        raise CompileError(f"{context}: unknown {direction} socket {socket_name!r}; available enabled sockets: {available}")
    if len(matches) > 1:
        raise CompileError(f"{context}: ambiguous {direction} socket {socket_name!r}")
    return matches[0]


def _wire_or_default_input(comp, node, socket_name, socket, value_spec, context):
    if isinstance(value_spec, Value):
        value = value_spec
        reject_compile_time_object(value, f"{context} input {socket_name!r}")
        _link_value(comp, value, socket, context, socket_name)
        return {"name": socket_name, "mode": INPUT_SINGLE_LINK, "links": 1}
    literal_known = not isinstance(value_spec, ast.AST)
    literal = value_spec
    if isinstance(value_spec, ast.AST):
        try:
            literal = _const_eval(value_spec, comp.consts)
            literal_known = True
        except CompileError:
            literal_known = False
    if literal_known:
        normalized = _assign_literal_default(socket, literal, context, socket_name)
        return {"name": socket_name, "mode": INPUT_LITERAL, "default": normalized}
    value = comp.compile(value_spec)
    reject_compile_time_object(value, f"{context} input {socket_name!r}")
    if isinstance(value, list):
        raise CompileError(f"{context}: input {socket_name!r} cannot be a script array; use a literal list only for multi-input fanout")
    _link_value(comp, value, socket, context, socket_name)
    return {"name": socket_name, "mode": INPUT_SINGLE_LINK, "links": 1}


def _wire_multi_input(comp, node, socket_name, socket, items, context):
    _require_multi_input(socket, context, socket_name)
    values = []
    for item in items:
        value = item if isinstance(item, Value) else comp.compile(item)
        reject_compile_time_object(value, f"{context} multi-input {socket_name!r}")
        if isinstance(value, list):
            raise CompileError(f"{context}: multi-input {socket_name!r} items cannot be arrays")
        values.append(value)
    for value in values:
        _link_value(comp, value, socket, context, socket_name)
    return {"name": socket_name, "mode": INPUT_MULTI_LINK, "links": len(values)}


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


def _link_value(comp, value, socket, context, socket_name):
    if not isinstance(value, Value):
        raise CompileError(f"{context}: input {socket_name!r} expects a runtime node value")
    _validate_runtime_socket_type(socket, value.typ, direction="input", context=context, socket_name=socket_name)
    try:
        comp.group.links.new(value.socket, socket)
    except Exception as exc:
        raise CompileError(f"{context}: failed to link input {socket_name!r}: {exc}") from exc


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
    if _is_const_vector(literal):
        return tuple(float(v) for v in literal)
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
    if _is_const_vector(value):
        return [float(v) for v in value]
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
    # Blender mathutils arrays and enum collections are sequence-like but not JSON values.
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


def _write_raw_metadata(node, bl_idname, props, inputs, outputs, mode):
    try:
        node[RAW_NODE_PROP] = True
        node[RAW_BL_IDNAME_PROP] = bl_idname
        node[RAW_PROPS_JSON_PROP] = _json_dumps(props)
        node[RAW_INPUTS_JSON_PROP] = _json_dumps(inputs)
        node[RAW_OUTPUTS_JSON_PROP] = _json_dumps(list(outputs))
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


def raw_node_spec(node):
    if not is_raw_node(node):
        raise CompileError("node is not marked as a NodeForge raw node")
    return {
        "bl_idname": node.get(RAW_BL_IDNAME_PROP),
        "props": _json_loads_node(node, RAW_PROPS_JSON_PROP, {}),
        "inputs": _json_loads_node(node, RAW_INPUTS_JSON_PROP, []),
        "outputs": _json_loads_node(node, RAW_OUTPUTS_JSON_PROP, []),
        "mode": node.get(RAW_MODE_PROP),
    }


def copy_raw_node_properties(src_node, dst_node):
    """Fail-closed property/default copy for a raw-node-owned node."""
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
    # Copy only NodeForge-owned metadata keys fail-closed.
    for key in (RAW_NODE_PROP, RAW_BL_IDNAME_PROP, RAW_PROPS_JSON_PROP, RAW_INPUTS_JSON_PROP, RAW_OUTPUTS_JSON_PROP, RAW_MODE_PROP):
        try:
            dst_node[key] = src_node[key]
        except Exception as exc:
            raise RuntimeError(f"raw node cutover failed to copy metadata {key!r}: {exc}") from exc
    # Assign declared literal defaults after props, using exact socket resolution.
    for contract in spec["inputs"]:
        if contract.get("mode") != INPUT_LITERAL:
            continue
        socket_name = contract.get("name")
        socket = resolve_socket(dst_node.inputs, socket_name, direction="input", context="raw-node cutover")
        expected = contract.get("default")
        try:
            socket.default_value = expected
        except Exception:
            try:
                for index, component in enumerate(expected):
                    socket.default_value[index] = component
            except Exception as exc:
                raise RuntimeError(f"raw node cutover failed to assign literal default {socket_name!r}: {exc}") from exc
        actual = _normalize_socket_default(socket.default_value, f"raw-node cutover input {socket_name!r}")
        if not _normalized_equal(actual, expected):
            raise RuntimeError(f"raw node cutover literal default mismatch for {socket_name!r}")
    for socket_name in spec["outputs"]:
        resolve_socket(dst_node.outputs, socket_name, direction="output", context="raw-node cutover")


def resolve_cutover_socket(src_node, src_socket, dst_node, *, direction):
    """Resolve a destination socket using raw metadata when the node is raw-owned."""
    collection = dst_node.outputs if direction == "output" else dst_node.inputs
    if not is_raw_node(src_node):
        src_collection = src_node.outputs if direction == "output" else src_node.inputs
        idx = list(src_collection).index(src_socket)
        return collection[idx]
    socket_name = src_socket.name
    spec = raw_node_spec(src_node)
    if direction == "output":
        declared = set(spec["outputs"])
        if socket_name in declared:
            return resolve_socket(collection, socket_name, direction=direction, context="raw-node cutover")
    else:
        declared = {entry.get("name") for entry in spec["inputs"] if entry.get("mode") in {INPUT_SINGLE_LINK, INPUT_MULTI_LINK}}
        if socket_name in declared:
            return resolve_socket(collection, socket_name, direction=direction, context="raw-node cutover")
    idx = list(src_node.outputs if direction == "output" else src_node.inputs).index(src_socket)
    return collection[idx]


def _same_blender_ref(left, right):
    """Return true for distinct Python proxies wrapping the same Blender RNA object."""
    if left is right:
        return True
    try:
        return left == right
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


def validate_raw_node_after_cutover(node, group):
    """Validate a destination raw node against its declared metadata contract."""
    spec = raw_node_spec(node)
    if getattr(node, "bl_idname", None) != spec["bl_idname"]:
        raise RuntimeError("raw node cutover validation bl_idname mismatch")
    for prop_name, expected in spec["props"].items():
        actual = _normalize_json_value(getattr(node, prop_name), f"raw-node cutover property {prop_name!r}")
        if not _normalized_equal(actual, expected):
            raise RuntimeError(f"raw node cutover validation property mismatch for {prop_name!r}")
    for socket_name in spec["outputs"]:
        resolve_socket(node.outputs, socket_name, direction="output", context="raw-node cutover validation")
    for contract in spec["inputs"]:
        socket_name = contract.get("name")
        mode = contract.get("mode")
        socket = resolve_socket(node.inputs, socket_name, direction="input", context="raw-node cutover validation")
        incoming = _incoming_links_to(group, node, socket)
        if mode == INPUT_LITERAL:
            expected = contract.get("default")
            actual = _normalize_socket_default(socket.default_value, f"raw-node cutover input {socket_name!r}")
            if not _normalized_equal(actual, expected):
                raise RuntimeError(f"raw node cutover validation literal default mismatch for {socket_name!r}")
            if incoming:
                raise RuntimeError(f"raw node cutover validation expected no links for literal input {socket_name!r}")
        elif mode == INPUT_SINGLE_LINK:
            if len(incoming) != 1:
                raise RuntimeError(f"raw node cutover validation expected one link for input {socket_name!r}")
        elif mode == INPUT_MULTI_LINK:
            _require_multi_input(socket, "raw-node cutover validation", socket_name)
            if len(incoming) != int(contract.get("links", -1)):
                raise RuntimeError(f"raw node cutover validation multi-link count mismatch for input {socket_name!r}")
        else:
            raise RuntimeError(f"raw node cutover validation unknown input mode {mode!r}")
    counts = Counter((entry.get("name"), entry.get("mode")) for entry in spec["inputs"])
    if any(count > 1 for count in counts.values()):
        raise RuntimeError("raw node cutover validation duplicate input contract")


__all__ = [
    "NAMES",
    "build_raw_node",
    "compile_call",
    "is_raw_node",
    "copy_raw_node_properties",
    "resolve_cutover_socket",
    "validate_raw_node_after_cutover",
    "RAW_NODE_PROP",
    "RAW_INPUTS_JSON_PROP",
]
