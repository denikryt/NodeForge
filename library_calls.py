"""Adapters for calls into NodeForge library catalog entries."""

from .constants import TYPE_BOOL, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_VECTOR
from .consteval import _is_const_vector
from .errors import CompileError
from .values import Value
from .compile_time import reject_compile_time_object
from .library import (
    has_native_compile_call,
    compile_module_library_entry_call,
    get_or_create_library_entry_group,
    make_library_call_node,
    _normalized_socket_name,
    _socket_type_to_value_type,
)


def _const_arg_type(value):
    """Return the NodeForge semantic type represented by one constant argument."""
    if _is_const_vector(value) or (
        isinstance(value, (tuple, list))
        and len(value) == 3
        and all(isinstance(component, (int, float)) and not isinstance(component, bool) for component in value)
    ):
        return TYPE_VECTOR
    if isinstance(value, bool):
        return TYPE_BOOL
    if isinstance(value, int):
        return TYPE_INT
    if isinstance(value, float):
        return TYPE_FLOAT
    return None


def _argument_type_matches(expected_type, actual_type):
    """Return True when a function argument may be wired to a group input."""
    if actual_type is None:
        return False
    if expected_type == TYPE_FLOAT:
        return actual_type in {TYPE_FLOAT, TYPE_INT}
    if expected_type == TYPE_INT:
        return actual_type == TYPE_INT
    return actual_type == expected_type


def _validate_argument_type(function_name, socket_name, expected_type, value):
    """Raise a controlled error when a library call argument has the wrong type."""
    actual_type = value.typ if isinstance(value, Value) else _const_arg_type(value)
    if not _argument_type_matches(expected_type, actual_type):
        raise CompileError(
            f"{function_name}() input {socket_name!r} expects {expected_type}, got {actual_type or type(value).__name__}"
        )


def compile_library_function_call(comp, expr, depth=0, function_name=None, namespace="functions", binding=None):
    """Compile a namespace-aware library call without owning discovery."""
    if binding is not None:
        namespace = binding.namespace
        name = binding.canonical_name
    else:
        name = function_name or expr.func.id
    if has_native_compile_call(namespace, name):
        return compile_module_library_entry_call(comp, expr, depth, namespace=namespace, entry_name=name)
    x = depth * 240
    y = -depth * 90
    function_group = get_or_create_library_entry_group(namespace, name, comp.compile_group_callback)

    probe = comp.group.nodes.new("GeometryNodeGroup")
    probe.location = (x, y)
    probe.node_tree = function_group
    input_sockets = [s for s in probe.inputs if getattr(s, "enabled", True)]
    input_names = [s.name for s in input_sockets]
    input_types = {s.name: _socket_type_to_value_type(s) for s in input_sockets}
    comp.group.nodes.remove(probe)

    if len(expr.args) > len(input_names):
        raise CompileError(f"{name}() got too many positional arguments")

    compiled_args = {}
    const_args = {}
    used = set()
    for idx, arg_expr in enumerate(expr.args):
        socket_name = input_names[idx]
        value, is_dynamic = comp._const_or_compile_arg(arg_expr, depth + 1)
        _validate_argument_type(name, socket_name, input_types[socket_name], value)
        if is_dynamic:
            reject_compile_time_object(value, "function-library argument")
            compiled_args[socket_name] = value
        else:
            const_args[socket_name] = value
        used.add(_normalized_socket_name(socket_name))

    normalized_inputs = {_normalized_socket_name(n): n for n in input_names}
    for kw in expr.keywords:
        if kw.arg is None:
            raise CompileError(f"{name}() does not support **kwargs")
        key = _normalized_socket_name(kw.arg)
        if key not in normalized_inputs:
            raise CompileError(f"{name}() got unknown keyword argument {kw.arg!r}")
        if key in used:
            raise CompileError(f"{name}() got multiple values for input {kw.arg!r}")
        socket_name = normalized_inputs[key]
        value, is_dynamic = comp._const_or_compile_arg(kw.value, depth + 1)
        _validate_argument_type(name, socket_name, input_types[socket_name], value)
        if is_dynamic:
            reject_compile_time_object(value, "function-library argument")
            compiled_args[socket_name] = value
        else:
            const_args[socket_name] = value
        used.add(key)

    return make_library_call_node(comp.group, function_group, compiled_args, const_args, x=x, y=y)


__all__ = ["compile_library_function_call"]
