"""Adapters for calls into NodeForge function-library entries."""

from .errors import CompileError
from .nodes import _new_node
from .library import (
    has_native_compile_call,
    compile_module_library_function_call,
    get_or_create_library_group,
    make_library_call_node,
    _normalized_socket_name,
)


def compile_library_function_call(comp, expr, depth=0):
    """Compile a function-library call without owning library discovery/materialization."""
    name = expr.func.id
    if has_native_compile_call(name):
        return compile_module_library_function_call(comp, expr, depth)
    x = depth * 240
    y = -depth * 90
    function_group = get_or_create_library_group(name, comp.compile_group_callback)

    probe = _new_node(comp.group, "GeometryNodeGroup", x, y)
    probe.node_tree = function_group
    input_names = [s.name for s in probe.inputs if getattr(s, "enabled", True)]
    comp.group.nodes.remove(probe)

    if len(expr.args) > len(input_names):
        raise CompileError(f"{name}() got too many positional arguments")

    compiled_args = {}
    const_args = {}
    used = set()
    for idx, arg_expr in enumerate(expr.args):
        socket_name = input_names[idx]
        value, is_dynamic = comp._const_or_compile_arg(arg_expr, depth + 1)
        if is_dynamic:
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
        if is_dynamic:
            compiled_args[socket_name] = value
        else:
            const_args[socket_name] = value
        used.add(key)

    return make_library_call_node(comp.group, function_group, compiled_args, const_args, x=x, y=y)


__all__ = ["compile_library_function_call"]
