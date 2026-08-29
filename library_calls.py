"""Adapters for calls into NodeForge library catalog entries."""

from .constants import TYPE_BOOL, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_VECTOR, TYPE_MATERIAL, TYPE_OBJECT, TYPE_STRING
from .consteval import _is_const_vector
from .errors import CompileError
from .values import Value
from .compile_time import reject_compile_time_object
from .compiler_identities import library_function_id, normalize_library_package_id
from .library import (
    has_native_compile_call,
    find_library_entry_record,
    compile_module_library_entry_call,
    get_or_create_library_entry_group,
    make_library_call_node,
    _normalized_socket_name,
    _socket_type_to_value_type,
)
from .function_instances import (
    FunctionCallModifiers,
    FUNCTION_INSTANCE_KEY_PROP,
    function_group_owner_scope,
    instance_key_for,
    unsupported_unique,
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
    if isinstance(value, str):
        return TYPE_STRING
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


def _supports_unique_function_group(namespace: str, name: str) -> bool:
    """Return whether an imported expression call has reusable editable groups."""
    if namespace not in {"functions", "examples"}:
        return False
    record = find_library_entry_record(namespace, name)
    return bool(record is not None and record.source_path is not None and not has_native_compile_call(namespace, name))


def compile_library_function_call(comp, expr, depth=0, function_name=None, namespace="functions", binding=None, modifiers=None):
    """Compile a namespace-aware library call without owning discovery."""
    modifiers = modifiers or FunctionCallModifiers()
    if binding is not None:
        namespace = binding.namespace
        name = binding.canonical_name
    else:
        name = function_name or expr.func.id
    if modifiers.unique_was_explicit and not _supports_unique_function_group(namespace, name):
        raise unsupported_unique(name)
    if has_native_compile_call(namespace, name):
        return compile_module_library_entry_call(comp, expr, depth, namespace=namespace, entry_name=name)
    x = depth * 240
    y = -depth * 90
    record = find_library_entry_record(namespace, name)
    instance_key = ""
    owner_scope = None
    function_id = None
    if record is not None:
        # CANONICAL_CALL_ID_MIGRATION: Imported reusable calls still resolve through the
        # legacy AST/library dispatcher. Construct their canonical FunctionId at this
        # boundary without changing call behavior. Remove this bridge when semantic call
        # resolution owns imported callable identity before function-group materialization.
        function_id = library_function_id(namespace, record.package_id, name)
        if namespace in {"functions", "examples"}:
            call_site = comp.next_unique_function_call_site(function_id) if modifiers.unique else None
            instance_key = instance_key_for(call_site) if call_site is not None else ""
            owner_scope = function_group_owner_scope(
                "LIBRARY",
                namespace,
                normalize_library_package_id(record.package_id),
                name,
                instance_key=instance_key,
            )
    cache_key = ("catalog", namespace, name)
    function_group = comp.local_group_cache.get(cache_key) if namespace == "local" else None
    if function_group is None:
        function_group = get_or_create_library_entry_group(
            namespace,
            name,
            comp.compile_group_callback,
            instance_key=instance_key,
            owner_scope=owner_scope,
            function_id=function_id,
            function_group_cache=getattr(comp, "function_group_cache", None),
            function_group_transaction=getattr(comp, "function_group_transaction", None),
            function_compilation_trace=getattr(comp, "function_compilation_trace", None),
        )
        if namespace == "local":
            # Reuse one freshly materialized Local dependency within this group
            # build while keeping separate outer compilations fully independent.
            comp.local_group_cache[cache_key] = function_group

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

    result = make_library_call_node(comp.group, function_group, compiled_args, const_args, x=x, y=y)
    if modifiers.unique:
        for node in reversed(list(comp.group.nodes)):
            if getattr(node, "bl_idname", None) == "GeometryNodeGroup" and getattr(node, "node_tree", None) is function_group:
                try:
                    node[FUNCTION_INSTANCE_KEY_PROP] = instance_key
                except Exception:
                    pass
                break
    return result


__all__ = ["compile_library_function_call"]
