bl_info = {
    "name": "GN Script MVP",
    "author": "nachitima",
    "version": (0, 16, 0),
    "blender": (5, 2, 0),
    "location": "Geometry Nodes Editor > Sidebar > GN Script; Add Menu > Script > Expression/Vector Group",
    "description": "Compile a small Python-like expression language into real Geometry Nodes node groups, with selected node group update/load support, optimized inline constants, explicit input sockets, dynamic vector copy scale, optimized mixed vectors, embedded source storage, reliable input defaults, update link preservation, corrected per-copy offset scaling, low-level transform()/join(), join(list), and generic runtime geometry assignment in Repeat Zones.",
    "category": "Node",
}

import ast
import math
import bpy
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import StringProperty, PointerProperty

TYPE_FLOAT = "FLOAT"
TYPE_VECTOR = "VECTOR"
TYPE_BOOL = "BOOL"
TYPE_GEOMETRY = "GEOMETRY"
TYPE_INT = "INT"

_ALLOWED_CONSTS = {"pi": math.pi, "tau": math.tau, "e": math.e}

_FLOAT_FUNCS_1 = {
    "sin": "SINE", "cos": "COSINE", "tan": "TANGENT",
    "asin": "ARCSINE", "acos": "ARCCOSINE", "atan": "ARCTANGENT",
    "sqrt": "SQRT", "abs": "ABSOLUTE", "floor": "FLOOR", "ceil": "CEIL",
    "round": "ROUND", "fract": "FRACT", "frac": "FRACT", "radians": "RADIANS", "degrees": "DEGREES",
    "exp": "EXPONENT", "ln": "NATURAL_LOGARITHM", "sign": "SIGN",
}
_FLOAT_FUNCS_2 = {"min": "MINIMUM", "max": "MAXIMUM", "pow": "POWER", "log": "LOGARITHM", "atan2": "ARCTANGENT2", "mod": "MODULO"}
_BIN_OPS = {ast.Add: "ADD", ast.Sub: "SUBTRACT", ast.Mult: "MULTIPLY", ast.Div: "DIVIDE", ast.Pow: "POWER", ast.Mod: "MODULO"}
_COMPARE_OPS = {ast.Lt: "LESS_THAN", ast.LtE: "LESS_EQUAL", ast.Gt: "GREATER_THAN", ast.GtE: "GREATER_EQUAL", ast.Eq: "EQUAL", ast.NotEq: "NOT_EQUAL"}
_BOOLEAN_OPS = {ast.And: "AND", ast.Or: "OR"}
_VECTOR_MATH_FLOAT_OUTPUT = {"length": "LENGTH", "distance": "DISTANCE", "dot": "DOT_PRODUCT"}
_VECTOR_MATH_VECTOR_OUTPUT_1 = {"normalize": "NORMALIZE"}
_VECTOR_MATH_VECTOR_OUTPUT_2 = {"cross": "CROSS_PRODUCT", "reflect": "REFLECT", "project": "PROJECT"}
_BUILTIN_NAMES = set(_FLOAT_FUNCS_1) | set(_FLOAT_FUNCS_2) | set(_VECTOR_MATH_FLOAT_OUTPUT) | set(_VECTOR_MATH_VECTOR_OUTPUT_1) | set(_VECTOR_MATH_VECTOR_OUTPUT_2) | {
    "clamp", "mix", "lerp", "select", "vector", "position", "normal", "index", "id",
    "set_position", "output", "store", "mod", "frac", "map_range", "sign", "range", "cube", "copy_by_offsets", "join", "transform", "realize_instances", "input_geometry", "input_float", "input_int", "input_bool", "input_vector"
}

class CompileError(Exception):
    pass

class Value:
    def __init__(self, socket, typ):
        self.socket = socket
        self.typ = typ


def _socket_type_for(typ):
    return {
        TYPE_FLOAT: "NodeSocketFloat",
        TYPE_VECTOR: "NodeSocketVector",
        TYPE_BOOL: "NodeSocketBool",
        TYPE_GEOMETRY: "NodeSocketGeometry",
        TYPE_INT: "NodeSocketInt",
    }[typ]


def _new_node(group, bl_idname, x=0, y=0):
    node = group.nodes.new(bl_idname)
    node.location = (x, y)
    return node


def _value(group, value, x=0, y=0):
    node = _new_node(group, "ShaderNodeValue", x, y)
    node.label = str(value)
    node.outputs[0].default_value = float(value)
    return Value(node.outputs[0], TYPE_FLOAT)


def _is_number_type(typ):
    return typ in {TYPE_FLOAT, TYPE_INT}

def _math(group, operation, args, x=0, y=0):
    node = _new_node(group, "ShaderNodeMath", x, y)
    node.operation = operation
    for i, arg in enumerate(args):
        if not _is_number_type(arg.typ):
            raise CompileError(f"Math operation {operation} expects numeric input")
        group.links.new(arg.socket, node.inputs[i])
    return Value(node.outputs[0], TYPE_FLOAT)


def _vector_math(group, operation, args, out_type=TYPE_VECTOR, x=0, y=0):
    node = _new_node(group, "ShaderNodeVectorMath", x, y)
    node.operation = operation
    vi = 0
    for arg in args:
        if arg.typ == TYPE_VECTOR:
            group.links.new(arg.socket, node.inputs[vi])
            vi += 1
        elif _is_number_type(arg.typ):
            group.links.new(arg.socket, node.inputs[3])
        else:
            raise CompileError(f"Vector Math {operation} got unsupported type {arg.typ}")
    return Value(node.outputs[1 if out_type == TYPE_FLOAT else 0], out_type)


def _combine_xyz(group, xval, yval, zval, x=0, y=0):
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
        if isinstance(val, Value):
            if not _is_number_type(val.typ):
                raise CompileError("vector(x, y, z) expects numeric arguments")
            group.links.new(val.socket, node.inputs[i])
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            node.inputs[i].default_value = float(val)
        else:
            raise CompileError("vector(x, y, z) expects numeric arguments")
    return Value(node.outputs[0], TYPE_VECTOR)


