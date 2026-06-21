"""Public compiler facade and high-level Geometry Nodes group assembly."""

import ast
import re
import bpy

from .constants import *
from .errors import CompileError
from .values import Value
from .nodes import *
from .parsing import *
from .statements import *
from .consteval import *
from .geometry import *
from .runtime import *
from .builtins import registry as builtin_registry
from .storage import _reset_node_group, _store_group_source, _extract_group_source, _get_or_create_scratch_text, _replace_text_contents
from .storage import INPUT_DEFAULTS_PROP
from .interface import _set_socket_default, _set_interface_socket_default, _record_group_input_default
from .update import _apply_group_defaults_to_node, _capture_node_external_state, _restore_node_external_state
from .library import (
    library_function_names,
    has_library_function,
    has_module_library_function,
    compile_module_library_function_call,
    get_or_create_library_group,
    make_library_call_node,
    materialize_library_function_group,
    _normalized_socket_name,
)

# Local compatibility guard: these core calls intentionally accept keyword arguments.
# File-based function modules are handled by has_library_function(...).
_KEYWORD_GEOMETRY_MACRO_NAMES = set(GEOMETRY_MACRO_NAMES) | {
    "cube", "join", "transform", "polyline",
    "realize_instances", "input_geometry", "input_float", "input_int",
    "input_bool", "input_vector",
}

