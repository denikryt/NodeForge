"""Compilation helpers for script-local DSL functions."""

import ast
import hashlib
import json
import re
from dataclasses import dataclass
import bpy

from .constants import *
from .errors import CompileError
from .library import (
    _new_node, _input_sockets, _output_sockets,
    _normalized_socket_name, _set_socket_default, _socket_type_to_value_type,
    apply_function_node_display_name, display_name_for_function,
)
from .consteval import _is_const_vector
from .compile_time import reject_compile_time_object
from .values import TupleValue, make_value, reject_tuple_value
from .library_calls import _argument_type_matches

LOCAL_HELPER_KIND_PROP = "nodeforge_generated_kind"
LOCAL_HELPER_KIND = "local_function_helper"
LOCAL_HELPER_NAMESPACE_PROP = "nodeforge_local_function_namespace"
LOCAL_HELPER_NAME_PROP = "nodeforge_local_function_name"
LOCAL_HELPER_SIGNATURE_PROP = "nodeforge_local_function_signature"
LOCAL_HELPER_SOURCE_PROP = "nodeforge_local_function_source"
LOCAL_HELPER_RETURN_PROP = "nodeforge_local_function_return_shape"


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
    if isinstance(value, str):
        return TYPE_STRING
    raise CompileError("Local function constant arguments must be numbers, booleans, strings or vectors")


def input_call_for_type(param_name, typ):
    """Return source code that recreates a local function parameter as an input."""
    constructors = {
        TYPE_GEOMETRY: "input_geometry", TYPE_MATERIAL: "input_material",
        TYPE_OBJECT: "input_object", TYPE_VECTOR: "input_vector",
        TYPE_BOOL: "input_bool", TYPE_INT: "input_int", TYPE_FLOAT: "input_float",
        TYPE_STRING: "input_string",
        TYPE_BUNDLE: "input_bundle",
    }
    constructor = constructors.get(typ)
    if constructor is None:
        raise CompileError(f"Local function input type {typ!r} has no supported input constructor")
    return f"{param_name} = {constructor}({param_name!r})"


@dataclass(frozen=True)
class LocalReturnElement:
    """One ordered local-function return socket descriptor."""
    index: int
    socket_name: str
    key: str
    expression: ast.expr


@dataclass(frozen=True)
class LocalReturnShape:
    """Fixed scalar or tuple return contract for a local function."""
    elements: tuple

    @property
    def is_tuple(self):
        """Return whether the source used tuple-return syntax."""
        return len(self.elements) > 1


def _display_name(name):
    """Convert a Python binding name to the project socket display convention."""
    return " ".join(part.capitalize() for part in name.split("_")) or "Value"


def analyze_local_return_shape(fn):
    """Validate and describe the single supported top-level return contract."""
    returns = [stmt for stmt in fn.body if isinstance(stmt, ast.Return)]
    nested_returns = [node for stmt in fn.body for node in ast.walk(stmt) if isinstance(node, ast.Return)]
    if len(returns) != 1 or len(nested_returns) != 1 or fn.body[-1] is not returns[0]:
        raise CompileError(f"Local function {fn.name}() must have exactly one final top-level return")
    value = returns[0].value
    if value is None:
        raise CompileError(f"Local function {fn.name}() return must produce at least one value")
    if isinstance(value, ast.List):
        raise CompileError(f"Local function {fn.name}() cannot return a list; use a flat tuple return")
    exprs = list(value.elts) if isinstance(value, ast.Tuple) else [value]
    if not exprs:
        raise CompileError(f"Local function {fn.name}() cannot return an empty tuple")
    if any(isinstance(item, ast.Starred) for item in exprs):
        raise CompileError(f"Local function {fn.name}() does not support starred return elements")
    if any(isinstance(item, (ast.Tuple, ast.List)) for item in exprs):
        raise CompileError(f"Local function {fn.name}() does not support nested tuple returns")
    used = {}
    elements = []
    for index, expr in enumerate(exprs):
        base = _display_name(expr.id) if isinstance(expr, ast.Name) else f"Value {index + 1}"
        count = used.get(base, 0) + 1
        used[base] = count
        socket_name = base if count == 1 else f"{base} {count}"
        elements.append(LocalReturnElement(index, socket_name, f"return:{index}", expr))
    if len(elements) == 1:
        elements[0] = LocalReturnElement(0, "Value", "return:0", elements[0].expression)
    return LocalReturnShape(tuple(elements))