def _separate_xyz(group, val, component, x=0, y=0):
    if val.typ != TYPE_VECTOR:
        raise CompileError(".x/.y/.z can only be used on Vector values")
    node = _new_node(group, "ShaderNodeSeparateXYZ", x, y)
    group.links.new(val.socket, node.inputs[0])
    return Value(node.outputs[{"x": 0, "y": 1, "z": 2}[component]], TYPE_FLOAT)


def _compare(group, operation, left, right, x=0, y=0):
    if _is_number_type(left.typ) and _is_number_type(right.typ):
        data_type = "FLOAT"
    elif left.typ == right.typ and left.typ in {TYPE_BOOL, TYPE_VECTOR}:
        data_type = {TYPE_BOOL: "BOOLEAN", TYPE_VECTOR: "VECTOR"}[left.typ]
    else:
        raise CompileError("Comparison inputs must both be numeric, both Bool, or both Vector")
    node = _new_node(group, "FunctionNodeCompare", x, y)
    node.operation = operation
    node.data_type = data_type
    group.links.new(left.socket, node.inputs[0])
    group.links.new(right.socket, node.inputs[1])
    return Value(node.outputs[0], TYPE_BOOL)


def _boolean_math(group, operation, args, x=0, y=0):
    node = _new_node(group, "FunctionNodeBooleanMath", x, y)
    node.operation = operation
    for i, arg in enumerate(args):
        if arg.typ != TYPE_BOOL:
            raise CompileError("Boolean operations expect Bool values")
        group.links.new(arg.socket, node.inputs[i])
    return Value(node.outputs[0], TYPE_BOOL)


def _switch(group, cond, false_val, true_val, x=0, y=0):
    if cond.typ != TYPE_BOOL:
        raise CompileError("select(cond, false, true): cond must be Bool")
    if false_val.typ != true_val.typ:
        raise CompileError("select() true/false values must have same type")
    node = _new_node(group, "GeometryNodeSwitch", x, y)
    node.input_type = {TYPE_FLOAT: "FLOAT", TYPE_INT: "INT", TYPE_VECTOR: "VECTOR", TYPE_BOOL: "BOOLEAN", TYPE_GEOMETRY: "GEOMETRY"}[false_val.typ]
    group.links.new(cond.socket, node.inputs[0])
    group.links.new(false_val.socket, node.inputs[1])
    group.links.new(true_val.socket, node.inputs[2])
    return Value(node.outputs[0], false_val.typ)


def _mix(group, a, b, factor, x=0, y=0):
    if a.typ != b.typ or a.typ not in {TYPE_FLOAT, TYPE_VECTOR}:
        raise CompileError("mix(a, b, factor) supports Float or Vector a/b of same type")
    if not _is_number_type(factor.typ):
        raise CompileError("mix() factor must be numeric")
    node = _new_node(group, "ShaderNodeMix", x, y)
    node.data_type = "VECTOR" if a.typ == TYPE_VECTOR else "FLOAT"
    node.factor_mode = "UNIFORM"
    node.clamp_factor = True
    group.links.new(factor.socket, node.inputs[0])
    if a.typ == TYPE_FLOAT:
        group.links.new(a.socket, node.inputs[2]); group.links.new(b.socket, node.inputs[3]); out_i = 0
    else:
        group.links.new(a.socket, node.inputs[4]); group.links.new(b.socket, node.inputs[5]); out_i = 1
    return Value(node.outputs[out_i], a.typ)


def _clamp(group, val, minv, maxv, x=0, y=0):
    if not (_is_number_type(val.typ) and _is_number_type(minv.typ) and _is_number_type(maxv.typ)):
        raise CompileError("clamp(value, min, max) expects numeric arguments")
    node = _new_node(group, "ShaderNodeClamp", x, y)
    group.links.new(val.socket, node.inputs[0]); group.links.new(minv.socket, node.inputs[1]); group.links.new(maxv.socket, node.inputs[2])
    return Value(node.outputs[0], TYPE_FLOAT)


def _position(group, x=0, y=0):
    node = _new_node(group, "GeometryNodeInputPosition", x, y)
    return Value(node.outputs[0], TYPE_VECTOR)


def _normal(group, x=0, y=0):
    node = _new_node(group, "GeometryNodeInputNormal", x, y)
    return Value(node.outputs[0], TYPE_VECTOR)

def _index(group, x=0, y=0):
    node = _new_node(group, "GeometryNodeInputIndex", x, y)
    return Value(node.outputs[0], TYPE_INT)

def _id(group, x=0, y=0):
    node = _new_node(group, "GeometryNodeInputID", x, y)
    return Value(node.outputs[0], TYPE_INT)

def _map_range(group, args, x=0, y=0):
    if len(args) != 5:
        raise CompileError("map_range(value, from_min, from_max, to_min, to_max) expects 5 arguments")
    if not all(_is_number_type(arg.typ) for arg in args):
        raise CompileError("map_range() currently supports numeric arguments")
    node = _new_node(group, "ShaderNodeMapRange", x, y)
    node.data_type = "FLOAT"
    for i, arg in enumerate(args):
        group.links.new(arg.socket, node.inputs[i])
    return Value(node.outputs[0], TYPE_FLOAT)


def _ensure_float(v):
    if v.typ != TYPE_FLOAT:
        raise CompileError("Expected Float")


def _parse_source(source: str):
    source = (source or "").strip()
    if not source:
        raise CompileError("Script is empty")
    tree = ast.parse(source, mode="exec")
    allowed = (ast.Assign, ast.Expr, ast.For, ast.If)
    if not tree.body or any(not isinstance(stmt, allowed) for stmt in tree.body):
        raise CompileError("Only assignments, for/if blocks, and expression/call statements are supported")
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                raise CompileError("Assignment target must be a simple name, e.g. out = sin(x)")
    return tree.body


def _assigned_names(stmts):
    return {stmt.targets[0].id for stmt in stmts if isinstance(stmt, ast.Assign)}


def _collect_external_names(node, assigned, names):
    if isinstance(node, ast.Name):
        if isinstance(node.ctx, ast.Load) and node.id not in assigned and node.id not in _BUILTIN_NAMES and node.id not in _ALLOWED_CONSTS:
            names.add(node.id)
        return
    for child in ast.iter_child_nodes(node):
        _collect_external_names(child, assigned, names)