class Compiler:
    """Class `Compiler` used by the NodeForge addon."""
    def __init__(self, group, group_input, consts=None, local_functions=None, local_group_cache=None):
        """Function `__init__` used by the NodeForge addon."""
        self.group = group
        self.group_input = group_input
        self.vars = {}
        self.consts = consts or {}
        self.local_functions = local_functions or {}
        self.local_group_cache = local_group_cache if local_group_cache is not None else {}
        self.depth = 0

    def compile(self, expr):
        """Function `compile` used by the NodeForge addon."""
        self.depth += 1
        try:
            return self._compile(expr, self.depth)
        finally:
            self.depth -= 1

    def _compile(self, expr, depth=0):
        """Function `_compile` used by the NodeForge addon."""
        x = depth * 240
        y = -depth * 90

        if isinstance(expr, ast.Constant):
            if isinstance(expr.value, bool):
                val = _value(self.group, 1.0 if expr.value else 0.0, x, y)
                # Bool constants through Compare: val != 0 for actual Bool socket.
                zero = _value(self.group, 0.0, x + 20, y - 40)
                return _compare(self.group, "NOT_EQUAL", val, zero, x, y)
            if isinstance(expr.value, (int, float)):
                return _value(self.group, expr.value, x, y)
            raise CompileError("Only numeric and boolean constants are supported")

        if isinstance(expr, ast.Name):
            if expr.id in _ALLOWED_CONSTS:
                return _value(self.group, _ALLOWED_CONSTS[expr.id], x, y)
            if expr.id in self.consts:
                return self._compile_const_value(self.consts[expr.id], x, y)
            if expr.id in self.vars:
                return self.vars[expr.id]
            raise CompileError(f"Unknown name: {expr.id}")

        if isinstance(expr, ast.Attribute):
            base = self._compile(expr.value, depth + 1)
            if expr.attr in {"x", "y", "z"}:
                return _separate_xyz(self.group, base, expr.attr, x, y)
            raise CompileError("Only .x, .y and .z vector attributes are supported")

        if isinstance(expr, ast.BinOp):
            left = self._compile(expr.left, depth + 1)
            right = self._compile(expr.right, depth + 1)
            op_type = type(expr.op)
            if op_type not in _BIN_OPS:
                raise CompileError(f"Unsupported binary operator: {op_type.__name__}")
            if _is_number_type(left.typ) and _is_number_type(right.typ):
                return _math(self.group, _BIN_OPS[op_type], [left, right], x, y)
            if op_type in {ast.Add, ast.Sub} and left.typ == TYPE_VECTOR and right.typ == TYPE_VECTOR:
                return _vector_math(self.group, _BIN_OPS[op_type], [left, right], TYPE_VECTOR, x, y)
            if op_type is ast.Mult:
                if left.typ == TYPE_VECTOR and right.typ == TYPE_FLOAT:
                    return _vector_math(self.group, "SCALE", [left, right], TYPE_VECTOR, x, y)
                if left.typ == TYPE_FLOAT and right.typ == TYPE_VECTOR:
                    return _vector_math(self.group, "SCALE", [right, left], TYPE_VECTOR, x, y)
                if left.typ == TYPE_VECTOR and right.typ == TYPE_VECTOR:
                    return _vector_math(self.group, "MULTIPLY", [left, right], TYPE_VECTOR, x, y)
            if op_type is ast.Div and left.typ == TYPE_VECTOR and right.typ == TYPE_FLOAT:
                inv = _math(self.group, "DIVIDE", [_value(self.group, 1.0, x, y - 40), right], x, y)
                return _vector_math(self.group, "SCALE", [left, inv], TYPE_VECTOR, x, y)
            raise CompileError(f"Unsupported operation between {left.typ} and {right.typ}")

        if isinstance(expr, ast.UnaryOp):
            val = self._compile(expr.operand, depth + 1)
            if isinstance(expr.op, ast.UAdd):
                return val
            if isinstance(expr.op, ast.USub):
                if _is_number_type(val.typ):
                    return _math(self.group, "SUBTRACT", [_value(self.group, 0.0, x, y - 40), val], x, y)
                if val.typ == TYPE_VECTOR:
                    return _vector_math(self.group, "SCALE", [val, _value(self.group, -1.0, x, y - 40)], TYPE_VECTOR, x, y)
            if isinstance(expr.op, ast.Not):
                if val.typ != TYPE_BOOL:
                    raise CompileError("not expects Bool")
                node = _new_node(self.group, "FunctionNodeBooleanMath", x, y)
                node.operation = "NOT"
                self.group.links.new(val.socket, node.inputs[0])
                return Value(node.outputs[0], TYPE_BOOL)
            raise CompileError(f"Unsupported unary operator: {type(expr.op).__name__}")

        if isinstance(expr, ast.BoolOp):
            if len(expr.values) < 2:
                return self._compile(expr.values[0], depth + 1)
            op = _BOOLEAN_OPS.get(type(expr.op))
            if not op:
                raise CompileError("Unsupported boolean operator")
            current = self._compile(expr.values[0], depth + 1)
            for nxt_expr in expr.values[1:]:
                nxt = self._compile(nxt_expr, depth + 1)
                current = _boolean_math(self.group, op, [current, nxt], x, y)
            return current

        if isinstance(expr, ast.Compare):
            if len(expr.ops) < 1 or len(expr.comparators) < 1:
                raise CompileError("Invalid comparison")
            comparisons = []
            left_expr = expr.left
            for op_node, right_expr in zip(expr.ops, expr.comparators):
                left = self._compile(left_expr, depth + 1)
                right = self._compile(right_expr, depth + 1)
                op = _COMPARE_OPS.get(type(op_node))
                if not op:
                    raise CompileError("Unsupported comparison operator")
                comparisons.append(_compare(self.group, op, left, right, x, y))
                left_expr = right_expr
            current = comparisons[0]
            for nxt in comparisons[1:]:
                current = _boolean_math(self.group, "AND", [current, nxt], x, y)
            return current

        if isinstance(expr, ast.IfExp):
            cond = self._compile(expr.test, depth + 1)
            true_val = self._compile(expr.body, depth + 1)
            false_val = self._compile(expr.orelse, depth + 1)
            if isinstance(true_val, list) or isinstance(false_val, list):
                raise CompileError("if-expression cannot return arrays")
            return _switch(self.group, cond, false_val, true_val, x, y)

        if isinstance(expr, (ast.List, ast.Tuple)):
            return [self._compile(e, depth + 1) for e in expr.elts]

        if isinstance(expr, ast.Subscript):
            base = self._compile(expr.value, depth + 1)
            try:
                idx = int(_const_eval(expr.slice, self.consts))
            except CompileError as exc:
                raise CompileError("array/vector indexing currently requires a compile-time integer index") from exc
            if isinstance(base, list):
                try:
                    return base[idx]
                except Exception as exc:
                    raise CompileError("array index out of range") from exc
            if isinstance(base, Value) and base.typ == TYPE_VECTOR:
                if idx not in (0, 1, 2):
                    raise CompileError("vector index must be 0, 1 or 2")
                return _separate_xyz(self.group, base, ("x", "y", "z")[idx], x, y)
            raise CompileError("indexing is supported for arrays and Vector values only")

        if isinstance(expr, ast.Call):
            if not isinstance(expr.func, ast.Name):
                raise CompileError("Only simple function calls are supported")
            name = expr.func.id
            if expr.keywords and not builtin_registry.has_callable_builtin(name) and not has_library_function(name) and name not in self.local_functions:
                raise CompileError(f"Keyword arguments are only supported for builtins, library functions or local functions; {name} is not registered as one")
            if builtin_registry.has_callable_builtin(name):
                return builtin_registry.compile_call(self, expr, depth)
            if name in self.local_functions:
                return self._compile_local_function_call(expr, depth)
            if has_library_function(name):
                return self._compile_geometry_macro(expr, depth)
            if name in {"output", "store"}:
                raise CompileError(f"{name}() is only supported as a top-level call")
            raise CompileError(f"Unsupported function: {name}")

        raise CompileError(f"Unsupported expression element: {type(expr).__name__}")



    def _compile_const_value(self, value, x=0, y=0):
        """Turn a compile-time constant into a node Value or script-level array."""
        if _is_const_vector(value) or (isinstance(value, (tuple, list)) and len(value) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
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
        if _is_const_vector(value) or (isinstance(value, (tuple, list)) and len(value) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
            return TYPE_VECTOR
        if isinstance(value, bool):
            return TYPE_BOOL
        if isinstance(value, int) and not isinstance(value, bool):
            return TYPE_INT
        if isinstance(value, float):
            return TYPE_FLOAT
        raise CompileError("Local function constant arguments must be numbers, booleans or vectors")

    def _input_call_for_type(self, param_name, typ):
        if typ == TYPE_GEOMETRY:
            return f'{param_name} = input_geometry({param_name!r})'
        if typ == TYPE_VECTOR:
            return f'{param_name} = input_vector({param_name!r})'
        if typ == TYPE_BOOL:
            return f'{param_name} = input_bool({param_name!r})'
        if typ == TYPE_INT:
            return f'{param_name} = input_int({param_name!r})'
        return f'{param_name} = input_float({param_name!r})'

    def _local_function_source(self, fn, param_types):
        lines = []
        for arg in fn.args.args:
            name = arg.arg
            lines.append(self._input_call_for_type(name, param_types[name]))
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

    def _compile_local_function_call(self, expr, depth=0):
        name = expr.func.id
        fn = self.local_functions[name]
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
            value, is_dynamic = self._const_or_compile_library_arg(arg_expr, depth + 1)
            if is_dynamic:
                if isinstance(value, list):
                    raise CompileError("Local function arguments cannot be arrays")
                compiled_args[param] = value
                param_types[param] = value.typ
            else:
                const_args[param] = value
                param_types[param] = self._value_type_for_const(value)
            used.add(param)

        for kw in expr.keywords:
            if kw.arg is None:
                raise CompileError(f"{name}() does not support **kwargs")
            if kw.arg not in params:
                raise CompileError(f"{name}() got unknown keyword argument {kw.arg!r}")
            if kw.arg in used:
                raise CompileError(f"{name}() got multiple values for argument {kw.arg!r}")
            value, is_dynamic = self._const_or_compile_library_arg(kw.value, depth + 1)
            if is_dynamic:
                if isinstance(value, list):
                    raise CompileError("Local function arguments cannot be arrays")
                compiled_args[kw.arg] = value
                param_types[kw.arg] = value.typ
            else:
                const_args[kw.arg] = value
                param_types[kw.arg] = self._value_type_for_const(value)
            used.add(kw.arg)

        missing = [p for p in params if p not in used]
        if missing:
            raise CompileError(f"{name}() missing arguments: {', '.join(missing)}")

        signature = ",".join(param_types[p] for p in params)
        safe_parent = re.sub(r"[^A-Za-z0-9_]+", "_", getattr(self.group, "name", "Group"))
        group_name = f"NodeForge.local.{safe_parent}.{name}.{signature}"
        source = self._local_function_source(fn, param_types)
        cache_key = (name, signature, source)
        function_group = self.local_group_cache.get(cache_key)
        if function_group is None or getattr(function_group, "name", None) not in bpy.data.node_groups:
            existing = bpy.data.node_groups.get(group_name)
            if existing is not None and getattr(existing, "bl_idname", None) == "GeometryNodeTree":
                function_group = _make_group(source, group_name, existing_group=existing, local_functions=self.local_functions)
            else:
                function_group = _make_group(source, group_name, local_functions=self.local_functions)
            try:
                function_group["nodeforge_local_function_name"] = name
                function_group["nodeforge_local_function_source"] = source
            except Exception:
                pass
            self.local_group_cache[cache_key] = function_group
        x = depth * 240
        y = -depth * 90
        return make_library_call_node(self.group, function_group, compiled_args, const_args, x=x, y=y)



    def _create_input_socket_value(self, name, typ, default=None):
        """Function `_create_input_socket_value` used by the NodeForge addon."""
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
        # Interface changes update NodeGroupInput outputs immediately in Blender 5.x.
        socket = next((s for s in self.group_input.outputs if s.name == name), None)
        if socket is None:
            raise CompileError(f'Internal error: input socket "{name}" was not created')
        val = Value(socket, typ)
        self.vars[name] = val
        return val



    def _const_eval_macro_arg(self, expr):
        """Function `_const_eval_macro_arg` used by the NodeForge addon."""
        return _const_eval(expr, self.consts)

    def _const_or_compile_library_arg(self, expr, depth=0):
        """Return either a compile-time value or a dynamic Value for a library argument."""
        try:
            return _const_eval(expr, self.consts), False
        except CompileError:
            return self.compile(expr), True

    def _compile_library_function_call(self, expr, depth=0):
        """Compile a call to a user-defined function from the functions folder.

        The function script is compiled into a reusable node group. The current
        group then receives a GeometryNodeGroup call node that wires arguments to
        the library group's inputs and returns its single output.
        """
        name = expr.func.id
        if has_module_library_function(name):
            return compile_module_library_function_call(self, expr, depth)
        x = depth * 240
        y = -depth * 90
        function_group = get_or_create_library_group(name, _make_group)

        probe = _new_node(self.group, "GeometryNodeGroup", x, y)
        probe.node_tree = function_group
        input_names = [s.name for s in probe.inputs if getattr(s, "enabled", True)]
        self.group.nodes.remove(probe)

        if len(expr.args) > len(input_names):
            raise CompileError(f"{name}() got too many positional arguments")

        compiled_args = {}
        const_args = {}
        used = set()
        for idx, arg_expr in enumerate(expr.args):
            socket_name = input_names[idx]
            value, is_dynamic = self._const_or_compile_library_arg(arg_expr, depth + 1)
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
            value, is_dynamic = self._const_or_compile_library_arg(kw.value, depth + 1)
            if is_dynamic:
                compiled_args[socket_name] = value
            else:
                const_args[socket_name] = value
            used.add(key)

        return make_library_call_node(self.group, function_group, compiled_args, const_args, x=x, y=y)

    def _compile_geometry_macro(self, expr, depth=0):
        """Function `_compile_geometry_macro` used by the NodeForge addon."""
        name = expr.func.id
        x = depth * 240
        y = -depth * 90
        kws = _kw_dict(expr)

        if has_library_function(name):
            return self._compile_library_function_call(expr, depth)
        if builtin_registry.has_callable_builtin(name):
            return builtin_registry.compile_call(self, expr, depth)

        if name in {"input_geometry", "input_float", "input_int", "input_bool", "input_vector"}:
            _check_no_extra_keywords(kws, {"default"})
            if len(expr.args) != 1:
                raise CompileError(f'{name}(name, ...) expects exactly one name argument')
            input_name = _literal_string(expr.args[0], f"{name}() name")
            if name == "input_geometry":
                if kws:
                    raise CompileError("input_geometry(name) does not support default=")
                return self._create_input_socket_value(input_name, TYPE_GEOMETRY, None)
            default_expr = kws.get("default", None)
            if name == "input_float":
                default = 0.0 if default_expr is None else _as_float_const(_const_eval(default_expr, self.consts), "input_float default")
                return self._create_input_socket_value(input_name, TYPE_FLOAT, default)
            if name == "input_int":
                default = 0 if default_expr is None else int(_as_float_const(_const_eval(default_expr, self.consts), "input_int default"))
                return self._create_input_socket_value(input_name, TYPE_INT, default)
            if name == "input_bool":
                default = False if default_expr is None else bool(_const_eval(default_expr, self.consts))
                return self._create_input_socket_value(input_name, TYPE_BOOL, default)
            if name == "input_vector":
                if default_expr is None:
                    default = (0.0, 0.0, 0.0)
                else:
                    default = _const_eval(default_expr, self.consts)
                    if _is_const_vector(default):
                        default = tuple(default)
                    elif isinstance(default, (tuple, list)) and len(default) == 3:
                        default = tuple(_as_float_const(v, "input_vector default component") for v in default)
                    else:
                        raise CompileError("input_vector default= must be vector(x,y,z) or a 3-number tuple/list")
                return self._create_input_socket_value(input_name, TYPE_VECTOR, default)

        if name == "points":
            if kws:
                raise CompileError("points() does not support keyword arguments")
            if len(expr.args) != 1:
                raise CompileError("points(count) expects one Int argument")
            try:
                count = _const_eval(expr.args[0], self.consts)
            except CompileError:
                count = self.compile(expr.args[0])
            return _points_geometry(self.group, count, x, y)

        if name == "set_position":
            _check_no_extra_keywords(kws, {"selection"})
            if len(expr.args) != 2:
                raise CompileError("set_position(geo, position, selection=...) expects Geometry and Vector")
            geo = self.compile(expr.args[0])
            pos = self.compile(expr.args[1])
            selection = None
            if "selection" in kws:
                selection = self.compile(kws["selection"])
            return _set_position_geometry(self.group, geo, pos, selection, x, y)

        if name == "instance_on_points":
            _check_no_extra_keywords(kws, {"scale", "rotation", "realize"})
            if len(expr.args) != 2:
                raise CompileError("instance_on_points(instance, points, ...) expects two Geometry arguments")
            instance = self.compile(expr.args[0])
            points_geo = self.compile(expr.args[1])
            scale = None
            rotation = None
            realize = True
            if "scale" in kws:
                try:
                    scale = _const_eval(kws["scale"], self.consts)
                except CompileError:
                    scale = self.compile(kws["scale"])
            if "rotation" in kws:
                try:
                    rotation = _const_eval(kws["rotation"], self.consts)
                except CompileError:
                    rotation = self.compile(kws["rotation"])
            if "realize" in kws:
                try:
                    realize = bool(_const_eval(kws["realize"], self.consts))
                except CompileError:
                    raise CompileError("instance_on_points realize= must be a compile-time bool")
            return _instance_on_points(self.group, instance, points_geo, scale=scale, rotation=rotation, realize=realize, x=x, y=y)

        if name == "cube":
            _check_no_extra_keywords(kws, {"size"})
            if len(expr.args) > 1:
                raise CompileError("cube(size) expects 0 or 1 positional argument")
            size_expr = expr.args[0] if expr.args else kws.get("size", ast.Constant(value=1.0))
            try:
                size = _const_eval(size_expr, self.consts)
            except CompileError:
                size = self.compile(size_expr)
            return _cube_geometry(self.group, size, x, y)

        if name == "join":
            if kws:
                raise CompileError("join() does not support keyword arguments")
            if len(expr.args) < 1:
                raise CompileError("join([geo_a, geo_b, ...]) or join(geo_a, geo_b, ...) expects Geometry")
            if len(expr.args) == 1 and isinstance(expr.args[0], (ast.List, ast.Tuple)):
                geos = [self.compile(arg) for arg in expr.args[0].elts]
            else:
                geos = [self.compile(arg) for arg in expr.args]
            if not geos:
                raise CompileError("join() expects at least one Geometry")
            return _join_geometry(self.group, geos, x, y)

        if name == "transform":
            _check_no_extra_keywords(kws, {"translation", "scale", "rotation"})
            if not expr.args or len(expr.args) > 3:
                raise CompileError("transform(geo, translation=..., scale=..., rotation=...) expects Geometry")
            geo = self.compile(expr.args[0])
            if geo.typ != TYPE_GEOMETRY:
                raise CompileError("transform() first argument must be Geometry")
            translation_expr = kws.get("translation", expr.args[1] if len(expr.args) > 1 else None)
            scale_expr = kws.get("scale", expr.args[2] if len(expr.args) > 2 else None)
            rotation_expr = kws.get("rotation", None)
            if translation_expr is not None:
                try:
                    translation = _const_eval(translation_expr, self.consts)
                except CompileError:
                    translation = self.compile(translation_expr)
            else:
                translation = None
            if scale_expr is not None:
                try:
                    scale = _const_eval(scale_expr, self.consts)
                except CompileError:
                    scale = self.compile(scale_expr)
            else:
                scale = None
            if rotation_expr is not None:
                try:
                    rotation = _const_eval(rotation_expr, self.consts)
                except CompileError:
                    rotation = self.compile(rotation_expr)
            else:
                rotation = None
            return _transform_geometry(self.group, geo, translation=translation, scale=scale, rotation=rotation, x=x, y=y)

        if name == "polyline":
            if kws:
                raise CompileError("polyline() does not support keyword arguments")
            if len(expr.args) != 1:
                raise CompileError("polyline(points) expects one compile-time list of vector points")
            points = self._const_eval_macro_arg(expr.args[0])
            return _polyline_geometry(self.group, points, x, y)


        if name == "realize_instances":
            if kws:
                raise CompileError("realize_instances() does not support keyword arguments")
            if len(expr.args) != 1:
                raise CompileError("realize_instances(geo) expects one Geometry")
            geo = self.compile(expr.args[0])
            return _realize_instances(self.group, geo, x, y)

        raise CompileError(f"Unsupported geometry macro: {name}")

def _make_group(source: str, name: str = "NodeForge Group", existing_group=None, local_functions=None):
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
    callable_names = library_function_names() | set(local_function_defs)
    input_names = sorted(set(_collect_inputs(stmts, extra_builtin_names=callable_names)) - set(consts.keys()))
    input_types = _infer_input_types(stmts)
    if existing_group is not None:
        if getattr(existing_group, "bl_idname", None) != "GeometryNodeTree":
            raise CompileError("Selected node group is not a GeometryNodeTree")
        group = existing_group
        _reset_node_group(group)
        # Preserve existing datablock name.
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
    comp = Compiler(group, group_input, consts, local_functions=local_function_defs, local_group_cache={})

    for socket in group_input.outputs:
        if socket.name in input_names:
            comp.vars[socket.name] = Value(socket, input_types.get(socket.name, TYPE_FLOAT))

    geometry_socket = None
    if geometry_mode:
        geometry_socket = next((s for s in group_input.outputs if s.name == "Geometry"), None)
        if geometry_socket is None:
            raise CompileError("Internal error: missing Geometry input")

    explicit_outputs = []  # [(name, Value)]
    output_names = set()
    auto_final_output = None

    def _as_array_iter_value(value):
        if isinstance(value, list):
            return value
        return None

    def _compile_statement(stmt, idx=0, allow_final_expr=False):
        nonlocal geometry_socket, auto_final_output
        call = _is_top_level_call(stmt)

        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                raise CompileError("Only simple assignments like name = value are supported")
            target = stmt.targets[0].id
            try:
                comp.consts[target] = _const_eval(stmt.value, comp.consts)
            except CompileError:
                comp.consts.pop(target, None)
            if isinstance(stmt.value, (ast.List, ast.Tuple)):
                if isinstance(stmt.value, ast.List) and not stmt.value.elts:
                    comp.consts.pop(target, None)
                comp.vars[target] = [comp.compile(e) for e in stmt.value.elts]
                auto_final_output = None
                return
            value = comp.compile(stmt.value)
            comp.vars[target] = value
            if isinstance(value, list):
                auto_final_output = None
            else:
                # Backward-compatible behavior: if there are no explicit output() calls,
                # the last assignment becomes the output.
                auto_final_output = (target, value)
            return

        if isinstance(stmt, ast.AugAssign):
            if not isinstance(stmt.target, ast.Name):
                raise CompileError("Only simple augmented assignments like name += value are supported")
            target = stmt.target.id
            if target not in comp.vars:
                raise CompileError(f"Unknown name for augmented assignment: {target}")
            bin_expr = ast.BinOp(left=ast.Name(id=target, ctx=ast.Load()), op=stmt.op, right=stmt.value)
            value = comp.compile(bin_expr)
            comp.vars[target] = value
            comp.consts.pop(target, None)
            if isinstance(value, list):
                auto_final_output = None
            else:
                auto_final_output = (target, value)
            return

        if isinstance(stmt, ast.Expr):
            expr = stmt.value
            if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "append":
                if not isinstance(expr.func.value, ast.Name) or len(expr.args) != 1:
                    raise CompileError("append must look like items.append(value)")
                list_name = expr.func.value.id
                arr = comp.vars.get(list_name)
                if not isinstance(arr, list):
                    raise CompileError(f"{list_name} is not an array")
                arr.append(comp.compile(expr.args[0]))
                comp.consts.pop(list_name, None)
                auto_final_output = None
                return
            # Let top-level output/store/set_position handlers below process their calls.
            if not (call and call.func.id in {"store", "set_position", "output"}):
                if not allow_final_expr:
                    raise CompileError("Only assignments, array append, for/if blocks, store(), set_position() and output() may appear before the final expression")
                value = comp.compile(expr)
                if isinstance(value, list):
                    raise CompileError("A final expression cannot be an array; use join(array) or index it")
                auto_final_output = ("out", value)
                return

        if isinstance(stmt, ast.For):
            if isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id == "runtime_range":
                iterations_expr, body = _parse_runtime_range_for(stmt)
                iterations = comp.compile(iterations_expr)
                if iterations.typ != TYPE_INT:
                    raise CompileError("runtime_range(n) expects an Int input or integer value")
                results = _repeat_scalar_assignments(group, comp, iterations, body, index_name=stmt.target.id, x=300 + idx * 160, y=-380 - idx * 70)
                if results:
                    last_name = list(results.keys())[-1]
                    auto_final_output = (last_name, results[last_name])
                return

            # Script-level array iteration: for item in items: ...
            iter_values = None
            if isinstance(stmt.iter, ast.Name) and stmt.iter.id in comp.vars:
                iter_values = _as_array_iter_value(comp.vars[stmt.iter.id])
            if iter_values is None:
                try:
                    raw_iter = _const_eval(stmt.iter, consts)
                    if isinstance(raw_iter, (list, tuple)):
                        iter_values = [comp._compile_const_value(v, 260 + idx * 120, -220 - idx * 50) for v in raw_iter]
                except CompileError:
                    iter_values = None
            if iter_values is not None:
                def _target_names(target):
                    if isinstance(target, ast.Name):
                        return [target.id]
                    if isinstance(target, (ast.Tuple, ast.List)) and all(isinstance(e, ast.Name) for e in target.elts):
                        return [e.id for e in target.elts]
                    raise CompileError("array for target must be a simple name or tuple of names")

                target_names = _target_names(stmt.target)
                old_values = {name: comp.vars.get(name) for name in target_names}
                had_old = {name: name in comp.vars for name in target_names}
                try:
                    for item in iter_values:
                        if len(target_names) == 1:
                            comp.vars[target_names[0]] = item
                        else:
                            if not isinstance(item, list) or len(item) != len(target_names):
                                raise CompileError("tuple unpack in for loop needs matching tuple/list item length")
                            for name, val in zip(target_names, item):
                                comp.vars[name] = val
                        for sub in stmt.body:
                            _compile_statement(sub, idx, allow_final_expr=False)
                finally:
                    for name in target_names:
                        if had_old[name]:
                            comp.vars[name] = old_values[name]
                        else:
                            comp.vars.pop(name, None)
                return

            geom_name, iterations_expr, body_expr = _parse_runtime_for(stmt, consts)
            if geom_name not in comp.vars or not isinstance(comp.vars[geom_name], Value) or comp.vars[geom_name].typ != TYPE_GEOMETRY:
                raise CompileError("runtime for requires an existing Geometry variable, e.g. geo = cube(1)")
            iterations = comp.compile(iterations_expr)
            if iterations.typ != TYPE_INT:
                raise CompileError("range(steps) expects an Int input or integer constant")
            geo = comp.vars[geom_name]
            new_geo = _repeat_geometry_assignment(group, comp, geom_name, geo, iterations, body_expr, 300 + idx * 160, -380 - idx * 70)
            comp.vars[geom_name] = new_geo
            auto_final_output = (geom_name, new_geo)
            return

        if isinstance(stmt, ast.If):
            # Static if is unrolled at compile time when possible.
            try:
                branch = stmt.body if bool(_const_eval(stmt.test, consts)) else stmt.orelse
                for sub in branch:
                    _compile_statement(sub, idx, allow_final_expr=False)
                return
            except CompileError:
                pass
            if not stmt.orelse:
                raise CompileError("runtime if currently requires an else branch")

            cond = comp.compile(stmt.test)
            base_vars = dict(comp.vars)
            saved_auto = auto_final_output

            def _compile_runtime_if_branch(branch_stmts):
                nonlocal auto_final_output
                comp.vars.clear(); comp.vars.update(base_vars)
                auto_final_output = saved_auto
                for sub in branch_stmts:
                    _compile_statement(sub, idx, allow_final_expr=False)
                branch_vars = dict(comp.vars)
                changed = {
                    name for name, value in branch_vars.items()
                    if name not in base_vars or base_vars.get(name) is not value
                }
                comp.vars.clear(); comp.vars.update(base_vars)
                auto_final_output = saved_auto
                return branch_vars, changed

            true_vars, true_changed = _compile_runtime_if_branch(stmt.body)
            false_vars, false_changed = _compile_runtime_if_branch(stmt.orelse)
            common_changed = sorted(true_changed & false_changed)
            if not common_changed:
                raise CompileError("runtime if branches must assign at least one common variable")

            last_target = None
            for target in common_changed:
                true_val = true_vars[target]
                false_val = false_vars[target]
                if isinstance(true_val, list) or isinstance(false_val, list):
                    raise CompileError("runtime if cannot assign arrays")
                if not isinstance(true_val, Value) or not isinstance(false_val, Value):
                    raise CompileError("runtime if branches must assign node values")
                if true_val.typ != false_val.typ:
                    raise CompileError(f"runtime if branch values for {target} have different types")
                merged = _switch(group, cond, false_val, true_val, 360 + idx * 160, -220 - idx * 70)
                comp.vars[target] = merged
                last_target = target

            if last_target is not None:
                auto_final_output = (last_target, comp.vars[last_target])
            return

        if call and call.func.id == "store":
            if geometry_socket is None:
                raise CompileError("Internal error: store() requires geometry mode")
            if len(call.args) != 2:
                raise CompileError('store("attribute_name", value, selection=..., domain="POINT", type="FLOAT") expects 2 positional arguments')
            kws = _kw_dict(call)
            _check_no_extra_keywords(kws, {"selection", "domain", "type"})
            attr_name = _literal_string(call.args[0], "store() attribute name")
            value = comp.compile(call.args[1])
            if isinstance(value, list):
                raise CompileError("store() value cannot be an array")
            selection = _selection_kw(comp, kws)
            domain = _optional_string_kw(kws, "domain", "POINT")
            data_type_override = _optional_string_kw(kws, "type", None)
            geometry_socket = _store_named_attribute(group, geometry_socket, attr_name, value, selection, domain, data_type_override, 520 + idx * 130, -260 - idx * 60)
            auto_final_output = None
            return

        if call and call.func.id == "set_position":
            if geometry_socket is None:
                raise CompileError("Internal error: set_position() requires geometry mode")
            if len(call.args) != 1:
                raise CompileError("set_position(position_vector, selection=...) expects exactly one positional argument")
            kws = _kw_dict(call)
            _check_no_extra_keywords(kws, {"selection"})
            pos = comp.compile(call.args[0])
            selection = _selection_kw(comp, kws)
            geometry_socket = _set_position_node(group, geometry_socket, pos, selection, 520 + idx * 130, -40 - idx * 60)
            auto_final_output = None
            return

        if call and call.func.id == "output":
            if call.keywords:
                kws = _kw_dict(call)
                _check_no_extra_keywords(kws, {"name", "value"})
                if call.args:
                    raise CompileError('output() cannot mix positional and keyword arguments')
                if "value" not in kws:
                    raise CompileError('output(name="Name", value=value) expects value=...')
                if "name" in kws:
                    out_name = _unique_output_name(output_names, _literal_string(kws["name"], "output() name"))
                else:
                    out_name = _unique_output_name(output_names, "out")
                value_expr = kws["value"]
            elif len(call.args) == 1:
                out_name = _unique_output_name(output_names, "out")
                value_expr = call.args[0]
            elif len(call.args) == 2:
                out_name = _unique_output_name(output_names, _literal_string(call.args[0], "output() name"))
                value_expr = call.args[1]
            else:
                raise CompileError('output(value), output("Name", value), or output(name="Name", value=value) expected')
            value = comp.compile(value_expr)
            if isinstance(value, list):
                raise CompileError("output() cannot output an array directly; use join(array) or index it")
            explicit_outputs.append((out_name, value))
            auto_final_output = None
            return

        raise CompileError("Unsupported statement")

    for idx, stmt in enumerate(stmts):
        _compile_statement(stmt, idx, allow_final_expr=(idx == len(stmts) - 1))

    if geometry_mode:
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        group.links.new(geometry_socket, group_output.inputs["Geometry"])

    if explicit_outputs:
        outputs = explicit_outputs
    elif auto_final_output is not None:
        outputs = [auto_final_output]
    else:
        outputs = []

    # Keep output names unique against Geometry and each other.
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
    """Function `create_expression_group` used by the NodeForge addon."""
    return _make_group(source, name)


def create_library_function_group(name: str):
    """Create or update a reusable node group for a function-library entry."""
    return materialize_library_function_group(name, _make_group)

def update_expression_group(group, source: str):
    """Function `update_expression_group` used by the NodeForge addon."""
    return _make_group(source, getattr(group, "name", "NodeForge Group"), existing_group=group)

__all__ = [
    "CompileError",
    "create_expression_group",
    "update_expression_group",
    "create_library_function_group",
    "_apply_group_defaults_to_node",
    "_extract_group_source",
    "_get_or_create_scratch_text",
    "_replace_text_contents",
]
