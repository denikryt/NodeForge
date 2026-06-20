"""Public compiler facade and high-level Geometry Nodes group assembly."""

import ast
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
from .storage import _reset_node_group, _store_group_source, _extract_group_source, _get_or_create_scratch_text, _replace_text_contents
from .storage import INPUT_DEFAULTS_PROP
from .interface import _set_socket_default, _set_interface_socket_default, _record_group_input_default
from .update import _apply_group_defaults_to_node, _capture_node_external_state, _restore_node_external_state

class Compiler:
    """Class `Compiler` used by the GN Script MVP addon."""
    def __init__(self, group, group_input, consts=None):
        """Function `__init__` used by the GN Script MVP addon."""
        self.group = group
        self.group_input = group_input
        self.vars = {}
        self.consts = consts or {}
        self.depth = 0

    def compile(self, expr):
        """Function `compile` used by the GN Script MVP addon."""
        self.depth += 1
        try:
            return self._compile(expr, self.depth)
        finally:
            self.depth -= 1

    def _compile(self, expr, depth=0):
        """Function `_compile` used by the GN Script MVP addon."""
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
                if val.typ == TYPE_FLOAT:
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
            if len(expr.ops) != 1 or len(expr.comparators) != 1:
                raise CompileError("Chained comparisons are not supported")
            left = self._compile(expr.left, depth + 1)
            right = self._compile(expr.comparators[0], depth + 1)
            op = _COMPARE_OPS.get(type(expr.ops[0]))
            if not op:
                raise CompileError("Unsupported comparison operator")
            return _compare(self.group, op, left, right, x, y)

        if isinstance(expr, ast.Call):
            if not isinstance(expr.func, ast.Name):
                raise CompileError("Only simple function calls are supported")
            name = expr.func.id
            macro_names = {"cube", "copy_by_offsets", "join", "transform", "polyline", "realize_instances", "input_geometry", "input_float", "input_int", "input_bool", "input_vector"}
            if expr.keywords and name not in macro_names:
                raise CompileError("Keyword arguments are only supported for geometry macros")
            if name in macro_names:
                return self._compile_geometry_macro(expr, depth)
            if name == "vector":
                if expr.keywords:
                    raise CompileError("vector() does not support keyword arguments")
                if len(expr.args) != 3:
                    raise CompileError("vector(x, y, z) expects 3 arguments")
                comps = []
                for comp_expr in expr.args:
                    try:
                        comps.append(_as_float_const(_const_eval(comp_expr, self.consts), "vector component"))
                    except CompileError:
                        comps.append(self._compile(comp_expr, depth + 1))
                return _combine_xyz_mixed(self.group, comps, x, y)

            args = [self._compile(arg, depth + 1) for arg in expr.args]

            if name in _FLOAT_FUNCS_1:
                if len(args) != 1: raise CompileError(f"{name}() expects 1 argument")
                return _math(self.group, _FLOAT_FUNCS_1[name], args, x, y)
            if name in _FLOAT_FUNCS_2:
                if len(args) != 2: raise CompileError(f"{name}() expects 2 arguments")
                return _math(self.group, _FLOAT_FUNCS_2[name], args, x, y)
            if name == "clamp":
                if len(args) != 3: raise CompileError("clamp(value, min, max) expects 3 arguments")
                return _clamp(self.group, args[0], args[1], args[2], x, y)
            if name in {"mix", "lerp"}:
                if len(args) != 3: raise CompileError("mix(a, b, factor) expects 3 arguments")
                return _mix(self.group, args[0], args[1], args[2], x, y)
            if name == "select":
                if len(args) != 3: raise CompileError("select(cond, false, true) expects 3 arguments")
                return _switch(self.group, args[0], args[1], args[2], x, y)
            if name == "position":
                if args: raise CompileError("position() expects no arguments")
                return _position(self.group, x, y)
            if name == "normal":
                if args: raise CompileError("normal() expects no arguments")
                return _normal(self.group, x, y)
            if name == "index":
                if args: raise CompileError("index() expects no arguments")
                return _index(self.group, x, y)
            if name == "id":
                if args: raise CompileError("id() expects no arguments")
                return _id(self.group, x, y)
            if name == "map_range":
                return _map_range(self.group, args, x, y)
            if name in _VECTOR_MATH_FLOAT_OUTPUT:
                expected = 2 if name in {"distance", "dot"} else 1
                if len(args) != expected: raise CompileError(f"{name}() expects {expected} argument(s)")
                return _vector_math(self.group, _VECTOR_MATH_FLOAT_OUTPUT[name], args, TYPE_FLOAT, x, y)
            if name in _VECTOR_MATH_VECTOR_OUTPUT_1:
                if len(args) != 1: raise CompileError(f"{name}() expects 1 argument")
                return _vector_math(self.group, _VECTOR_MATH_VECTOR_OUTPUT_1[name], args, TYPE_VECTOR, x, y)
            if name in _VECTOR_MATH_VECTOR_OUTPUT_2:
                if len(args) != 2: raise CompileError(f"{name}() expects 2 arguments")
                return _vector_math(self.group, _VECTOR_MATH_VECTOR_OUTPUT_2[name], args, TYPE_VECTOR, x, y)
            if name in {"set_position", "output", "store"}:
                raise CompileError(f"{name}() is only supported as a top-level call")
            raise CompileError(f"Unsupported function: {name}")

        raise CompileError(f"Unsupported expression element: {type(expr).__name__}")




    def _create_input_socket_value(self, name, typ, default=None):
        """Function `_create_input_socket_value` used by the GN Script MVP addon."""
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
        """Function `_const_eval_macro_arg` used by the GN Script MVP addon."""
        return _const_eval(expr, self.consts)

    def _compile_geometry_macro(self, expr, depth=0):
        """Function `_compile_geometry_macro` used by the GN Script MVP addon."""
        name = expr.func.id
        x = depth * 240
        y = -depth * 90
        kws = _kw_dict(expr)

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

        if name == "copy_by_offsets":
            _check_no_extra_keywords(kws, {"scale"})
            if len(expr.args) < 2 or len(expr.args) > 3:
                raise CompileError("copy_by_offsets(geo, offsets, scale=...) expects Geometry and compile-time offsets list")
            geo = self.compile(expr.args[0])
            offsets = self._const_eval_macro_arg(expr.args[1])
            scale_expr = kws.get("scale", expr.args[2] if len(expr.args) == 3 else ast.Constant(value=1.0/3.0))
            try:
                scale = _const_eval(scale_expr, self.consts)
            except CompileError:
                scale = self.compile(scale_expr)
            return _copy_by_offsets(self.group, geo, offsets, scale, x, y)

        raise CompileError(f"Unsupported geometry macro: {name}")