def _collect_inputs(stmts):
    assigned = _assigned_names(stmts)
    names = set()
    for stmt in stmts:
        _collect_external_names(stmt, assigned, names)
    return sorted(names)


class Compiler:
    def __init__(self, group, group_input, consts=None):
        self.group = group
        self.group_input = group_input
        self.vars = {}
        self.consts = consts or {}
        self.depth = 0

    def compile(self, expr):
        self.depth += 1
        try:
            return self._compile(expr, self.depth)
        finally:
            self.depth -= 1

    def _compile(self, expr, depth=0):
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
            macro_names = {"cube", "copy_by_offsets", "join", "transform", "realize_instances", "input_geometry", "input_float", "input_int", "input_bool", "input_vector"}
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
        return _const_eval(expr, self.consts)

    def _compile_geometry_macro(self, expr, depth=0):
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
            _check_no_extra_keywords(kws, {"translation", "scale"})
            if not expr.args:
                raise CompileError("transform(geo, translation=..., scale=...) expects Geometry")
            geo = self.compile(expr.args[0])
            if geo.typ != TYPE_GEOMETRY:
                raise CompileError("transform() first argument must be Geometry")
            translation_expr = kws.get("translation", expr.args[1] if len(expr.args) > 1 else None)
            scale_expr = kws.get("scale", expr.args[2] if len(expr.args) > 2 else None)
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
            return _transform_geometry(self.group, geo, translation=translation, scale=scale, x=x, y=y)

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

def _literal_string(expr, context="argument"):
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str) and expr.value:
        return expr.value
    raise CompileError(f"Expected a non-empty string literal for {context}")


def _is_top_level_call(stmt, names=None):
    if not isinstance(stmt, ast.Expr):
        return None
    call = stmt.value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
        return None
    if names is not None and call.func.id not in names:
        return None
    return call


def _top_level_call_name(stmt):
    call = _is_top_level_call(stmt)
    return call.func.id if call else None


def _needs_geometry_io(stmts):
    return any(_top_level_call_name(stmt) in {"set_position", "store"} for stmt in stmts)


def _attribute_data_type(typ):
    return {
        TYPE_FLOAT: "FLOAT",
        TYPE_INT: "INT",
        TYPE_VECTOR: "FLOAT_VECTOR",
        TYPE_BOOL: "BOOLEAN",
    }.get(typ)

_ALLOWED_STORE_TYPES = {
    "FLOAT": "FLOAT",
    "INT": "INT",
    "INTEGER": "INT",
    "VECTOR": "FLOAT_VECTOR",
    "FLOAT_VECTOR": "FLOAT_VECTOR",
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
}
_ALLOWED_DOMAINS = {"POINT", "EDGE", "FACE", "CORNER", "CURVE", "INSTANCE"}

def _kw_dict(call):
    result = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise CompileError("**kwargs are not supported")
        if kw.arg in result:
            raise CompileError(f"Duplicate keyword argument: {kw.arg}")
        result[kw.arg] = kw.value
    return result

def _optional_string_kw(kws, name, default=None):
    if name not in kws:
        return default
    return _literal_string(kws[name], f"{name}=")

def _selection_kw(comp, kws, default=None):
    if "selection" not in kws:
        return default
    selection = comp.compile(kws["selection"])
    if selection.typ != TYPE_BOOL:
        raise CompileError("selection= must be a Bool expression")
    return selection

def _check_no_extra_keywords(kws, allowed):
    extra = set(kws) - set(allowed)
    if extra:
        raise CompileError("Unsupported keyword argument(s): " + ", ".join(sorted(extra)))

def _store_named_attribute(group, geometry_socket, attr_name, value, selection=None, domain="POINT", data_type_override=None, x=0, y=0):
    if data_type_override:
        data_type = _ALLOWED_STORE_TYPES.get(data_type_override.upper())
        if data_type is None:
            raise CompileError("Unsupported store() type. Use FLOAT, INT, VECTOR or BOOLEAN")
    else:
        data_type = _attribute_data_type(value.typ)
    if data_type is None:
        raise CompileError("store(name, value) supports Float, Int, Vector and Bool values")
    domain = (domain or "POINT").upper()
    if domain not in _ALLOWED_DOMAINS:
        raise CompileError("Unsupported store() domain. Use POINT, EDGE, FACE, CORNER, CURVE or INSTANCE")
    node = _new_node(group, "GeometryNodeStoreNamedAttribute", x, y)
    node.data_type = data_type
    try:
        node.domain = domain
    except Exception:
        pass
    node.inputs[1].default_value = True
    node.inputs[2].default_value = attr_name
    group.links.new(geometry_socket, node.inputs[0])
    if selection is not None:
        group.links.new(selection.socket, node.inputs[1])
    group.links.new(value.socket, node.inputs[3])
    return node.outputs[0]


def _set_position_node(group, geometry_socket, pos, selection=None, x=0, y=0):
    if pos.typ != TYPE_VECTOR:
        raise CompileError("set_position() expects a Vector argument")
    node = _new_node(group, "GeometryNodeSetPosition", x, y)
    group.links.new(geometry_socket, node.inputs[0])
    if selection is not None:
        group.links.new(selection.socket, node.inputs[1])
    group.links.new(pos.socket, node.inputs[2])
    return node.outputs[0]


def _unique_output_name(existing, requested):
    base = requested or "out"
    name = base
    idx = 2
    while name in existing:
        name = f"{base}_{idx}"
        idx += 1
    existing.add(name)
    return name




# -----------------------------
# Compile-time subset and geometry macros
# -----------------------------

class ConstVector(tuple):
    pass


def _is_const_vector(v):
    return isinstance(v, ConstVector) and len(v) == 3


def _as_float_const(v, context="value"):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    raise CompileError(f"Expected numeric compile-time {context}")


