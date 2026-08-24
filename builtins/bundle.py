"""Runtime Blender Bundle construction and item access for the NodeForge DSL."""

from __future__ import annotations

import ast

from ..compile_time import reject_compile_time_object
from ..constants import (
    TYPE_BOOL,
    TYPE_BUNDLE,
    TYPE_FLOAT,
    TYPE_GEOMETRY,
    TYPE_INT,
    TYPE_MATERIAL,
    TYPE_OBJECT,
    TYPE_STRING,
    TYPE_TOKEN_NAMES,
    TYPE_VECTOR,
)
from ..errors import CompileError
from ..nodes import _new_node
from ..values import Value, make_value, reject_tuple_value

NAMES = {"bundle", "bundle_get", "bundle_set"}

_BUNDLE_SOCKET_TYPES = {
    TYPE_FLOAT: "FLOAT",
    TYPE_INT: "INT",
    TYPE_BOOL: "BOOLEAN",
    TYPE_VECTOR: "VECTOR",
    TYPE_GEOMETRY: "GEOMETRY",
    TYPE_MATERIAL: "MATERIAL",
    TYPE_OBJECT: "OBJECT",
    TYPE_STRING: "STRING",
    TYPE_BUNDLE: "BUNDLE",
}


def compile_call(comp, expr, depth=0):
    """Compile one public Bundle built-in call."""
    name = expr.func.id
    if name == "bundle":
        return _compile_bundle(comp, expr, depth)
    if name == "bundle_get":
        return _compile_bundle_get(comp, expr, depth)
    if name == "bundle_set":
        return _compile_bundle_set(comp, expr, depth)
    raise CompileError(f"Unsupported Bundle builtin: {name}")


def _compile_bundle(comp, expr, depth):
    """Lower ``bundle(**named_items)`` to Blender's Combine Bundle node."""
    if expr.args:
        raise CompileError("bundle(...) accepts named keyword items only")
    if any(keyword.arg is None for keyword in expr.keywords):
        raise CompileError("bundle(...) does not support **kwargs")

    values = []
    seen_names = set()
    for keyword in expr.keywords:
        item_name = keyword.arg
        if item_name in seen_names:
            raise CompileError(f"bundle() has duplicate item name {item_name!r}")
        seen_names.add(item_name)
        value = comp.compile(keyword.value)
        value = _require_runtime_value(value, f"bundle() item {item_name!r}")
        values.append((item_name, value))

    node = _new_node(comp.group, "NodeCombineBundle", depth * 240, -depth * 90)
    try:
        for item_name, value in values:
            socket_type = _bundle_socket_type(value.typ, f"bundle() item {item_name!r}")
            node.bundle_items.new(socket_type, item_name)
    except Exception as exc:
        raise CompileError(f"bundle(): failed to define Combine Bundle items: {exc}") from exc

    for index, (item_name, value) in enumerate(values):
        try:
            socket = node.inputs[index]
        except Exception as exc:
            raise CompileError(f"bundle(): missing generated input for item {item_name!r}") from exc
        if getattr(socket, "name", None) != item_name:
            raise CompileError(
                f"bundle(): generated input order mismatch for {item_name!r}; got {getattr(socket, 'name', None)!r}"
            )
        try:
            comp.group.links.new(value.socket, socket)
        except Exception as exc:
            raise CompileError(f"bundle(): failed to link item {item_name!r}: {exc}") from exc

    return make_value(node.outputs["Bundle"], TYPE_BUNDLE)


