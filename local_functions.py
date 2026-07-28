"""Compilation helpers for script-local DSL functions."""

import ast
import re
import bpy

from .constants import *
from .errors import CompileError
from .library import make_library_call_node
from .consteval import _is_const_vector
from .compile_time import reject_compile_time_object

LOCAL_HELPER_KIND_PROP = "nodeforge_generated_kind"
LOCAL_HELPER_KIND = "local_function_helper"
LOCAL_HELPER_NAMESPACE_PROP = "nodeforge_local_function_namespace"
LOCAL_HELPER_NAME_PROP = "nodeforge_local_function_name"
LOCAL_HELPER_SIGNATURE_PROP = "nodeforge_local_function_signature"
LOCAL_HELPER_SOURCE_PROP = "nodeforge_local_function_source"


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
    if typ == TYPE_MATERIAL:
        return f'{param_name} = input_material({param_name!r})'
    if typ == TYPE_OBJECT:
        return f'{param_name} = input_object({param_name!r})'
    if typ == TYPE_VECTOR:
        return f'{param_name} = input_vector({param_name!r})'
    if typ == TYPE_BOOL:
        return f'{param_name} = input_bool({param_name!r})'
    if typ == TYPE_INT:
        return f'{param_name} = input_int({param_name!r})'
    return f'{param_name} = input_float({param_name!r})'


def local_function_source(fn, param_types, hidden_captures=()):
    """Lower a script-local function definition into a temporary group source."""
    lines = []
    for arg in fn.args.args:
        name = arg.arg
        lines.append(input_call_for_type(name, param_types[name]))
    for name in hidden_captures:
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


def _logical_namespace(name):
    """Return the stable, non-lossy parent identity used for helper ownership."""
    return str(name or "Group")


def _helper_namespace_fragment(namespace):
    """Return a reversible group-name fragment without collapsing identities.

    Keep legacy spelling for simple names, but escape separator and punctuation
    characters so distinct parent identities such as ``A.B`` and ``A_B`` never
    resolve to the same helper group name.
    """
    raw = _logical_namespace(namespace)
    encoded = []
    for char in raw:
        if char.isascii() and (char.isalnum() or char == "_"):
            encoded.append(char)
        else:
            encoded.append(f"_u{ord(char):06X}_")
    return "".join(encoded) or "Group"


def _reserved_capture_label(comp, name):
    """Return a user-facing label when a free name is reserved by DSL behavior."""
    label = getattr(comp, "reserved_name_labels", {}).get(name)
    if label == "DSL builtin":
        return "reserved by DSL builtin"
    if label == "imported function":
        return "already registered as imported function"
    if label == "local function":
        return "already registered as local function"
    if label == "type token":
        return "reserved by type token"
    if label is not None:
        return f"reserved by {label}"
    return None


def _binding_names_in_local_function(fn):
    """Return names local to a script-local function body."""
    names = {arg.arg for arg in fn.args.args}

    def add_target(target):
        if isinstance(target, ast.Name):
            names.add(target.id)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                add_target(item)

    for stmt in fn.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.FunctionDef):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    add_target(target)
            elif isinstance(node, ast.AugAssign):
                add_target(node.target)
            elif isinstance(node, ast.For):
                add_target(node.target)
    return names


class _FreeNameVisitor(ast.NodeVisitor):
    """Collect value-load free names while ignoring callable-name positions."""

    def __init__(self, local_names):
        self.local_names = set(local_names)
        self.names = []
        self._seen = set()

    def _add(self, name):
        if name not in self.local_names and name not in self._seen:
            self._seen.add(name)
            self.names.append(name)

    def visit_Call(self, node):
        # A registered name in function position is a callable use, not a capture.
        if not isinstance(node.func, ast.Name):
            self.visit(node.func)
        for arg in node.args:
            self.visit(arg)
        for kw in node.keywords:
            self.visit(kw.value)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self._add(node.id)

    def visit_FunctionDef(self, node):
        return


def _free_names(fn):
    """Return free value-load names in stable first-seen order."""
    visitor = _FreeNameVisitor(_binding_names_in_local_function(fn))
    for stmt in fn.body:
        visitor.visit(stmt)
    return tuple(visitor.names)


class _LocalFunctionCallVisitor(ast.NodeVisitor):
    """Collect script-local function calls in stable first-seen order."""

    def __init__(self, local_function_names):
        self.local_function_names = set(local_function_names)
        self.names = []
        self._seen = set()

    def _add(self, name):
        if name not in self._seen:
            self._seen.add(name)
            self.names.append(name)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            if node.func.id in self.local_function_names:
                self._add(node.func.id)
        else:
            self.visit(node.func)
        for arg in node.args:
            self.visit(arg)
        for kw in node.keywords:
            self.visit(kw.value)

    def visit_FunctionDef(self, node):
        return


def _called_local_functions(fn, local_functions):
    """Return script-local functions called by *fn* in stable first-seen order."""
    visitor = _LocalFunctionCallVisitor(local_functions)
    for stmt in fn.body:
        visitor.visit(stmt)
    return tuple(visitor.names)


def _append_capture(captures, seen, capture):
    """Append a capture tuple once, preserving the first required order."""
    name = capture[0]
    if name in seen:
        return
    seen.add(name)
    captures.append(capture)