def _const_eval(expr, env):
    if isinstance(expr, ast.Constant):
        if isinstance(expr.value, (int, float, bool, str)):
            return expr.value
        raise CompileError("Unsupported compile-time constant")
    if isinstance(expr, ast.Name):
        if expr.id in env:
            return env[expr.id]
        if expr.id in _ALLOWED_CONSTS:
            return _ALLOWED_CONSTS[expr.id]
        raise CompileError(f"Unknown compile-time name: {expr.id}")
    if isinstance(expr, ast.List):
        return [_const_eval(e, env) for e in expr.elts]
    if isinstance(expr, ast.Tuple):
        return tuple(_const_eval(e, env) for e in expr.elts)
    if isinstance(expr, ast.UnaryOp):
        v = _const_eval(expr.operand, env)
        if isinstance(expr.op, ast.USub):
            return -_as_float_const(v)
        if isinstance(expr.op, ast.UAdd):
            return _as_float_const(v)
        if isinstance(expr.op, ast.Not):
            return not bool(v)
    if isinstance(expr, ast.BinOp):
        a = _const_eval(expr.left, env); b = _const_eval(expr.right, env)
        if isinstance(expr.op, ast.Add): return a + b
        if isinstance(expr.op, ast.Sub): return a - b
        if isinstance(expr.op, ast.Mult): return a * b
        if isinstance(expr.op, ast.Div): return a / b
        if isinstance(expr.op, ast.Pow): return a ** b
        if isinstance(expr.op, ast.Mod): return a % b
    if isinstance(expr, ast.BoolOp):
        vals = [_const_eval(v, env) for v in expr.values]
        if isinstance(expr.op, ast.And): return all(bool(v) for v in vals)
        if isinstance(expr.op, ast.Or): return any(bool(v) for v in vals)
    if isinstance(expr, ast.Compare):
        if len(expr.ops) != 1 or len(expr.comparators) != 1:
            raise CompileError("Compile-time chained comparisons are not supported")
        a = _const_eval(expr.left, env); b = _const_eval(expr.comparators[0], env); op = expr.ops[0]
        if isinstance(op, ast.Lt): return a < b
        if isinstance(op, ast.LtE): return a <= b
        if isinstance(op, ast.Gt): return a > b
        if isinstance(op, ast.GtE): return a >= b
        if isinstance(op, ast.Eq): return a == b
        if isinstance(op, ast.NotEq): return a != b
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        name = expr.func.id
        args = [_const_eval(a, env) for a in expr.args]
        if name == "vector":
            if len(args) != 3:
                raise CompileError("compile-time vector(x,y,z) expects 3 arguments")
            return ConstVector((_as_float_const(args[0]), _as_float_const(args[1]), _as_float_const(args[2])))
        if name == "range":
            if len(args) == 1: return list(range(int(args[0])))
            if len(args) == 2: return list(range(int(args[0]), int(args[1])))
            if len(args) == 3: return list(range(int(args[0]), int(args[1]), int(args[2])))
            raise CompileError("range() expects 1-3 arguments")
        if name == "count_zero":
            return sum(1 for a in args if a == 0)
        if name in {"len", "sum"}:
            return getattr(__builtins__, name)(args[0]) if len(args) == 1 else None
    raise CompileError(f"Unsupported compile-time expression: {type(expr).__name__}")


def _handle_compile_time_stmt(stmt, env, out_stmts):
    if isinstance(stmt, ast.Assign):
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            raise CompileError("Assignment target must be a simple name")
        # Treat [] and any fully constant assignment as compile-time only.
        try:
            val = _const_eval(stmt.value, env)
            # Do not swallow expressions that are meant to become GN values, except lists/vectors/strings/bools/int literals used by compile-time code.
            if isinstance(stmt.value, (ast.List, ast.Tuple)) or isinstance(val, (list, tuple, ConstVector, str, bool, int)):
                env[target.id] = val
                return
        except CompileError:
            pass
        out_stmts.append(stmt)
        return
    if isinstance(stmt, ast.Expr):
        call = stmt.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "append":
            if not isinstance(call.func.value, ast.Name) or len(call.args) != 1:
                raise CompileError("compile-time append must look like offsets.append(value)")
            list_name = call.func.value.id
            if list_name not in env or not isinstance(env[list_name], list):
                raise CompileError(f"{list_name} is not a compile-time list")
            env[list_name].append(_const_eval(call.args[0], env))
            return
        out_stmts.append(stmt)
        return
    if isinstance(stmt, ast.If):
        branch = stmt.body if bool(_const_eval(stmt.test, env)) else stmt.orelse
        for sub in branch:
            _handle_compile_time_stmt(sub, env, out_stmts)
        return
    if isinstance(stmt, ast.For):
        # Runtime for range(input) is preserved; compile-time for requires a const iterable.
        try:
            iterable = _const_eval(stmt.iter, env)
        except CompileError:
            out_stmts.append(stmt)
            return
        if not isinstance(stmt.target, ast.Name):
            raise CompileError("Only simple compile-time for targets are supported")
        old = env.get(stmt.target.id, None); had_old = stmt.target.id in env
        for item in iterable:
            env[stmt.target.id] = item
            for sub in stmt.body:
                _handle_compile_time_stmt(sub, env, out_stmts)
        if had_old: env[stmt.target.id] = old
        else: env.pop(stmt.target.id, None)
        return
    out_stmts.append(stmt)


def _preprocess_compile_time(stmts):
    env = {}
    out = []
    for stmt in stmts:
        _handle_compile_time_stmt(stmt, env, out)
    return out, env


def _infer_input_types(stmts):
    result = {}
    for stmt in stmts:
        if isinstance(stmt, ast.For) and isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id == "range":
            for arg in stmt.iter.args:
                if isinstance(arg, ast.Name):
                    result[arg.id] = TYPE_INT
    return result


def _normalize_offsets(offsets):
    if not isinstance(offsets, list) or not offsets:
        raise CompileError("offsets must be a non-empty compile-time list")
    out = []
    for v in offsets:
        if _is_const_vector(v):
            out.append(tuple(float(c) for c in v))
        elif isinstance(v, (tuple, list)) and len(v) == 3:
            out.append(tuple(_as_float_const(c, "offset component") for c in v))
        else:
            raise CompileError("offsets must contain vector(x,y,z) values")
    return out


def _set_vector_socket_default(socket, vec):
    try:
        socket.default_value = (float(vec[0]), float(vec[1]), float(vec[2]))
    except Exception:
        socket.default_value[0] = float(vec[0]); socket.default_value[1] = float(vec[1]); socket.default_value[2] = float(vec[2])


def _is_const_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_const_vector_like(v):
    return _is_const_vector(v) or (isinstance(v, (tuple, list)) and len(v) == 3 and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in v))