def _make_group(source: str, name: str = "GN Script Expression", existing_group=None):
    """Function `_make_group` used by the GN Script MVP addon."""
    raw_stmts = _parse_source(source)
    stmts, consts = _preprocess_compile_time(raw_stmts)
    input_names = sorted(set(_collect_inputs(stmts)) - set(consts.keys()))
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
    comp = Compiler(group, group_input, consts)

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

    for idx, stmt in enumerate(stmts):
        call = _is_top_level_call(stmt)

        if isinstance(stmt, ast.Assign):
            comp.vars[stmt.targets[0].id] = comp.compile(stmt.value)
            # Backward-compatible behavior: if there are no explicit output() calls,
            # the last assignment becomes the output.
            auto_final_output = (stmt.targets[0].id, comp.vars[stmt.targets[0].id])
            continue

        if isinstance(stmt, ast.For):
            geom_name, iterations_expr, body_expr = _parse_runtime_for(stmt, consts)
            if geom_name not in comp.vars or comp.vars[geom_name].typ != TYPE_GEOMETRY:
                raise CompileError("runtime for requires an existing Geometry variable, e.g. geo = cube(1)")
            iterations = comp.compile(iterations_expr)
            if iterations.typ != TYPE_INT:
                raise CompileError("range(steps) expects an Int input or integer constant")
            geo = comp.vars[geom_name]
            new_geo = _repeat_geometry_assignment(group, comp, geom_name, geo, iterations, body_expr, 300 + idx * 160, -380 - idx * 70)
            comp.vars[geom_name] = new_geo
            auto_final_output = (geom_name, new_geo)
            continue

        if call and call.func.id == "store":
            if geometry_socket is None:
                raise CompileError("Internal error: store() requires geometry mode")
            if len(call.args) != 2:
                raise CompileError('store("attribute_name", value, selection=..., domain="POINT", type="FLOAT") expects 2 positional arguments')
            kws = _kw_dict(call)
            _check_no_extra_keywords(kws, {"selection", "domain", "type"})
            attr_name = _literal_string(call.args[0], "store() attribute name")
            value = comp.compile(call.args[1])
            selection = _selection_kw(comp, kws)
            domain = _optional_string_kw(kws, "domain", "POINT")
            data_type_override = _optional_string_kw(kws, "type", None)
            geometry_socket = _store_named_attribute(group, geometry_socket, attr_name, value, selection, domain, data_type_override, 520 + idx * 130, -260 - idx * 60)
            auto_final_output = None
            continue

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
            continue

        if call and call.func.id == "output":
            if len(call.args) == 1:
                out_name = _unique_output_name(output_names, "out")
                value_expr = call.args[0]
            elif len(call.args) == 2:
                out_name = _unique_output_name(output_names, _literal_string(call.args[0], "output() name"))
                value_expr = call.args[1]
            else:
                raise CompileError('output(value) or output("Name", value) expected')
            value = comp.compile(value_expr)
            explicit_outputs.append((out_name, value))
            auto_final_output = None
            continue

        if isinstance(stmt, ast.Expr):
            # Backward-compatible final expression support. If not last, require output()/store()/set_position().
            if idx != len(stmts) - 1:
                raise CompileError("Only assignments, store(), set_position() and output() may appear before the final expression")
            value = comp.compile(stmt.value)
            auto_final_output = ("out", value)
            continue

        raise CompileError("Unsupported statement")

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
        final_name = _unique_output_name(used_interface_names, output_name)
        group.interface.new_socket(name=final_name, in_out="OUTPUT", socket_type=_socket_type_for(result.typ))
        group.links.new(result.socket, group_output.inputs[final_name])

    if not geometry_mode and not outputs:
        raise CompileError("Script produced no output. Use out = ..., output(...), set_position(...), or store(...)")

    return group

def create_expression_group(source: str, name: str = "GN Script Expression"):
    """Function `create_expression_group` used by the GN Script MVP addon."""
    return _make_group(source, name)

def update_expression_group(group, source: str):
    """Function `update_expression_group` used by the GN Script MVP addon."""
    return _make_group(source, getattr(group, "name", "GN Script Expression"), existing_group=group)

__all__ = [
    "CompileError",
    "create_expression_group",
    "update_expression_group",
    "_apply_group_defaults_to_node",
    "_extract_group_source",
    "_get_or_create_scratch_text",
    "_replace_text_contents",
]
