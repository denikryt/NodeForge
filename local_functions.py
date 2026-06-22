"""Compilation helpers for script-local DSL functions."""

import ast
import re
import bpy

from .constants import *
from .errors import CompileError
from .library import make_library_call_node
from .consteval import _is_const_vector
from .compile_time import reject_compile_time_object


def value_type_for_const(value):
    """Infer the NodeForge type represented by a compile-time argument."""
    if _is_const_vector(value) or (
        isinstance(value, (tuple, list))
        and len(value) == 3
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
    ):
        return TYPE_VECTOR
    if isinstance(value, bool):
        return TYPE_BOOL
    if isinstance(value, int) and not isinstance(value, bool):
        return TYPE_INT
    if isinstance(value, float):
        return TYPE_FLOAT
    raise CompileError("Local function constant arguments must be numbers, booleans or vectors")


def input_call_for_type(param_name, typ):
    """Return source code that recreates a local function parameter as an input."""
    if typ == TYPE_GEOMETRY:
        return f'{param_name} = input_geometry({param_name!r})'
    if typ == TYPE_VECTOR:
        return f'{param_name} = input_vector({param_name!r})'
    if typ == TYPE_BOOL:
        return f'{param_name} = input_bool({param_name!r})'
    if typ == TYPE_INT:
        return f'{param_name} = input_int({param_name!r})'
    return f'{param_name} = input_float({param_name!r})'


def local_function_source(fn, param_types):
    """Lower a script-local function definition into a temporary group source."""
    lines = []
    for arg in fn.args.args:
        name = arg.arg
        lines.append(input_call_for_type(name, param_types[name]))
    if fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs or fn.args.defaults:
        raise CompileError("Local functions currently support only plain positional parameters without defaults")
    for stmt in fn.body:
        if isinstance(stmt, ast.Return):
            lines.append(f'output("Value", {ast.unparse(stmt.value)})')
        elif isinstance(stmt, ast.FunctionDef):
            raise CompileError("Nested function definitions are not supported")
        else:
            lines.append(ast.unparse(stmt))
    if not any(isinstance(stmt, ast.Return) for stmt in fn.body):
        raise CompileError(f"Local function {fn.name} must end with return ...")
    return "\n".join(lines)


def compile_backend_builtin_call(comp, expr, depth=0):
    """Compile a package-local Python helper exposed only while compiling source.nf."""
    name = expr.func.id
    helper = comp.backend_builtins.get(name)
    if not callable(helper):
        raise CompileError(f"Local backend helper {name} is not callable")
    return helper(comp, expr, depth)


def compile_local_function_call(comp, expr, depth=0):
    """Compile a script-local function call as a cached node group."""
    name = expr.func.id
    fn = comp.local_functions[name]
    params = [a.arg for a in fn.args.args]
    if fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs or fn.args.defaults:
        raise CompileError("Local functions currently support only plain positional parameters without defaults")
    if len(expr.args) > len(params):
        raise CompileError(f"{name}() got too many positional arguments")

    compiled_args = {}
    const_args = {}
    param_types = {}
    used = set()
    for idx, arg_expr in enumerate(expr.args):
        param = params[idx]
        value, is_dynamic = comp._const_or_compile_arg(arg_expr, depth + 1)
        if is_dynamic:
            reject_compile_time_object(value, "script-local function argument")
            if isinstance(value, list):
                raise CompileError("Local function arguments cannot be arrays")
            compiled_args[param] = value
            param_types[param] = value.typ
        else:
            const_args[param] = value
            param_types[param] = value_type_for_const(value)
        used.add(param)

    for kw in expr.keywords:
        if kw.arg is None:
            raise CompileError(f"{name}() does not support **kwargs")
        if kw.arg not in params:
            raise CompileError(f"{name}() got unknown keyword argument {kw.arg!r}")
        if kw.arg in used:
            raise CompileError(f"{name}() got multiple values for argument {kw.arg!r}")
        value, is_dynamic = comp._const_or_compile_arg(kw.value, depth + 1)
        if is_dynamic:
            reject_compile_time_object(value, "script-local function argument")
            if isinstance(value, list):
                raise CompileError("Local function arguments cannot be arrays")
            compiled_args[kw.arg] = value
            param_types[kw.arg] = value.typ
        else:
            const_args[kw.arg] = value
            param_types[kw.arg] = value_type_for_const(value)
        used.add(kw.arg)

    missing = [p for p in params if p not in used]
    if missing:
        raise CompileError(f"{name}() missing arguments: {', '.join(missing)}")

    signature = ",".join(param_types[p] for p in params)
    safe_parent = re.sub(r"[^A-Za-z0-9_]+", "_", getattr(comp.group, "name", "Group"))
    group_name = f"NodeForge.local.{safe_parent}.{name}.{signature}"
    source = local_function_source(fn, param_types)
    cache_key = (name, signature, source)
    function_group = comp.local_group_cache.get(cache_key)
    if function_group is None or getattr(function_group, "name", None) not in bpy.data.node_groups:
        existing = bpy.data.node_groups.get(group_name)
        if existing is not None and getattr(existing, "bl_idname", None) == "GeometryNodeTree":
            function_group = comp.compile_group_callback(
                source,
                group_name,
                existing_group=existing,
                local_functions=comp.local_functions,
                backend_builtins=comp.backend_builtins,
            )
        else:
            function_group = comp.compile_group_callback(
                source,
                group_name,
                local_functions=comp.local_functions,
                backend_builtins=comp.backend_builtins,
            )
        try:
            function_group["nodeforge_local_function_name"] = name
            function_group["nodeforge_local_function_source"] = source
        except Exception:
            pass
        comp.local_group_cache[cache_key] = function_group
    x = depth * 240
    y = -depth * 90
    return make_library_call_node(comp.group, function_group, compiled_args, const_args, x=x, y=y)


__all__ = [
    "value_type_for_const",
    "input_call_for_type",
    "local_function_source",
    "compile_backend_builtin_call",
    "compile_local_function_call",
]