def _resolve_direct_capture(comp, fn, capture_name):
    """Resolve one direct free name against the original lexical compiler scope."""
    if capture_name in _ALLOWED_CONSTS:
        return None
    reserved = _reserved_capture_label(comp, capture_name)
    if reserved is not None:
        raise CompileError(f"Local function {fn.name}() cannot capture {capture_name}: name is {reserved}")
    if capture_name in comp.vars:
        value = comp.vars[capture_name]
        reject_compile_time_object(value, f"local function {fn.name}() capture {capture_name}")
        if isinstance(value, list):
            raise CompileError(f"Local function {fn.name}() cannot capture {capture_name}: arrays are not supported")
        return (capture_name, value, True, value.typ)
    if capture_name in comp.consts:
        value = comp.consts[capture_name]
        try:
            typ = value_type_for_const(value)
        except CompileError as exc:
            raise CompileError(
                f"Local function {fn.name}() cannot capture {capture_name}: unsupported compile-time value"
            ) from exc
        return (capture_name, value, False, typ)
    raise CompileError(f"Local function {fn.name}() cannot capture {capture_name}: name is not available in the outer scope")


def _analyze_captures(comp, fn, _stack=()):
    """Resolve direct and transitive lexical captures for a script-local function.

    Local helper groups are compiled independently.  When one local function calls
    another, the caller helper must receive hidden inputs needed by the callee so
    the recursive helper compile can resolve those captures from its immediate
    generated input scope.
    """
    if fn.name in _stack:
        cycle = " -> ".join(_stack + (fn.name,))
        raise CompileError(f"Recursive local function calls are not supported: {cycle}")

    captures = []
    seen = set()
    for capture_name in _free_names(fn):
        capture = _resolve_direct_capture(comp, fn, capture_name)
        if capture is not None:
            _append_capture(captures, seen, capture)

    local_functions = getattr(comp, "local_functions", {})
    local_names = _binding_names_in_local_function(fn)
    for called_name in _called_local_functions(fn, local_functions):
        if called_name == fn.name or called_name in _stack:
            cycle = " -> ".join(_stack + (fn.name, called_name))
            raise CompileError(f"Recursive local function calls are not supported: {cycle}")
        for capture in _analyze_captures(comp, local_functions[called_name], _stack + (fn.name,)):
            capture_name = capture[0]
            if capture_name in local_names and capture_name not in seen:
                raise CompileError(
                    f"Local function {fn.name}() cannot forward capture {capture_name} "
                    f"required by {called_name}(): name is local to {fn.name}()"
                )
            _append_capture(captures, seen, capture)
    return tuple(captures)


def _helper_metadata_matches(group, *, namespace, function_name, signature):
    """Return True when an existing group is an owned matching local-function helper."""
    try:
        return (
            group.get(LOCAL_HELPER_KIND_PROP) == LOCAL_HELPER_KIND
            and group.get(LOCAL_HELPER_NAMESPACE_PROP) == namespace
            and group.get(LOCAL_HELPER_NAME_PROP) == function_name
            and group.get(LOCAL_HELPER_SIGNATURE_PROP) == signature
        )
    except Exception:
        return False


def _write_helper_metadata(group, *, namespace, function_name, signature, source):
    """Persist local-helper ownership metadata used by future reuse checks."""
    group[LOCAL_HELPER_KIND_PROP] = LOCAL_HELPER_KIND
    group[LOCAL_HELPER_NAMESPACE_PROP] = namespace
    group[LOCAL_HELPER_NAME_PROP] = function_name
    group[LOCAL_HELPER_SIGNATURE_PROP] = signature
    group[LOCAL_HELPER_SOURCE_PROP] = source


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

    captures = _analyze_captures(comp, fn)
    hidden_capture_names = tuple(capture[0] for capture in captures)
    for capture_name, value, is_dynamic, typ in captures:
        param_types[capture_name] = typ
        if is_dynamic:
            compiled_args[capture_name] = value
        else:
            const_args[capture_name] = value

    signature_names = tuple(params) + hidden_capture_names
    signature = ",".join(f"{param}:{param_types[param]}" for param in signature_names)
    logical_namespace = _logical_namespace(getattr(comp, "helper_namespace", None) or getattr(comp.group, "name", "Group"))
    namespace_fragment = _helper_namespace_fragment(logical_namespace)
    group_name = f"NodeForge.local.{namespace_fragment}.{name}.{signature}"
    source = local_function_source(fn, param_types, hidden_captures=hidden_capture_names)
    cache_key = (name, signature, source)
    function_group = comp.local_group_cache.get(cache_key)
    if function_group is None or getattr(function_group, "name", None) not in bpy.data.node_groups:
        existing = bpy.data.node_groups.get(group_name)
        if existing is not None:
            if getattr(existing, "bl_idname", None) != "GeometryNodeTree" or not _helper_metadata_matches(
                existing,
                namespace=logical_namespace,
                function_name=name,
                signature=signature,
            ):
                raise CompileError(f"Local function helper group name collision: {group_name}")
            function_group = comp.compile_group_callback(
                source,
                group_name,
                existing_group=existing,
                local_functions=comp.local_functions,
                backend_builtins=comp.backend_builtins,
                imported_library_functions=comp.imported_library_functions,
                helper_namespace=getattr(comp, "helper_namespace", None),
                local_helper_transaction=getattr(comp, "local_helper_transaction", None),
            )
        else:
            function_group = comp.compile_group_callback(
                source,
                group_name,
                local_functions=comp.local_functions,
                backend_builtins=comp.backend_builtins,
                imported_library_functions=comp.imported_library_functions,
                helper_namespace=getattr(comp, "helper_namespace", None),
                local_helper_transaction=getattr(comp, "local_helper_transaction", None),
            )
        if getattr(function_group, "name", None) != group_name:
            try:
                if existing is None and function_group is not None and bpy.data.node_groups.get(function_group.name) is function_group:
                    bpy.data.node_groups.remove(function_group, do_unlink=True)
            except Exception:
                pass
            raise CompileError(f"Local function helper group name collision: {group_name}")
        _write_helper_metadata(
            function_group,
            namespace=logical_namespace,
            function_name=name,
            signature=signature,
            source=source,
        )
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