def _compile_bundle_get(comp, expr, depth):
    """Lower ``bundle_get(bundle, path, typ=Type)`` to Get Bundle Item."""
    if len(expr.args) != 2:
        raise CompileError("bundle_get(bundle, path, typ=...) expects exactly two positional arguments")
    if any(keyword.arg is None for keyword in expr.keywords):
        raise CompileError("bundle_get(...) does not support **kwargs")
    keywords = {keyword.arg: keyword.value for keyword in expr.keywords}
    if set(keywords) != {"typ"}:
        raise CompileError("bundle_get(...) requires exactly one keyword argument: typ=")

    bundle_value = _require_runtime_value(comp.compile(expr.args[0]), "bundle_get() bundle")
    if bundle_value.typ != TYPE_BUNDLE:
        raise CompileError(f"bundle_get() first argument expects Bundle, got {bundle_value.typ}")
    path_value = _compile_path(comp, expr.args[1], "bundle_get() path")
    output_type = _parse_type_token(keywords["typ"], "bundle_get() typ=")
    socket_type = _bundle_socket_type(output_type, "bundle_get() typ=")

    node = _new_node(comp.group, "NodeGetBundleItem", depth * 240, -depth * 90)
    try:
        node.socket_type = socket_type
        comp.group.links.new(bundle_value.socket, node.inputs["Bundle"])
        comp.group.links.new(path_value.socket, node.inputs["Path"])
    except Exception as exc:
        raise CompileError(f"bundle_get(): failed to configure Get Bundle Item: {exc}") from exc
    return make_value(node.outputs["Item"], output_type)


def _compile_bundle_set(comp, expr, depth):
    """Lower ``bundle_set(bundle, path, value)`` to Store Bundle Item."""
    if len(expr.args) != 3:
        raise CompileError("bundle_set(bundle, path, value) expects exactly three positional arguments")
    if expr.keywords:
        raise CompileError("bundle_set(...) does not accept keyword arguments")

    bundle_value = _require_runtime_value(comp.compile(expr.args[0]), "bundle_set() bundle")
    if bundle_value.typ != TYPE_BUNDLE:
        raise CompileError(f"bundle_set() first argument expects Bundle, got {bundle_value.typ}")
    path_value = _compile_path(comp, expr.args[1], "bundle_set() path")
    item_value = _require_runtime_value(comp.compile(expr.args[2]), "bundle_set() value")
    socket_type = _bundle_socket_type(item_value.typ, "bundle_set() value")

    node = _new_node(comp.group, "NodeStoreBundleItem", depth * 240, -depth * 90)
    try:
        node.socket_type = socket_type
        comp.group.links.new(bundle_value.socket, node.inputs["Bundle"])
        comp.group.links.new(path_value.socket, node.inputs["Path"])
        comp.group.links.new(item_value.socket, node.inputs["Item"])
    except Exception as exc:
        raise CompileError(f"bundle_set(): failed to configure Store Bundle Item: {exc}") from exc
    return make_value(node.outputs["Bundle"], TYPE_BUNDLE)


def _compile_path(comp, expr, context):
    """Compile a Bundle path expression and require a runtime String value."""
    value = _require_runtime_value(comp.compile(expr), context)
    if value.typ != TYPE_STRING:
        raise CompileError(f"{context} expects String, got {value.typ}")
    return value


def _require_runtime_value(value, context):
    """Return one ordinary runtime Value or raise a controlled Bundle diagnostic."""
    reject_compile_time_object(value, context)
    reject_tuple_value(value, context)
    if isinstance(value, list):
        raise CompileError(f"{context} cannot be a script array")
    if not isinstance(value, Value):
        raise CompileError(f"{context} expects a runtime node value")
    return value


def _parse_type_token(expr, context):
    """Resolve one NodeForge type token used by Bundle item access."""
    if not isinstance(expr, ast.Name) or expr.id not in TYPE_TOKEN_NAMES:
        raise CompileError(f"{context} must be one of: {', '.join(sorted(TYPE_TOKEN_NAMES))}")
    return TYPE_TOKEN_NAMES[expr.id]


def _bundle_socket_type(typ, context):
    """Map a supported NodeForge runtime type to Blender's Bundle item enum."""
    socket_type = _BUNDLE_SOCKET_TYPES.get(typ)
    if socket_type is None:
        raise CompileError(f"{context} has unsupported Bundle item type {typ}")
    return socket_type


__all__ = [
    "NAMES",
    "compile_call",
    "_BUNDLE_SOCKET_TYPES",
    "_bundle_socket_type",
    "_parse_type_token",
]
