"""Blender backend helpers for NodeForge Bundle construction and item access."""

from __future__ import annotations

from ..semantic.constants import (
    TYPE_BOOL, TYPE_BUNDLE, TYPE_COLOR, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_MATERIAL,
    TYPE_OBJECT, TYPE_STRING, TYPE_VECTOR,
)
from ..errors import CompileError
from .nodes import _new_node
from .input_delivery import deliver_input, require_output
from .values import make_value

_BUNDLE_SOCKET_TYPES = {
    TYPE_FLOAT: "FLOAT",
    TYPE_INT: "INT",
    TYPE_BOOL: "BOOLEAN",
    TYPE_VECTOR: "VECTOR",
    TYPE_COLOR: "RGBA",
    TYPE_GEOMETRY: "GEOMETRY",
    TYPE_MATERIAL: "MATERIAL",
    TYPE_OBJECT: "OBJECT",
    TYPE_STRING: "STRING",
    TYPE_BUNDLE: "BUNDLE",
}

def build_bundle(group, items, *, x=0, y=0):
    """Build a Combine Bundle, delivering items after dynamic sockets are defined."""
    node = _new_node(group, "NodeCombineBundle", x, y)
    try:
        for item_name, value in items:
            socket_type = _bundle_socket_type(value.typ, f"bundle() item {item_name!r}")
            node.bundle_items.new(socket_type, item_name)
    except Exception as exc:
        raise CompileError(f"bundle(): failed to define Combine Bundle items: {exc}") from exc
    for index, (item_name, value) in enumerate(items):
        try:
            socket = node.inputs[index]
        except Exception as exc:
            raise CompileError(f"bundle(): missing generated input for item {item_name!r}") from exc
        if getattr(socket, "name", None) != item_name:
            raise CompileError(
                f"bundle(): generated input order mismatch for {item_name!r}; got {getattr(socket, 'name', None)!r}"
            )
        deliver_input(group, socket, value, context=f"bundle() item {item_name!r}")
    return make_value(node.outputs["Bundle"], TYPE_BUNDLE)


def build_bundle_get(group, bundle_value, path_value, output_type, *, x=0, y=0):
    """Build Get Bundle Item with a physical Bundle and consumer-aware Path."""
    if bundle_value.typ != TYPE_BUNDLE:
        raise CompileError(f"bundle_get() first argument expects Bundle, got {bundle_value.typ}")
    if path_value.typ != TYPE_STRING:
        raise CompileError(f"bundle_get() path expects String, got {path_value.typ}")
    socket_type = _bundle_socket_type(output_type, "bundle_get() typ=")
    node = _new_node(group, "NodeGetBundleItem", x, y)
    try:
        node.socket_type = socket_type
    except Exception as exc:
        raise CompileError(f"bundle_get(): failed to configure Get Bundle Item: {exc}") from exc
    physical_bundle = require_output(group, bundle_value, context="bundle_get() Bundle")
    deliver_input(group, node.inputs["Bundle"], physical_bundle, context="bundle_get() Bundle")
    deliver_input(group, node.inputs["Path"], path_value, context="bundle_get() Path")
    return make_value(node.outputs["Item"], output_type)


def build_bundle_set(group, bundle_value, path_value, item_value, *, x=0, y=0):
    """Build Store Bundle Item with typed Path/Item socket delivery."""
    if bundle_value.typ != TYPE_BUNDLE:
        raise CompileError(f"bundle_set() first argument expects Bundle, got {bundle_value.typ}")
    if path_value.typ != TYPE_STRING:
        raise CompileError(f"bundle_set() path expects String, got {path_value.typ}")
    socket_type = _bundle_socket_type(item_value.typ, "bundle_set() value")
    node = _new_node(group, "NodeStoreBundleItem", x, y)
    try:
        node.socket_type = socket_type
    except Exception as exc:
        raise CompileError(f"bundle_set(): failed to configure Store Bundle Item: {exc}") from exc
    physical_bundle = require_output(group, bundle_value, context="bundle_set() Bundle")
    deliver_input(group, node.inputs["Bundle"], physical_bundle, context="bundle_set() Bundle")
    deliver_input(group, node.inputs["Path"], path_value, context="bundle_set() Path")
    deliver_input(group, node.inputs["Item"], item_value, context="bundle_set() Item")
    return make_value(node.outputs["Bundle"], TYPE_BUNDLE)


def _bundle_socket_type(typ, context):
    """Map a supported NodeForge runtime type to Blender's Bundle item enum."""
    socket_type = _BUNDLE_SOCKET_TYPES.get(typ)
    if socket_type is None:
        raise CompileError(f"{context} has unsupported Bundle item type {typ}")
    return socket_type

__all__ = [
    "build_bundle",
    "build_bundle_get",
    "build_bundle_set",
    "_BUNDLE_SOCKET_TYPES",
    "_bundle_socket_type",
]
