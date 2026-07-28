"""Lazy lowering of Object values through Blender Object Info nodes."""

from ..constants import TYPE_GEOMETRY, TYPE_VECTOR
from ..errors import CompileError
from ..nodes import _new_node
from ..values import Value

_PROPERTY_OUTPUTS = {
    "geometry": ("Geometry", TYPE_GEOMETRY),
    "location": ("Location", TYPE_VECTOR),
    "rotation": ("Rotation", TYPE_VECTOR),
    "scale": ("Scale", TYPE_VECTOR),
}

def _socket(sockets, name, context):
    socket = next((item for item in sockets if item.name == name), None)
    if socket is None:
        raise CompileError(f"Object Info node is missing expected {context} socket {name!r}")
    return socket

def resolve_object_property(comp, obj, name, *, x=0, y=0):
    """Return a cached Object Info output for an ObjectValue property."""
    if name not in _PROPERTY_OUTPUTS:
        raise CompileError("Object values support only .geometry, .location, .rotation and .scale")
    if obj._object_info_outputs is None:
        try:
            node = _new_node(comp.group, "GeometryNodeObjectInfo", x, y)
            node.transform_space = obj._info_transform_space
            object_input = _socket(node.inputs, "Object", "input")
            as_instance = _socket(node.inputs, "As Instance", "input")
            as_instance.default_value = obj._info_as_instance
            comp.group.links.new(obj.socket, object_input)
            outputs = {
                prop: Value(_socket(node.outputs, socket_name, "output"), typ)
                for prop, (socket_name, typ) in _PROPERTY_OUTPUTS.items()
            }
        except CompileError:
            raise
        except Exception as exc:
            raise CompileError(f"Failed to create Object Info node: {exc}") from exc
        obj._object_info_outputs = outputs
        obj._info_resolved = True
    return obj._object_info_outputs[name]
