"""Low-level helpers for creating and wiring Blender nodes."""

from .constants import *
from .errors import CompileError
from .values import Value



def _socket_type_for(typ):
    """Function `_socket_type_for` used by the NodeForge addon."""
    return {
        TYPE_FLOAT: "NodeSocketFloat",
        TYPE_VECTOR: "NodeSocketVector",
        TYPE_BOOL: "NodeSocketBool",
        TYPE_GEOMETRY: "NodeSocketGeometry",
        TYPE_INT: "NodeSocketInt",
        TYPE_MATERIAL: "NodeSocketMaterial",
        TYPE_OBJECT: "NodeSocketObject",
        TYPE_STRING: "NodeSocketString",
    }[typ]

def _new_node(group, bl_idname, x=0, y=0):
    """Function `_new_node` used by the NodeForge addon."""
    node = group.nodes.new(bl_idname)
    node.location = (x, y)
    return node


def _int_value(group, value, x=0, y=0):
    """Create an integer constant node for sockets that require Int values."""
    node = _new_node(group, "FunctionNodeInputInt", x, y)
    node.label = str(value)
    node.integer = int(value)
    return Value(node.outputs[0], TYPE_INT)

def _value(group, value, x=0, y=0):
    """Function `_value` used by the NodeForge addon."""
    node = _new_node(group, "ShaderNodeValue", x, y)
    node.label = str(value)
    node.outputs[0].default_value = float(value)
    return Value(node.outputs[0], TYPE_FLOAT)


def _string_value(group, value, x=0, y=0):
    """Create a runtime String value from a compile-time Python string."""
    node = _new_node(group, "FunctionNodeInputString", x, y)
    node.string = str(value)
    return Value(node.outputs[0], TYPE_STRING)

def _is_number_type(typ):
    """Function `_is_number_type` used by the NodeForge addon."""
    return typ in {TYPE_FLOAT, TYPE_INT}

def _math(group, operation, args, x=0, y=0):
    """Function `_math` used by the NodeForge addon."""
    node = _new_node(group, "ShaderNodeMath", x, y)
    node.operation = operation
    for i, arg in enumerate(args):
        if not _is_number_type(arg.typ):
            raise CompileError(f"Math operation {operation} expects numeric input")
        group.links.new(arg.socket, node.inputs[i])
    return Value(node.outputs[0], TYPE_FLOAT)

def _vector_math(group, operation, args, out_type=TYPE_VECTOR, x=0, y=0):
    """Function `_vector_math` used by the NodeForge addon."""
    node = _new_node(group, "ShaderNodeVectorMath", x, y)
    node.operation = operation
    vi = 0
    for arg in args:
        if arg.typ == TYPE_VECTOR:
            group.links.new(arg.socket, node.inputs[vi])
            vi += 1
        elif _is_number_type(arg.typ):
            group.links.new(arg.socket, node.inputs[3])
        else:
            raise CompileError(f"Vector Math {operation} got unsupported type {arg.typ}")
    return Value(node.outputs[1 if out_type == TYPE_FLOAT else 0], out_type)

def _combine_xyz(group, xval, yval, zval, x=0, y=0):
    """Function `_combine_xyz` used by the NodeForge addon."""
    return _combine_xyz_mixed(group, [xval, yval, zval], x, y)

def _combine_xyz_mixed(group, comps, x=0, y=0):
    """Create a vector from numeric Values and/or compile-time numeric constants.

    Static components are written into the Combine XYZ socket defaults instead of
    creating separate Value nodes. This matters for scale=vector(scale, scale, 1)
    and similar per-axis copy_by_offsets controls.
    """
    if len(comps) != 3:
        raise CompileError("vector(x, y, z) expects 3 arguments")
    node = _new_node(group, "ShaderNodeCombineXYZ", x, y)
    for i, val in enumerate(comps):
        if isinstance(val, Value):
            if not _is_number_type(val.typ):
                raise CompileError("vector(x, y, z) expects numeric arguments")
            group.links.new(val.socket, node.inputs[i])
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            node.inputs[i].default_value = float(val)
        else:
            raise CompileError("vector(x, y, z) expects numeric arguments")
    return Value(node.outputs[0], TYPE_VECTOR)

def _separate_xyz(group, val, component, x=0, y=0):
    """Function `_separate_xyz` used by the NodeForge addon."""
    if val.typ != TYPE_VECTOR:
        raise CompileError(".x/.y/.z can only be used on Vector values")
    node = _new_node(group, "ShaderNodeSeparateXYZ", x, y)
    group.links.new(val.socket, node.inputs[0])
    return Value(node.outputs[{"x": 0, "y": 1, "z": 2}[component]], TYPE_FLOAT)

def _compare(group, operation, left, right, x=0, y=0):
    """Function `_compare` used by the NodeForge addon."""
    if _is_number_type(left.typ) and _is_number_type(right.typ):
        data_type = "FLOAT"
    elif left.typ == right.typ and left.typ in {TYPE_BOOL, TYPE_VECTOR}:
        data_type = {TYPE_BOOL: "BOOLEAN", TYPE_VECTOR: "VECTOR"}[left.typ]
    else:
        raise CompileError("Comparison inputs must both be numeric, both Bool, or both Vector")
    node = _new_node(group, "FunctionNodeCompare", x, y)
    node.operation = operation
    node.data_type = data_type
    group.links.new(left.socket, node.inputs[0])
    group.links.new(right.socket, node.inputs[1])
    return Value(node.outputs[0], TYPE_BOOL)

