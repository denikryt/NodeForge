"""Low-level helpers for creating and wiring Blender nodes."""

from ..semantic.constants import *
from ..type_capabilities import SWITCH_INPUT_TYPES
from .socket_types import socket_type_for_nf_type
from ..errors import CompileError
from ..semantic.numeric_semantics import normalize_float_constant, normalize_int_constant
from .values import Value, DeferredConstant
from .input_delivery import deliver_input, require_output
from .socket_types import runtime_socket_nf_type, runtime_socket_owner_tree



def _socket_type_for(typ):
    """Function `_socket_type_for` used by the NodeForge addon."""
    return socket_type_for_nf_type(typ)

def _new_node(group, bl_idname, x=0, y=0):
    """Function `_new_node` used by the NodeForge addon."""
    node = group.nodes.new(bl_idname)
    node.location = (x, y)
    return node


def _int_value(group, value, x=0, y=0):
    """Create an integer constant node for one validated NodeForge Int value."""
    value = normalize_int_constant(value)
    node = _new_node(group, "FunctionNodeInputInt", x, y)
    node.label = str(value)
    node.integer = value
    return Value(node.outputs[0], TYPE_INT)

def _value(group, value, x=0, y=0):
    """Create a Float constant node from one canonical NodeForge Float value."""
    value = normalize_float_constant(value)
    node = _new_node(group, "ShaderNodeValue", x, y)
    node.label = str(value)
    node.outputs[0].default_value = value
    return Value(node.outputs[0], TYPE_FLOAT)


def _string_value(group, value, x=0, y=0):
    """Create a runtime String value from a compile-time Python string."""
    node = _new_node(group, "FunctionNodeInputString", x, y)
    node.string = str(value)
    return Value(node.outputs[0], TYPE_STRING)

def _bool_value(group, value, x=0, y=0):
    """Create a native Bool constant node for one compile-time Bool input."""
    if type(value) is not bool:
        raise CompileError("Bool constant requires a Bool value")
    node = _new_node(group, "FunctionNodeInputBool", x, y)
    node.boolean = value
    return Value(node.outputs[0], TYPE_BOOL)


def _color_value(group, rgba, x=0, y=0):
    """Create a Blender Color input node preserving all four RGBA components."""
    from ..semantic.callable_contracts import canonicalize_color_components

    node = _new_node(group, "FunctionNodeInputColor", x, y)
    node.value = canonicalize_color_components(rgba)
    return Value(node.outputs[0], TYPE_COLOR)


def _rotation_value(group, angles, x=0, y=0):
    """Create a native Rotation constant from canonical XYZ Euler angles."""
    node = _new_node(group, "FunctionNodeInputRotation", x, y)
    node.rotation_euler = angles
    return Value(node.outputs["Rotation"], TYPE_ROTATION)


def _vector_value(group, components, x=0, y=0):
    """Create a native Vector value with canonical component defaults."""
    from ..semantic.callable_contracts import canonicalize_group_input_default

    values = canonicalize_group_input_default(TYPE_VECTOR, components)
    return _combine_xyz_mixed(group, list(values), x, y)


def _is_number_type(typ):
    """Function `_is_number_type` used by the NodeForge addon."""
    return typ in {TYPE_FLOAT, TYPE_INT}

def _math(group, operation, args, x=0, y=0):
    """Create one Float Math node for already-typed numeric operands."""
    node = _new_node(group, "ShaderNodeMath", x, y)
    node.operation = operation
    for i, arg in enumerate(args):
        if not _is_number_type(arg.typ):
            raise CompileError(f"Math operation {operation} expects numeric input")
        deliver_input(group, node.inputs[i], arg, context=f"{operation} input {i}")
    return Value(node.outputs[0], TYPE_FLOAT)


def _integer_math(group, operation, args, x=0, y=0):
    """Create one Integer Math node for already-typed NodeForge Int operands."""
    node = _new_node(group, "FunctionNodeIntegerMath", x, y)
    node.operation = operation
    for i, arg in enumerate(args):
        if arg.typ is not TYPE_INT:
            raise CompileError(f"Integer Math operation {operation} expects Int input")
        deliver_input(group, node.inputs[i], arg, context=f"{operation} input {i}")
    return Value(node.outputs[0], TYPE_INT)

