"""Blender backend helpers for Object Info property materialization."""

from __future__ import annotations

from ..constants import OBJECT_PROPERTY_TYPES
from ..errors import CompileError
from ..nodes import _new_node
from ..values import Value

_PROPERTY_OUTPUT_NAMES = {
    "geometry": "Geometry",
    "location": "Location",
    "rotation": "Rotation",
    "scale": "Scale",
}


def _socket(sockets, name, context):
    """Return one Blender socket by name with a controlled backend diagnostic."""
    try:
        return sockets[name]
    except Exception as exc:
        raise CompileError(f"Object Info node is missing expected {context} socket {name!r}") from exc


def resolve_object_property_explicit(
    group,
    obj,
    name,
    *,
    transform_space: str,
    as_instance: bool,
    x=0,
    y=0,
):
    """Materialize one Object property using explicit frontend-owned configuration."""
    if name not in OBJECT_PROPERTY_TYPES:
        raise CompileError("Object values support only .geometry, .location, .rotation and .scale")
    if transform_space not in {"ORIGINAL", "RELATIVE"}:
        raise CompileError("Internal error: invalid Object Info transform-space configuration")
    if not isinstance(as_instance, bool):
        raise CompileError("Internal error: invalid Object Info as-instance configuration")
    config = (transform_space, as_instance)
    if obj._object_info_outputs is None:
        try:
            node = _new_node(group, "GeometryNodeObjectInfo", x, y)
            node.transform_space = transform_space
            object_input = _socket(node.inputs, "Object", "input")
            as_instance_socket = _socket(node.inputs, "As Instance", "input")
            as_instance_socket.default_value = as_instance
            group.links.new(obj.socket, object_input)
            outputs = {
                prop: Value(_socket(node.outputs, _PROPERTY_OUTPUT_NAMES[prop], "output"), typ)
                for prop, typ in OBJECT_PROPERTY_TYPES.items()
            }
        except CompileError:
            raise
        except Exception as exc:
            raise CompileError(f"Failed to create Object Info node: {exc}") from exc
        obj._object_info_outputs = outputs
        obj._object_info_cache_config = config
    elif obj._object_info_cache_config != config:
        raise CompileError("Internal error: Object Info backend cache configuration mismatch")
    return obj._object_info_outputs[name]


__all__ = ["resolve_object_property_explicit"]
