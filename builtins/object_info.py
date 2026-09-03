"""Lazy lowering of Object values through Blender Object Info nodes."""

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
    socket = next((item for item in sockets if item.name == name), None)
    if socket is None:
        raise CompileError(f"Object Info node is missing expected {context} socket {name!r}")
    return socket

def resolve_object_property(group, obj, name, *, x=0, y=0):
    """Return a cached Object Info output for an ObjectValue property."""
    if name not in OBJECT_PROPERTY_TYPES:
        raise CompileError("Object values support only .geometry, .location, .rotation and .scale")
    if obj._object_info_outputs is None:
        try:
            node = _new_node(group, "GeometryNodeObjectInfo", x, y)
            node.transform_space = obj._info_transform_space
            object_input = _socket(node.inputs, "Object", "input")
            as_instance = _socket(node.inputs, "As Instance", "input")
            as_instance.default_value = obj._info_as_instance
            group.links.new(obj.socket, object_input)
            outputs = {
                prop: Value(
                    _socket(node.outputs, _PROPERTY_OUTPUT_NAMES[prop], "output"),
                    typ,
                )
                for prop, typ in OBJECT_PROPERTY_TYPES.items()
            }
        except CompileError:
            raise
        except Exception as exc:
            raise CompileError(f"Failed to create Object Info node: {exc}") from exc
        obj._object_info_outputs = outputs
        obj._info_resolved = True
    return obj._object_info_outputs[name]