def _cube_geometry(group, size, x=0, y=0):
    node = _new_node(group, "GeometryNodeMeshCube", x, y)
    for i in [1, 2, 3]:
        try: node.inputs[i].default_value = 2
        except Exception: pass
    if _is_const_number(size):
        _set_vector_socket_default(node.inputs[0], (size, size, size))
    elif _is_const_vector_like(size):
        _set_vector_socket_default(node.inputs[0], size)
    elif isinstance(size, Value) and size.typ == TYPE_VECTOR:
        group.links.new(size.socket, node.inputs[0])
    elif isinstance(size, Value) and _is_number_type(size.typ):
        v = _combine_xyz(group, size, size, size, x - 180, y - 60)
        group.links.new(v.socket, node.inputs[0])
    else:
        raise CompileError("cube(size) expects Float/Int or Vector size")
    return Value(node.outputs[0], TYPE_GEOMETRY)


def _join_geometry(group, geos, x=0, y=0):
    if len(geos) == 1:
        return geos[0]
    node = _new_node(group, "GeometryNodeJoinGeometry", x, y)
    for geo in geos:
        if geo.typ != TYPE_GEOMETRY:
            raise CompileError("join() expects Geometry arguments")
        group.links.new(geo.socket, node.inputs[0])
    return Value(node.outputs[0], TYPE_GEOMETRY)


def _transform_geometry(group, geo, translation=None, scale=None, x=0, y=0):
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("transform() expects Geometry")
    node = _new_node(group, "GeometryNodeTransform", x, y)
    group.links.new(geo.socket, node.inputs[0])
    if translation is not None:
        if _is_const_vector_like(translation):
            _set_vector_socket_default(node.inputs[2], translation)
        elif isinstance(translation, Value) and translation.typ == TYPE_VECTOR:
            group.links.new(translation.socket, node.inputs[2])
        else:
            raise CompileError("translation= must be Vector")
    if scale is not None:
        if _is_const_number(scale):
            _set_vector_socket_default(node.inputs[4], (scale, scale, scale))
        elif _is_const_vector_like(scale):
            _set_vector_socket_default(node.inputs[4], scale)
        elif isinstance(scale, Value) and scale.typ == TYPE_VECTOR:
            group.links.new(scale.socket, node.inputs[4])
        elif isinstance(scale, Value) and _is_number_type(scale.typ):
            v = _combine_xyz(group, scale, scale, scale, x - 180, y - 80)
            group.links.new(v.socket, node.inputs[4])
        else:
            raise CompileError("scale= must be Float/Int or Vector")
    return Value(node.outputs[0], TYPE_GEOMETRY)


def _realize_instances(group, geo, x=0, y=0):
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("realize_instances() expects Geometry")
    node = _new_node(group, "GeometryNodeRealizeInstances", x, y)
    group.links.new(geo.socket, node.inputs[0])
    return Value(node.outputs[0], TYPE_GEOMETRY)


def _const_vector_value(group, vec, x=0, y=0):
    # Kept for dynamic fallbacks/backwards compatibility. Static geometry macros
    # should prefer direct socket defaults via _transform_geometry(..., translation=tuple).
    return _combine_xyz(group, _value(group, vec[0], x-120, y), _value(group, vec[1], x-120, y-40), _value(group, vec[2], x-120, y-80), x, y)


def _scale_to_static_vec(scale):
    if _is_const_number(scale):
        s = float(scale)
        return (s, s, s)
    if _is_const_vector_like(scale):
        return (float(scale[0]), float(scale[1]), float(scale[2]))
    return None


def _scale_to_vector_value(group, scale, x=0, y=0):
    if isinstance(scale, Value):
        if scale.typ == TYPE_VECTOR:
            return scale
        if _is_number_type(scale.typ):
            return _combine_xyz_mixed(group, [scale, scale, scale], x, y)
    raise CompileError("scale= must be Float/Int or Vector")


def _cell_translation_for_offset(group, off, scale, x=0, y=0):
    """Return translation that places a scaled copy into a -1/0/1 cell.

    For static scale, this is folded to socket defaults. For dynamic scale,
    translation = offset * (1 - scale) is built as vector nodes. This makes
    offsets behave like third-grid cell positions: scale=1/3 -> centers at
    -2/3, 0, 2/3 instead of -1/3, 0, 1/3.
    """
    sv = _scale_to_static_vec(scale)
    if sv is not None:
        return (float(off[0]) * (1.0 - sv[0]), float(off[1]) * (1.0 - sv[1]), float(off[2]) * (1.0 - sv[2]))
    scale_vec = _scale_to_vector_value(group, scale, x - 260, y)
    one_vec = _combine_xyz_mixed(group, [1.0, 1.0, 1.0], x - 520, y)
    inv_scale = _vector_math(group, "SUBTRACT", [one_vec, scale_vec], TYPE_VECTOR, x - 320, y)
    off_vec = _combine_xyz_mixed(group, [float(off[0]), float(off[1]), float(off[2])], x - 520, y - 70)
    return _vector_math(group, "MULTIPLY", [off_vec, inv_scale], TYPE_VECTOR, x - 120, y)


def _copy_by_offsets(group, geo, offsets, scale=1.0/3.0, x=0, y=0):
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("copy_by_offsets() first argument must be Geometry")
    offsets = _normalize_offsets(offsets)
    # Correct Sierpinski/Menger semantics: each copy is scaled and translated
    # into its own cell, then all copies are joined. The previous implementation
    # translated all copies first and scaled the joined result, which compressed
    # cell positions and delayed/obscured holes.
    copies = []
    for idx, off in enumerate(offsets):
        yy = y - idx * 110
        translation = _cell_translation_for_offset(group, off, scale, x + 220, yy)
        copies.append(_transform_geometry(group, geo, translation=translation, scale=scale, x=x + 340, y=yy))
    return _join_geometry(group, copies, x + 720, y)