def _vector_math(group, operation, args, out_type=TYPE_VECTOR, x=0, y=0):
    """Function `_vector_math` used by the NodeForge addon."""
    node = _new_node(group, "ShaderNodeVectorMath", x, y)
    node.operation = operation
    vi = 0
    for arg in args:
        if arg.typ == TYPE_VECTOR:
            if isinstance(arg, Value) and runtime_socket_nf_type(arg.socket) is TYPE_ROTATION:
                # Object Info exposes Rotation as a physical Rotation socket,
                # while its established NodeForge object property is Vector.
                # Preserve Blender's existing Rotation -> Vector field link.
                if runtime_socket_owner_tree(arg.socket) is not group or not getattr(arg.socket, "is_output", False):
                    raise CompileError(f"{operation} Vector input: foreign or non-output Rotation socket")
                group.links.new(arg.socket, node.inputs[vi])
            else:
                deliver_input(group, node.inputs[vi], arg, context=f"{operation} Vector input")
            vi += 1
        elif _is_number_type(arg.typ):
            deliver_input(group, node.inputs[3], arg, context=f"{operation} scalar input")
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
        if isinstance(val, (Value, DeferredConstant)):
            if not _is_number_type(val.typ):
                raise CompileError("vector(x, y, z) expects numeric arguments")
            deliver_input(group, node.inputs[i], val, context=f"vector component {i}")
        elif type(val) in {int, float}:
            deliver_input(group, node.inputs[i], normalize_float_constant(val), context=f"vector component {i}")
        else:
            raise CompileError("vector(x, y, z) expects numeric arguments")
    return Value(node.outputs[0], TYPE_VECTOR)

def _separate_xyz(group, val, component, x=0, y=0):
    """Function `_separate_xyz` used by the NodeForge addon."""
    if val.typ != TYPE_VECTOR:
        raise CompileError(".x/.y/.z can only be used on Vector values")
    node = _new_node(group, "ShaderNodeSeparateXYZ", x, y)
    deliver_input(group, node.inputs[0], val, context="separate xyz")
    return Value(node.outputs[{"x": 0, "y": 1, "z": 2}[component]], TYPE_FLOAT)

def _compare(group, operation, left, right, x=0, y=0):
    """Create one typed Compare node from already-resolved semantic operand types."""
    if left.typ is TYPE_INT and right.typ is TYPE_INT:
        data_type = "INT"
    elif _is_number_type(left.typ) and _is_number_type(right.typ):
        data_type = "FLOAT"
    elif left.typ == right.typ and left.typ in {TYPE_BOOL, TYPE_VECTOR}:
        data_type = {TYPE_BOOL: "INT", TYPE_VECTOR: "VECTOR"}[left.typ]
    else:
        raise CompileError("Comparison inputs must both be numeric, both Bool, or both Vector")
    node = _new_node(group, "FunctionNodeCompare", x, y)
    node.operation = operation
    node.data_type = data_type
    if data_type == "FLOAT" and operation in {"EQUAL", "NOT_EQUAL"}:
        for socket in node.inputs:
            if getattr(socket, "name", None) == "Epsilon":
                socket.default_value = 0.0
                break
    for index, value in enumerate((left, right)):
        if data_type == "INT" and value.typ is TYPE_BOOL and isinstance(value, DeferredConstant):
            # Blender represents Bool comparisons with integer inputs; this
            # conversion is specific to Compare, not a general raw socket cast.
            value = DeferredConstant(TYPE_INT, int(value.value))
        if data_type == "INT" and value.typ is TYPE_BOOL and isinstance(value, Value):
            group.links.new(value.socket, node.inputs[index])
        else:
            deliver_input(group, node.inputs[index], value, context="Compare input")
    return Value(node.outputs[0], TYPE_BOOL)

def _boolean_math(group, operation, args, x=0, y=0):
    """Function `_boolean_math` used by the NodeForge addon."""
    node = _new_node(group, "FunctionNodeBooleanMath", x, y)
    node.operation = operation
    for i, arg in enumerate(args):
        if arg.typ != TYPE_BOOL:
            raise CompileError("Boolean operations expect Bool values")
        deliver_input(group, node.inputs[i], arg, context=f"{operation} input {i}")
    return Value(node.outputs[0], TYPE_BOOL)

def _switch(group, cond, false_val, true_val, x=0, y=0):
    """Function `_switch` used by the NodeForge addon."""
    if cond.typ != TYPE_BOOL:
        raise CompileError("select(cond, true, false): cond must be Bool")
    if false_val.typ != true_val.typ:
        raise CompileError("select() true/false values must have same type")
    node = _new_node(group, "GeometryNodeSwitch", x, y)
    node.input_type = SWITCH_INPUT_TYPES[false_val.typ]
    deliver_input(group, node.inputs[0], cond, context="Switch condition")
    deliver_input(group, node.inputs[1], false_val, context="Switch false input")
    deliver_input(group, node.inputs[2], true_val, context="Switch true input")
    return Value(node.outputs[0], false_val.typ)

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

__all__ = ['_socket_type_for', '_new_node', '_value', '_string_value', '_int_value', '_bool_value', '_color_value', '_rotation_value', '_vector_value', '_is_number_type', '_math', '_integer_math', '_vector_math', '_combine_xyz', '_combine_xyz_mixed', '_separate_xyz', '_compare', '_boolean_math', '_switch', '_position', '_normal', '_index', '_id']
