"""Public compiler facade and high-level Geometry Nodes group assembly."""

import ast
import bpy

from .constants import TYPE_FLOAT, TYPE_INT
from .errors import CompileError
from .values import Value
from .nodes import (
    _new_node,
    _value,
    _compare,
    _combine_xyz_mixed,
    _socket_type_for,
)
from .parsing import _parse_source, _collect_inputs, _needs_geometry_io
from .consteval import _const_eval, _preprocess_compile_time, _infer_input_types, _is_const_vector
from .storage import (
    _reset_node_group,
    _store_group_source,
    _extract_group_source,
    _get_or_create_scratch_text,
    _replace_text_contents,
    INPUT_DEFAULTS_PROP,
)
from .interface import _set_socket_default, _set_interface_socket_default, _record_group_input_default
from .update import _apply_group_defaults_to_node, _capture_node_external_state, _restore_node_external_state
from .library import library_function_names, materialize_library_function_group
from .statements import _unique_output_name
from . import expression_compiler
from . import local_functions
from . import library_calls
from .statement_compiler import GroupBuildContext, compile_statements


class Compiler:
    """Compilation context for one Geometry Nodes group build."""

    def __init__(
        self,
        group,
        group_input,
        consts=None,
        local_functions=None,
        local_group_cache=None,
        backend_builtins=None,
        compile_group_callback=None,
    ):
        """Initialize state shared by expression, statement, and call compilers."""
        self.group = group
        self.group_input = group_input
        self.vars = {}
        self.consts = consts or {}
        self.local_functions = local_functions or {}
        self.local_group_cache = local_group_cache if local_group_cache is not None else {}
        self.backend_builtins = dict(backend_builtins or {})
        self.compile_group_callback = compile_group_callback or _make_group
        self.depth = 0

    def compile(self, expr):
        """Compile one AST expression into this group's node tree."""
        self.depth += 1
        try:
            return expression_compiler.compile_expr(self, expr, self.depth)
        finally:
            self.depth -= 1

    def _compile(self, expr, depth=0):
        """Compatibility facade for expression lowering helpers."""
        return expression_compiler.compile_expr(self, expr, depth)

    def _compile_const_value(self, value, x=0, y=0):
        """Turn a compile-time constant into a node Value or script-level array."""
        if _is_const_vector(value) or (
            isinstance(value, (tuple, list))
            and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
        ):
            return _combine_xyz_mixed(self.group, list(value), x, y)
        if isinstance(value, bool):
            val = _value(self.group, 1.0 if value else 0.0, x, y)
            zero = _value(self.group, 0.0, x + 20, y - 40)
            return _compare(self.group, "NOT_EQUAL", val, zero, x, y)
        if isinstance(value, int):
            return _value(self.group, value, x, y)
        if isinstance(value, float):
            return _value(self.group, value, x, y)
        if isinstance(value, (list, tuple)):
            return [self._compile_const_value(v, x, y) for v in value]
        raise CompileError("Unsupported compile-time value in runtime expression")

    def _value_type_for_const(self, value):
        """Infer the NodeForge type represented by a compile-time argument."""
        return local_functions.value_type_for_const(value)

    def _input_call_for_type(self, param_name, typ):
        """Return source code that recreates a local function parameter as an input."""
        return local_functions.input_call_for_type(param_name, typ)

    def _local_function_source(self, fn, param_types):
        """Lower a script-local function definition into a temporary group source."""
        return local_functions.local_function_source(fn, param_types)

    def _compile_backend_builtin_call(self, expr, depth=0):
        """Compile a package-local backend helper call."""
        return local_functions.compile_backend_builtin_call(self, expr, depth)

    def _compile_local_function_call(self, expr, depth=0):
        """Compile a script-local helper function call."""
        return local_functions.compile_local_function_call(self, expr, depth)

    def _create_input_socket_value(self, name, typ, default=None):
        """Create or reuse a group input socket and expose it as a Value."""
        if name in self.vars:
            existing = self.vars[name]
            if existing.typ != typ:
                raise CompileError(f'Input "{name}" already exists with another type')
            return existing
        sock_type = _socket_type_for(typ)
        iface = self.group.interface.new_socket(name=name, in_out="INPUT", socket_type=sock_type)
        if default is not None:
            _set_socket_default(iface, default)
            _set_interface_socket_default(self.group, name, "INPUT", default)
            _record_group_input_default(self.group, name, typ, default)
        socket = next((s for s in self.group_input.outputs if s.name == name), None)
        if socket is None:
            raise CompileError(f'Internal error: input socket "{name}" was not created')
        val = Value(socket, typ)
        self.vars[name] = val
        return val

    def _const_eval_macro_arg(self, expr):
        """Evaluate a compile-time macro argument."""
        return _const_eval(expr, self.consts)

    def _const_or_compile_arg(self, expr, depth=0):
        """Return a compile-time value or a dynamic Value for a call argument."""
        try:
            return _const_eval(expr, self.consts), False
        except CompileError:
            return self.compile(expr), True

    def _const_or_compile_library_arg(self, expr, depth=0):
        """Compatibility wrapper for the shared const/dynamic argument classifier."""
        return self._const_or_compile_arg(expr, depth)

    def _compile_library_function_call(self, expr, depth=0):
        """Compile a function-library call through the library-call adapter."""
        return library_calls.compile_library_function_call(self, expr, depth)