def _repeat_copy_by_offsets(group, geo, iterations, offsets, scale=1.0/3.0, x=0, y=0):
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError("runtime for input must be Geometry")
    ri = _new_node(group, "GeometryNodeRepeatInput", x, y)
    ro = _new_node(group, "GeometryNodeRepeatOutput", x + 1120, y)
    if not ri.pair_with_output(ro):
        raise CompileError("Could not pair Repeat Zone nodes")
    group.links.new(iterations.socket, ri.inputs[0])
    group.links.new(geo.socket, ri.inputs[1])
    current = Value(ri.outputs[1], TYPE_GEOMETRY)
    body = _copy_by_offsets(group, current, offsets, scale, x + 240, y - 160)
    group.links.new(body.socket, ro.inputs[0])
    return Value(ro.outputs[0], TYPE_GEOMETRY)


def _parse_runtime_for(stmt, consts):
    if not isinstance(stmt.target, ast.Name):
        raise CompileError("runtime for target must be a simple name, e.g. for i in range(steps)")
    if not (isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id == "range" and len(stmt.iter.args) == 1):
        raise CompileError("runtime for must look like: for i in range(steps):")
    if len(stmt.body) != 1 or not isinstance(stmt.body[0], ast.Assign):
        raise CompileError("runtime for body currently supports one assignment")
    assign = stmt.body[0]
    if len(assign.targets) != 1 or not isinstance(assign.targets[0], ast.Name):
        raise CompileError("runtime for body assignment must target a simple geometry variable")
    return assign.targets[0].id, stmt.iter.args[0], assign.value


def _repeat_geometry_assignment(group, comp, geom_name, start_geo, iterations, body_expr, x=0, y=0):
    if start_geo.typ != TYPE_GEOMETRY:
        raise CompileError("runtime for input must be Geometry")
    ri = _new_node(group, "GeometryNodeRepeatInput", x, y)
    ro = _new_node(group, "GeometryNodeRepeatOutput", x + 1120, y)
    if not ri.pair_with_output(ro):
        raise CompileError("Could not pair Repeat Zone nodes")
    group.links.new(iterations.socket, ri.inputs[0])
    group.links.new(start_geo.socket, ri.inputs[1])
    old = comp.vars.get(geom_name)
    comp.vars[geom_name] = Value(ri.outputs[1], TYPE_GEOMETRY)
    try:
        body = comp.compile(body_expr)
    finally:
        if old is None:
            comp.vars.pop(geom_name, None)
        else:
            comp.vars[geom_name] = old
    if body.typ != TYPE_GEOMETRY:
        raise CompileError("runtime for body must assign Geometry")
    group.links.new(body.socket, ro.inputs[0])
    return Value(ro.outputs[0], TYPE_GEOMETRY)


def _reset_node_group(group):
    """Clear a GeometryNodeTree in-place so existing Group nodes keep pointing to it."""
    # Remove links first, then nodes.
    try:
        group.links.clear()
    except Exception:
        pass
    for node in list(group.nodes):
        group.nodes.remove(node)
    # Remove interface sockets/panels. items_tree is flat in Blender 4+/5+.
    try:
        for item in reversed(list(group.interface.items_tree)):
            try:
                group.interface.remove(item)
            except Exception:
                pass
    except Exception:
        pass

SOURCE_PROP = "gn_script_mvp_source"
SOURCE_VERSION_PROP = "gn_script_mvp_source_version"
INPUT_DEFAULTS_PROP = "gn_script_mvp_input_defaults"
DESCRIPTION_PREFIX = "Generated by GN Script MVP from: "
SCRATCH_TEXT_NAME = "GNScriptMVP_Current"


def _json_safe_default(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, (tuple, list)):
        return [float(v) for v in value]
    return value


def _set_socket_default(socket, value):
    """Set a Blender socket/interface default robustly across socket classes."""
    if socket is None or value is None:
        return False
    try:
        if isinstance(value, (tuple, list)):
            # Vector sockets usually expose a mutable default_value sequence.
            try:
                socket.default_value = (float(value[0]), float(value[1]), float(value[2]))
            except Exception:
                socket.default_value[0] = float(value[0])
                socket.default_value[1] = float(value[1])
                socket.default_value[2] = float(value[2])
        elif isinstance(value, bool):
            socket.default_value = bool(value)
        elif isinstance(value, int):
            socket.default_value = int(value)
        else:
            socket.default_value = float(value)
        return True
    except Exception:
        return False


def _set_interface_socket_default(group, name, in_out, value):
    if value is None:
        return False
    ok = False
    try:
        for item in group.interface.items_tree:
            if getattr(item, "item_type", None) == 'SOCKET' and getattr(item, "name", None) == name and getattr(item, "in_out", None) == in_out:
                ok = _set_socket_default(item, value) or ok
    except Exception:
        pass
    return ok


def _record_group_input_default(group, name, typ, default):
    if default is None:
        return
    try:
        data = dict(group.get(INPUT_DEFAULTS_PROP, {}))
    except Exception:
        data = {}
    data[name] = {"type": typ, "default": _json_safe_default(default)}
    try:
        group[INPUT_DEFAULTS_PROP] = data
    except Exception:
        pass