def resolve_local_parameter_annotation(annotation):
    """Resolve one simple local-function parameter annotation to a type token."""
    if annotation is None:
        return None
    if not isinstance(annotation, ast.Name) or annotation.id not in TYPE_TOKEN_NAMES:
        rendered = ast.unparse(annotation)
        raise CompileError(f"Unsupported local function parameter annotation: {rendered}")
    return TYPE_TOKEN_NAMES[annotation.id]


def local_function_source(fn, param_types, hidden_captures=(), return_shape=None):
    """Lower a script-local function definition into a temporary group source."""
    shape = return_shape or analyze_local_return_shape(fn)
    lines = [input_call_for_type(arg.arg, param_types[arg.arg]) for arg in fn.args.args]
    lines.extend(input_call_for_type(name, param_types[name]) for name in hidden_captures)
    if fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs or fn.args.defaults:
        raise CompileError("Local functions currently support only plain positional parameters without defaults")
    for stmt in fn.body[:-1]:
        if isinstance(stmt, ast.FunctionDef):
            raise CompileError("Nested function definitions are not supported")
        lines.append(ast.unparse(stmt))
    for element in shape.elements:
        lines.append(f"output({element.socket_name!r}, {ast.unparse(element.expression)})")
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


def _local_helper_group_name(namespace, function_name, signature):
    """Return the readable Blender datablock name for a local helper.

    Helper identity is stored in metadata, not encoded in the datablock name.
    Blender may append a numeric suffix when several helpers share the same
    function name; reuse remains deterministic because lookup uses metadata.
    """
    parts = [part for part in re.split(r"[_\s]+", function_name or "") if part]
    return " ".join(part[:1].upper() + part[1:] for part in parts) or "Function"


def _find_local_helper(*, namespace, function_name, signature):
    """Return the unique helper matching the exact metadata identity."""
    matches = [
        group for group in bpy.data.node_groups
        if _helper_metadata_matches(
            group,
            namespace=namespace,
            function_name=function_name,
            signature=signature,
            return_shape=None,
        )
    ]
    if len(matches) > 1:
        raise CompileError(
            f"Multiple local function helpers match {function_name}() with signature {signature}"
        )
    return matches[0] if matches else None


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
        if isinstance(node.ctx, ast.Load) and node.id not in TYPE_TOKEN_NAMES:
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
        reject_tuple_value(value, f"local function {fn.name}() capture {capture_name}")
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


def _helper_metadata_matches(group, *, namespace, function_name, signature, return_shape=None):
    """Return True when an existing group is an owned matching local-function helper."""
    try:
        return (
            group.get(LOCAL_HELPER_KIND_PROP) == LOCAL_HELPER_KIND
            and group.get(LOCAL_HELPER_NAMESPACE_PROP) == namespace
            and group.get(LOCAL_HELPER_NAME_PROP) == function_name
            and group.get(LOCAL_HELPER_SIGNATURE_PROP) == signature
            and (return_shape is None or group.get(LOCAL_HELPER_RETURN_PROP) == _serialize_return_shape(return_shape))
        )
    except Exception:
        return False


def _serialize_return_shape(shape, types=None):
    """Serialize ordered local return metadata for Blender custom properties."""
    payload = [{"key": e.key, "name": e.socket_name} for e in shape.elements]
    if types is not None:
        for item, typ in zip(payload, types):
            item["type"] = typ
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _write_helper_metadata(group, *, namespace, function_name, signature, source, return_shape, return_types):
    """Persist local-helper ownership metadata used by future reuse checks."""
    group[LOCAL_HELPER_KIND_PROP] = LOCAL_HELPER_KIND
    group[LOCAL_HELPER_NAMESPACE_PROP] = namespace
    group[LOCAL_HELPER_NAME_PROP] = function_name
    group[LOCAL_HELPER_SIGNATURE_PROP] = signature
    group[LOCAL_HELPER_SOURCE_PROP] = source
    group[LOCAL_HELPER_RETURN_PROP] = _serialize_return_shape(return_shape, return_types)