def _boolean_math(group, operation, args, x=0, y=0):
    """Function `_boolean_math` used by the NodeForge addon."""
    node = _new_node(group, "FunctionNodeBooleanMath", x, y)
    node.operation = operation
    for i, arg in enumerate(args):
        if arg.typ != TYPE_BOOL:
            raise CompileError("Boolean operations expect Bool values")
        group.links.new(arg.socket, node.inputs[i])
    return Value(node.outputs[0], TYPE_BOOL)

def _switch(group, cond, false_val, true_val, x=0, y=0):
    """Function `_switch` used by the NodeForge addon."""
    if cond.typ != TYPE_BOOL:
        raise CompileError("select(cond, true, false): cond must be Bool")
    if false_val.typ != true_val.typ:
        raise CompileError("select() true/false values must have same type")
    node = _new_node(group, "GeometryNodeSwitch", x, y)
    node.input_type = {
        TYPE_FLOAT: "FLOAT",
        TYPE_INT: "INT",
        TYPE_VECTOR: "VECTOR",
        TYPE_BOOL: "BOOLEAN",
        TYPE_GEOMETRY: "GEOMETRY",
        TYPE_STRING: "STRING",
    }[false_val.typ]
    group.links.new(cond.socket, node.inputs[0])
    group.links.new(false_val.socket, node.inputs[1])
    group.links.new(true_val.socket, node.inputs[2])
    return Value(node.outputs[0], false_val.typ)

def _mix(group, a, b, factor, x=0, y=0):
    """Function `_mix` used by the NodeForge addon."""
    if a.typ != b.typ or a.typ not in {TYPE_FLOAT, TYPE_VECTOR}:
        raise CompileError("mix(a, b, factor) supports Float or Vector a/b of same type")
    if not _is_number_type(factor.typ):
        raise CompileError("mix() factor must be numeric")
    node = _new_node(group, "ShaderNodeMix", x, y)
    node.data_type = "VECTOR" if a.typ == TYPE_VECTOR else "FLOAT"
    node.factor_mode = "UNIFORM"
    node.clamp_factor = True
    group.links.new(factor.socket, node.inputs[0])
    if a.typ == TYPE_FLOAT:
        group.links.new(a.socket, node.inputs[2]); group.links.new(b.socket, node.inputs[3]); out_i = 0
    else:
        group.links.new(a.socket, node.inputs[4]); group.links.new(b.socket, node.inputs[5]); out_i = 1
    return Value(node.outputs[out_i], a.typ)

def _clamp(group, val, minv, maxv, x=0, y=0):
    """Function `_clamp` used by the NodeForge addon."""
    if not (_is_number_type(val.typ) and _is_number_type(minv.typ) and _is_number_type(maxv.typ)):
        raise CompileError("clamp(value, min, max) expects numeric arguments")
    node = _new_node(group, "ShaderNodeClamp", x, y)
    group.links.new(val.socket, node.inputs[0]); group.links.new(minv.socket, node.inputs[1]); group.links.new(maxv.socket, node.inputs[2])
    return Value(node.outputs[0], TYPE_FLOAT)

def _position(group, x=0, y=0):
    """Function `_position` used by the NodeForge addon."""
    node = _new_node(group, "GeometryNodeInputPosition", x, y)
    return Value(node.outputs[0], TYPE_VECTOR)

def _normal(group, x=0, y=0):
    """Function `_normal` used by the NodeForge addon."""
    node = _new_node(group, "GeometryNodeInputNormal", x, y)
    return Value(node.outputs[0], TYPE_VECTOR)

def _index(group, x=0, y=0):
    """Function `_index` used by the NodeForge addon."""
    node = _new_node(group, "GeometryNodeInputIndex", x, y)
    return Value(node.outputs[0], TYPE_INT)

def _id(group, x=0, y=0):
    """Function `_id` used by the NodeForge addon."""
    node = _new_node(group, "GeometryNodeInputID", x, y)
    return Value(node.outputs[0], TYPE_INT)

def _map_range(group, args, x=0, y=0):
    """Function `_map_range` used by the NodeForge addon."""
    if len(args) != 5:
        raise CompileError("map_range(value, from_min, from_max, to_min, to_max) expects 5 arguments")
    if not all(_is_number_type(arg.typ) for arg in args):
        raise CompileError("map_range() currently supports numeric arguments")
    node = _new_node(group, "ShaderNodeMapRange", x, y)
    node.data_type = "FLOAT"
    for i, arg in enumerate(args):
        group.links.new(arg.socket, node.inputs[i])
    return Value(node.outputs[0], TYPE_FLOAT)

def _ensure_float(v):
    """Function `_ensure_float` used by the NodeForge addon."""
    if v.typ != TYPE_FLOAT:
        raise CompileError("Expected Float")

__all__ = ['_socket_type_for', '_new_node', '_value', '_string_value', '_int_value', '_is_number_type', '_math', '_vector_math', '_combine_xyz', '_combine_xyz_mixed', '_separate_xyz', '_compare', '_boolean_math', '_switch', '_mix', '_clamp', '_position', '_normal', '_index', '_id', '_map_range', '_ensure_float']
