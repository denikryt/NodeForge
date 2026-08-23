"""Helpers for top-level script statements such as store(), output(), and set_position()."""

from .constants import *
from .errors import CompileError
from .nodes import _new_node
from .parsing import _literal_string
from .compile_time import reject_compile_time_object
from .values import Value, reject_tuple_value

_ALLOWED_STORE_TYPES = {
    "FLOAT": "FLOAT",
    "INT": "INT",
    "INTEGER": "INT",
    "VECTOR": "FLOAT_VECTOR",
    "FLOAT_VECTOR": "FLOAT_VECTOR",
    "COLOR": "FLOAT_COLOR",
    "RGBA": "FLOAT_COLOR",
    "FLOAT_COLOR": "FLOAT_COLOR",
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
}
_ALLOWED_DOMAINS = {"POINT", "EDGE", "FACE", "CORNER", "CURVE", "INSTANCE"}

def _attribute_data_type(typ):
    """Function `_attribute_data_type` used by the NodeForge addon."""
    return {
        TYPE_FLOAT: "FLOAT",
        TYPE_INT: "INT",
        TYPE_VECTOR: "FLOAT_VECTOR",
        TYPE_BOOL: "BOOLEAN",
    }.get(typ)

def _attribute_domain(domain, context):
    """Return a validated Geometry Nodes attribute domain token."""
    normalized = (domain or "POINT").upper()
    if normalized not in _ALLOWED_DOMAINS:
        raise CompileError(f"Unsupported {context} domain. Use POINT, EDGE, FACE, CORNER, CURVE or INSTANCE")
    return normalized

def _kw_dict(call):
    """Function `_kw_dict` used by the NodeForge addon."""
    result = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise CompileError("**kwargs are not supported")
        if kw.arg in result:
            raise CompileError(f"Duplicate keyword argument: {kw.arg}")
        result[kw.arg] = kw.value
    return result

def _optional_string_kw(kws, name, default=None, consts=None):
    """Return an optional keyword value as a non-empty compile-time string."""
    if name not in kws:
        return default
    return _literal_string(kws[name], f"{name}=", consts)

def _selection_kw(comp, kws, default=None):
    """Function `_selection_kw` used by the NodeForge addon."""
    if "selection" not in kws:
        return default
    selection = comp.compile(kws["selection"])
    reject_compile_time_object(selection, "selection= expression")
    if selection.typ != TYPE_BOOL:
        raise CompileError("selection= must be a Bool expression")
    return selection

def _check_no_extra_keywords(kws, allowed):
    """Function `_check_no_extra_keywords` used by the NodeForge addon."""
    extra = set(kws) - set(allowed)
    if extra:
        raise CompileError("Unsupported keyword argument(s): " + ", ".join(sorted(extra)))


def _string_value_or_literal(comp, expr, context):
    """Return a compile-time string or a runtime String value for a socket argument."""
    try:
        return _literal_string(expr, context, comp.consts)
    except CompileError:
        value = comp.compile(expr)
        reject_compile_time_object(value, context)
        reject_tuple_value(value, context)
        if not isinstance(value, Value) or value.typ != TYPE_STRING:
            actual = getattr(value, "typ", type(value).__name__)
            raise CompileError(f"{context} must be a compile-time string or runtime String, got {actual}")
        return value

def _store_named_attribute(group, geometry_socket, attr_name, value, selection=None, domain="POINT", data_type_override=None, x=0, y=0):
    """Function `_store_named_attribute` used by the NodeForge addon."""
    if data_type_override:
        data_type = _ALLOWED_STORE_TYPES.get(data_type_override.upper())
        if data_type is None:
            raise CompileError("Unsupported store() type. Use FLOAT, INT, VECTOR, COLOR or BOOLEAN")
    else:
        data_type = _attribute_data_type(value.typ)
    if data_type is None:
        raise CompileError("store(name, value) supports Float, Int, Vector and Bool values")
    domain = _attribute_domain(domain, "store()")
    node = _new_node(group, "GeometryNodeStoreNamedAttribute", x, y)
    node.data_type = data_type
    try:
        node.domain = domain
    except Exception:
        pass
    node.inputs[1].default_value = True
    if isinstance(attr_name, Value):
        if attr_name.typ != TYPE_STRING:
            raise CompileError("store() attribute name must be String")
        group.links.new(attr_name.socket, node.inputs[2])
    else:
        node.inputs[2].default_value = attr_name
    group.links.new(geometry_socket, node.inputs[0])
    if selection is not None:
        group.links.new(selection.socket, node.inputs[1])
    group.links.new(value.socket, node.inputs[3])
    return node.outputs[0]

def _set_position_node(group, geometry_socket, pos, selection=None, x=0, y=0):
    """Function `_set_position_node` used by the NodeForge addon."""
    if pos.typ != TYPE_VECTOR:
        raise CompileError("set_position() expects a Vector argument")
    node = _new_node(group, "GeometryNodeSetPosition", x, y)
    group.links.new(geometry_socket, node.inputs[0])
    if selection is not None:
        group.links.new(selection.socket, node.inputs[1])
    group.links.new(pos.socket, node.inputs[2])
    return node.outputs[0]

def _unique_output_name(existing, requested):
    """Function `_unique_output_name` used by the NodeForge addon."""
    base = requested or "out"
    name = base
    idx = 2
    while name in existing:
        name = f"{base}_{idx}"
        idx += 1
    existing.add(name)
    return name

__all__ = ['_attribute_data_type', '_attribute_domain', '_kw_dict', '_optional_string_kw', '_selection_kw', '_check_no_extra_keywords', '_string_value_or_literal', '_store_named_attribute', '_set_position_node', '_unique_output_name']