def make_local_function_call_node(group, function_group, compiled_args, const_args, return_shape, x=0, y=0):
    """Create a local helper call and reconstruct its scalar or tuple result."""
    node = _new_node(group, "GeometryNodeGroup", x, y)
    node.node_tree = function_group
    apply_function_node_display_name(node, function_group)
    normalized_inputs = {_normalized_socket_name(s.name): s for s in _input_sockets(node)}
    for raw_name, value in const_args.items():
        socket = normalized_inputs.get(_normalized_socket_name(raw_name))
        if socket is None:
            raise CompileError(f"Function {function_group.name} has no input named {raw_name!r}")
        _set_socket_default(socket, value)
    for raw_name, value in compiled_args.items():
        reject_tuple_value(value, f"local function {function_group.name}() argument")
        socket = normalized_inputs.get(_normalized_socket_name(raw_name))
        if socket is None:
            raise CompileError(f"Function {function_group.name} has no input named {raw_name!r}")
        group.links.new(value.socket, socket)
    interface_output_types = {}
    for interface_item in function_group.interface.items_tree:
        if getattr(interface_item, "in_out", None) != "OUTPUT":
            continue
        interface_output_types[_normalized_socket_name(interface_item.name)] = _socket_type_to_value_type(interface_item)

    outputs = []
    for element in return_shape.elements:
        key = _normalized_socket_name(element.socket_name)
        expected_type = interface_output_types.get(key)
        matches = []
        for candidate in node.outputs:
            if not getattr(candidate, "is_output", True):
                continue
            if _normalized_socket_name(candidate.name) != key:
                continue
            candidate_type = _socket_type_to_value_type(candidate)
            if expected_type is None or candidate_type == expected_type:
                matches.append(candidate)
        if not matches:
            raise CompileError(
                f"Local function {function_group.name} has no output named {element.socket_name!r} with the declared type"
            )
        outputs.append(matches[-1])
    values = tuple(make_value(socket, _socket_type_to_value_type(socket)) for socket in outputs)
    return values[0] if len(values) == 1 else TupleValue(values)


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
    declared_types = {a.arg: resolve_local_parameter_annotation(a.annotation) for a in fn.args.args}
    return_shape = analyze_local_return_shape(fn)
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
            reject_tuple_value(value, "script-local function argument")
            if isinstance(value, list):
                raise CompileError("Local function arguments cannot be arrays")
            compiled_args[param] = value
            param_types[param] = value.typ
            declared = declared_types[param]
            if declared is not None and not _argument_type_matches(declared, value.typ):
                raise CompileError(f"{name}() parameter {param!r} expects {declared}, got {value.typ}")
            if declared is not None:
                param_types[param] = declared
        else:
            const_args[param] = value
            param_types[param] = value_type_for_const(value)
            declared = declared_types[param]
            if declared is not None and not _argument_type_matches(declared, param_types[param]):
                raise CompileError(f"{name}() parameter {param!r} expects {declared}, got {param_types[param]}")
            if declared is not None:
                param_types[param] = declared
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
            reject_tuple_value(value, "script-local function argument")
            if isinstance(value, list):
                raise CompileError("Local function arguments cannot be arrays")
            compiled_args[kw.arg] = value
            param_types[kw.arg] = value.typ
            declared = declared_types[kw.arg]
            if declared is not None and not _argument_type_matches(declared, value.typ):
                raise CompileError(f"{name}() parameter {kw.arg!r} expects {declared}, got {value.typ}")
            if declared is not None:
                param_types[kw.arg] = declared
        else:
            const_args[kw.arg] = value
            param_types[kw.arg] = value_type_for_const(value)
            declared = declared_types[kw.arg]
            if declared is not None and not _argument_type_matches(declared, param_types[kw.arg]):
                raise CompileError(f"{name}() parameter {kw.arg!r} expects {declared}, got {param_types[kw.arg]}")
            if declared is not None:
                param_types[kw.arg] = declared
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
    group_name = _local_helper_group_name(logical_namespace, name, signature)
    source = local_function_source(fn, param_types, hidden_captures=hidden_capture_names, return_shape=return_shape)
    cache_key = (name, signature, source)
    function_group = comp.local_group_cache.get(cache_key)
    if function_group is None or getattr(function_group, "name", None) not in bpy.data.node_groups:
        existing = _find_local_helper(
            namespace=logical_namespace,
            function_name=name,
            signature=signature,
        )
        if existing is not None:
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
        _write_helper_metadata(
            function_group,
            namespace=logical_namespace,
            function_name=name,
            signature=signature,
            source=source,
            return_shape=return_shape,
            return_types=[_socket_type_to_value_type(s) for s in function_group.interface.items_tree if getattr(s, "in_out", None) == "OUTPUT"],
        )
        function_group.name = group_name
        comp.local_group_cache[cache_key] = function_group
    x = depth * 240
    y = -depth * 90
    return make_local_function_call_node(comp.group, function_group, compiled_args, const_args, return_shape, x=x, y=y)


__all__ = [
    "value_type_for_const",
    "input_call_for_type",
    "LocalReturnElement", "LocalReturnShape", "analyze_local_return_shape",
    "resolve_local_parameter_annotation", "make_local_function_call_node",
    "local_function_source",
    "compile_backend_builtin_call",
    "compile_local_function_call",
]