def _make_group(source: str, name: str = "NodeForge Group", existing_group=None, local_functions=None, backend_builtins=None):
    """Compile NodeForge source into a GeometryNodeTree."""
    raw_stmts = _parse_source(source)

    local_function_defs = dict(local_functions or {})
    body_stmts = []
    for stmt in raw_stmts:
        if isinstance(stmt, ast.FunctionDef):
            if stmt.name in local_function_defs:
                raise CompileError(f"Duplicate local function: {stmt.name}")
            local_function_defs[stmt.name] = stmt
        else:
            body_stmts.append(stmt)

    stmts, consts = _preprocess_compile_time(body_stmts)
    callable_names = library_function_names() | set(local_function_defs) | set(backend_builtins or {})
    input_names = sorted(set(_collect_inputs(stmts, extra_builtin_names=callable_names)) - set(consts.keys()))
    input_types = _infer_input_types(stmts)
    if existing_group is not None:
        if getattr(existing_group, "bl_idname", None) != "GeometryNodeTree":
            raise CompileError("Selected node group is not a GeometryNodeTree")
        group = existing_group
        _reset_node_group(group)
    else:
        group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    group.color_tag = 'CONVERTER'
    _store_group_source(group, source)
    try:
        group[INPUT_DEFAULTS_PROP] = {}
    except Exception:
        pass

    geometry_mode = _needs_geometry_io(stmts)
    if geometry_mode:
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    for input_name in input_names:
        sock_type = "NodeSocketInt" if input_types.get(input_name) == TYPE_INT else "NodeSocketFloat"
        sock = group.interface.new_socket(name=input_name, in_out="INPUT", socket_type=sock_type)
        default_value = (1 if input_name == "iterations" else 0) if sock_type == "NodeSocketInt" else 0.0
        _set_socket_default(sock, default_value)
        _set_interface_socket_default(group, input_name, "INPUT", default_value)
        _record_group_input_default(group, input_name, input_types.get(input_name, TYPE_FLOAT), default_value)

    group_input = _new_node(group, "NodeGroupInput", -1100, 0)
    group_output = _new_node(group, "NodeGroupOutput", 1100, 0)
    group_output.is_active_output = True
    comp = Compiler(
        group,
        group_input,
        consts,
        local_functions=local_function_defs,
        local_group_cache={},
        backend_builtins=backend_builtins,
        compile_group_callback=_make_group,
    )

    for socket in group_input.outputs:
        if socket.name in input_names:
            comp.vars[socket.name] = Value(socket, input_types.get(socket.name, TYPE_FLOAT))

    geometry_socket = None
    if geometry_mode:
        geometry_socket = next((s for s in group_input.outputs if s.name == "Geometry"), None)
        if geometry_socket is None:
            raise CompileError("Internal error: missing Geometry input")

    ctx = GroupBuildContext(
        group=group,
        comp=comp,
        consts=consts,
        geometry_mode=geometry_mode,
        geometry_socket=geometry_socket,
    )
    compile_statements(ctx, stmts)

    if geometry_mode:
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        group.links.new(ctx.geometry_socket, group_output.inputs["Geometry"])

    if ctx.explicit_outputs:
        outputs = ctx.explicit_outputs
    elif ctx.auto_final_output is not None:
        outputs = [ctx.auto_final_output]
    else:
        outputs = []

    used_interface_names = {"Geometry"} if geometry_mode else set()
    for output_name, result in outputs:
        if isinstance(result, list):
            raise CompileError("Cannot output an array directly; use join(array) or index it")
        final_name = _unique_output_name(used_interface_names, output_name)
        group.interface.new_socket(name=final_name, in_out="OUTPUT", socket_type=_socket_type_for(result.typ))
        group.links.new(result.socket, group_output.inputs[final_name])

    if not geometry_mode and not outputs:
        raise CompileError("Script produced no output. Use out = ..., output(...), set_position(...), or store(...)")

    return group


def create_expression_group(source: str, name: str = "NodeForge Group"):
    """Create a new Geometry Nodes group from NodeForge source."""
    return _make_group(source, name)


def create_library_function_group(name: str):
    """Create or update a reusable node group for a function-library entry."""
    return materialize_library_function_group(name, _make_group)


def update_expression_group(group, source: str):
    """Rebuild an existing Geometry Nodes group from NodeForge source."""
    return _make_group(source, getattr(group, "name", "NodeForge Group"), existing_group=group)


__all__ = [
    "CompileError",
    "Compiler",
    "create_expression_group",
    "update_expression_group",
    "create_library_function_group",
    "_apply_group_defaults_to_node",
    "_capture_node_external_state",
    "_restore_node_external_state",
    "_extract_group_source",
    "_get_or_create_scratch_text",
    "_replace_text_contents",
]