def _idprop_to_plain(value):
    """Convert Blender IDProperty groups/arrays into ordinary Python values."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (tuple, list)):
        return [_idprop_to_plain(v) for v in value]
    try:
        # IDPropertyArray and bpy_prop_array are iterable but not dict-like.
        if not hasattr(value, "items"):
            return [_idprop_to_plain(v) for v in value]
    except Exception:
        pass
    try:
        return {str(k): _idprop_to_plain(v) for k, v in value.items()}
    except Exception:
        return value


def _get_group_input_defaults(group):
    if group is None:
        return {}
    try:
        data = group.get(INPUT_DEFAULTS_PROP, {})
    except Exception:
        data = {}
    data = _idprop_to_plain(data)
    return data if isinstance(data, dict) else {}


def _apply_group_defaults_to_node(node, *, preserve_existing=None):
    """Apply stored script defaults to a GeometryNodeGroup instance.

    preserve_existing is an optional dict {socket_name: value}; those values win
    when the socket still exists after update, so user-entered values survive.
    """
    group = getattr(node, "node_tree", None)
    if group is None:
        return
    defaults = _get_group_input_defaults(group)
    preserve_existing = preserve_existing or {}
    for sock in getattr(node, "inputs", []):
        if not hasattr(sock, "default_value"):
            continue
        if sock.name in preserve_existing:
            _set_socket_default(sock, preserve_existing[sock.name])
            continue
        entry = defaults.get(sock.name)
        if isinstance(entry, dict) and "default" in entry:
            _set_socket_default(sock, entry.get("default"))


def _copy_socket_default_value(socket):
    if socket is None or not hasattr(socket, "default_value"):
        return None
    try:
        val = socket.default_value
        if isinstance(val, (float, int, bool)):
            return val
        try:
            return tuple(float(v) for v in val)
        except Exception:
            return val
    except Exception:
        return None


def _defaults_equal(a, b, eps=1e-6):
    if a is None or b is None:
        return False
    if isinstance(a, (tuple, list)) or isinstance(b, (tuple, list)):
        try:
            if len(a) != len(b):
                return False
            return all(abs(float(x) - float(y)) <= eps for x, y in zip(a, b))
        except Exception:
            return False
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    try:
        return abs(float(a) - float(b)) <= eps
    except Exception:
        return a == b


def _is_zero_like_default(value):
    if value is None:
        return True
    if isinstance(value, bool):
        return value is False
    if isinstance(value, (tuple, list)):
        try:
            return all(abs(float(v)) <= 1e-6 for v in value)
        except Exception:
            return False
    try:
        return abs(float(value)) <= 1e-6
    except Exception:
        return False


def _capture_node_external_state(tree, node):
    """Capture links and only real user-overridden input values before recompiling.

    If a visible Group-node value is still equal to the previous script default,
    it is not preserved; after update it should receive the new script default.
    If it differs, preserve it as a user override.
    """
    state = {"input_defaults": {}, "incoming": [], "outgoing": []}
    old_defaults = _get_group_input_defaults(getattr(node, "node_tree", None))
    for sock in getattr(node, "inputs", []):
        val = _copy_socket_default_value(sock)
        if val is None:
            continue
        entry = old_defaults.get(sock.name) if isinstance(old_defaults, dict) else None
        if isinstance(entry, dict) and "default" in entry:
            # Not a user override: let the newly compiled default show in UI.
            if _defaults_equal(val, entry.get("default")):
                continue
            state["input_defaults"][sock.name] = val
        else:
            # Old files may lack stored defaults. Avoid preserving Blender's
            # implicit zero values over newly declared script defaults.
            if not _is_zero_like_default(val):
                state["input_defaults"][sock.name] = val
    for link in list(getattr(tree, "links", [])):
        if link.to_node == node:
            state["incoming"].append({"to_name": link.to_socket.name, "from_socket": link.from_socket})
        elif link.from_node == node:
            state["outgoing"].append({"from_name": link.from_socket.name, "to_socket": link.to_socket})
    return state


def _find_socket_by_name(sockets, name):
    for sock in sockets:
        if sock.name == name:
            return sock
    return None


def _restore_node_external_state(tree, node, state):
    _apply_group_defaults_to_node(node, preserve_existing=state.get("input_defaults", {}))
    restored = 0
    for item in state.get("incoming", []):
        to_socket = _find_socket_by_name(node.inputs, item.get("to_name"))
        from_socket = item.get("from_socket")
        if to_socket is None or from_socket is None:
            continue
        try:
            if not any(l.from_socket == from_socket and l.to_socket == to_socket for l in tree.links):
                tree.links.new(from_socket, to_socket)
                restored += 1
        except Exception:
            pass
    for item in state.get("outgoing", []):
        from_socket = _find_socket_by_name(node.outputs, item.get("from_name"))
        to_socket = item.get("to_socket")
        if from_socket is None or to_socket is None:
            continue
        try:
            if not any(l.from_socket == from_socket and l.to_socket == to_socket for l in tree.links):
                tree.links.new(from_socket, to_socket)
                restored += 1
        except Exception:
            pass
    return restored

def _store_group_source(group, source: str):
    src = source or ""
    group[SOURCE_PROP] = src
    group[SOURCE_VERSION_PROP] = "0.16"
    # Keep tooltip useful but avoid relying on it as the canonical storage.
    one_line = " ".join(src.strip().split())
    if len(one_line) > 900:
        one_line = one_line[:897] + "..."
    group.description = DESCRIPTION_PREFIX + one_line

def _extract_group_source(group):
    if group is None:
        return ""
    try:
        src = group.get(SOURCE_PROP, "")
    except Exception:
        src = ""
    if isinstance(src, str) and src:
        return src
    desc = getattr(group, "description", "") or ""
    if desc.startswith(DESCRIPTION_PREFIX):
        return desc[len(DESCRIPTION_PREFIX):]
    return ""

def _get_or_create_scratch_text():
    text = bpy.data.texts.get(SCRATCH_TEXT_NAME)
    if text is None:
        text = bpy.data.texts.new(SCRATCH_TEXT_NAME)
    return text

def _replace_text_contents(text, source: str):
    text.clear()
    text.write(source or "")

def _make_group(source: str, name: str = "GN Script Expression", existing_group=None):
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
    return _make_group(source, name)

def update_expression_group(group, source: str):
    return _make_group(source, getattr(group, "name", "GN Script Expression"), existing_group=group)


def _source_from_props(props):
    if props and props.text_block:
        return props.text_block.as_string()
    return props.expression if props else ""


def _active_gn_tree(context):
    space = getattr(context, "space_data", None)
    if space and getattr(space, "type", None) == 'NODE_EDITOR':
        tree = getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)
        if tree and getattr(tree, "bl_idname", None) == "GeometryNodeTree":
            return tree
    return None

def _selected_group_node(context):
    tree = _active_gn_tree(context)
    if tree is None:
        return None
    node = getattr(tree.nodes, "active", None)
    if node and getattr(node, "select", False) and getattr(node, "bl_idname", "") == "GeometryNodeGroup" and getattr(node, "node_tree", None):
        return node
    selected = [n for n in tree.nodes if getattr(n, "select", False) and getattr(n, "bl_idname", "") == "GeometryNodeGroup" and getattr(n, "node_tree", None)]
    if len(selected) == 1:
        return selected[0]
    return None

class GNSCRIPT_MVP_Properties(PropertyGroup):
    expression: StringProperty(
        name="Script",
        description="Small Python-like script compiled into Geometry Nodes. For multi-line editing, use the optional Text datablock below.",
        default="p = position(); h = sin(p.x * freq) * cos(p.y * freq) * amp; out = p + vector(0, 0, h)",
    )
    text_block: PointerProperty(
        name="Text Script",
        description="Optional Blender Text datablock. If set, this overrides the inline Script field.",
        type=bpy.types.Text,
    )

class GNSCRIPT_MVP_OT_compile_expression(Operator):
    bl_idname = "gn_script_mvp.compile_expression"
    bl_label = "Compile GN Script Group"
    bl_description = "Compile a small Python-like script into a Geometry Nodes group"
    bl_options = {'REGISTER', 'UNDO'}

    expression: StringProperty(default="")
    insert_node: bpy.props.BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        props = getattr(context.scene, "gn_script_mvp", None)
        source = self.expression or _source_from_props(props)
        try:
            group = create_expression_group(source)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        tree = _active_gn_tree(context)
        if self.insert_node and tree is not None:
            node = tree.nodes.new("GeometryNodeGroup")
            node.node_tree = group
            _apply_group_defaults_to_node(node)
            node.location = (0, 0)
            tree.nodes.active = node
            node.select = True
        self.report({'INFO'}, f"Created node group: {group.name}")
        return {'FINISHED'}


class GNSCRIPT_MVP_OT_update_selected_group(Operator):
    bl_idname = "gn_script_mvp.update_selected_group"
    bl_label = "Update Selected NodeGroup"
    bl_description = "Recompile the current script into the selected Geometry Node Group, preserving the group datablock and name"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _selected_group_node(context) is not None

    def execute(self, context):
        props = getattr(context.scene, "gn_script_mvp", None)
        source = _source_from_props(props)
        node = _selected_group_node(context)
        if node is None:
            self.report({'ERROR'}, "Select exactly one Geometry Node Group node to update")
            return {'CANCELLED'}
        old_name = node.node_tree.name
        tree = _active_gn_tree(context)
        external_state = _capture_node_external_state(tree, node) if tree is not None else {"input_defaults": {}, "incoming": [], "outgoing": []}
        try:
            update_expression_group(node.node_tree, source)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        node.node_tree.name = old_name
        node.name = node.name or old_name
        restored = _restore_node_external_state(tree, node, external_state) if tree is not None else 0
        self.report({'INFO'}, f"Updated node group: {old_name}; restored {restored} link(s)")
        return {'FINISHED'}

class GNSCRIPT_MVP_OT_load_selected_group_source(Operator):
    bl_idname = "gn_script_mvp.load_selected_group_source"
    bl_label = "Load Script From Selected NodeGroup"
    bl_description = "Load the embedded GN Script source from the selected Geometry Node Group into the editor field/Text datablock"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        node = _selected_group_node(context)
        return node is not None and bool(_extract_group_source(node.node_tree))

    def execute(self, context):
        props = getattr(context.scene, "gn_script_mvp", None)
        node = _selected_group_node(context)
        if node is None:
            self.report({'ERROR'}, "Select exactly one Geometry Node Group node")
            return {'CANCELLED'}
        source = _extract_group_source(node.node_tree)
        if not source:
            self.report({'ERROR'}, "Selected node group has no embedded GN Script source")
            return {'CANCELLED'}
        if props is None:
            self.report({'ERROR'}, "GN Script MVP properties are not available")
            return {'CANCELLED'}
        text = props.text_block or _get_or_create_scratch_text()
        _replace_text_contents(text, source)
        props.text_block = text
        props.expression = source
        self.report({'INFO'}, f"Loaded script from: {node.node_tree.name}")
        return {'FINISHED'}

class GNSCRIPT_MVP_PT_panel(Panel):
    bl_label = "GN Script MVP"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "GN Script"

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        return bool(space and getattr(space, "type", None) == 'NODE_EDITOR')

    def draw(self, context):
        layout = self.layout
        props = context.scene.gn_script_mvp
        layout.label(text="Python-like script → Node Group")
        layout.prop(props, "text_block")
        layout.prop(props, "expression", text="")
        op = layout.operator(GNSCRIPT_MVP_OT_compile_expression.bl_idname, text="Compile Script")
        op.expression = "" if props.text_block else props.expression
        op.insert_node = True
        row = layout.row()
        selected_group = _selected_group_node(context)
        row.enabled = selected_group is not None
        row.operator(GNSCRIPT_MVP_OT_update_selected_group.bl_idname, text="Update Selected NodeGroup")
        row = layout.row()
        row.enabled = selected_group is not None and bool(_extract_group_source(selected_group.node_tree))
        row.operator(GNSCRIPT_MVP_OT_load_selected_group_source.bl_idname, text="Load Script From Selected NodeGroup")
        if selected_group is None:
            layout.label(text="Select a Group node to update/load", icon='INFO')
        elif not _extract_group_source(selected_group.node_tree):
            layout.label(text="Selected Group has no embedded GN Script", icon='INFO')
        layout.separator()
        layout.label(text="Examples:")
        layout.label(text="out = clamp(sin(x), 0, 1)")
        layout.label(text="p = position(); out = p + vector(0,0,1)")
        layout.label(text="set_position(position() + vector(0,0,amp))")

def menu_func(self, context):
    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None) if space else None
    if tree and getattr(tree, "bl_idname", None) == "GeometryNodeTree":
        self.layout.separator()
        op = self.layout.operator(GNSCRIPT_MVP_OT_compile_expression.bl_idname, text="GN Script: Compile Group")
        props = getattr(context.scene, "gn_script_mvp", None)
        op.expression = props.expression if props else "out = sin(x * 10) + cos(y * 10)"
        op.insert_node = True

classes = (GNSCRIPT_MVP_Properties, GNSCRIPT_MVP_OT_compile_expression, GNSCRIPT_MVP_OT_update_selected_group, GNSCRIPT_MVP_OT_load_selected_group_source, GNSCRIPT_MVP_PT_panel)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gn_script_mvp = PointerProperty(type=GNSCRIPT_MVP_Properties)
    bpy.types.NODE_MT_add.append(menu_func)

def unregister():
    try:
        bpy.types.NODE_MT_add.remove(menu_func)
    except Exception:
        pass
    if hasattr(bpy.types.Scene, "gn_script_mvp"):
        del bpy.types.Scene.gn_script_mvp
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
