"""Pure semantic analysis for compiler-owned NodeForge builtin calls.

The module classifies compiler-owned callable builtins and input declarations.
It normalizes source call syntax and compile-time options without importing
Blender-facing builtin implementations.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Callable

from .call_resolution import (
    AnalyzedCallOperand,
    ContextReadCallResult,
    NamedOutputsCallResult,
    ProjectedCallResult,
    RuntimeCallResult,
    TupleCallResult,
)
from .constants import (
    TYPE_BOOL,
    TYPE_BUNDLE,
    TYPE_FLOAT,
    TYPE_GEOMETRY,
    TYPE_INT,
    TYPE_MATERIAL,
    TYPE_OBJECT,
    TYPE_STRING,
    TYPE_TOKEN_NAMES,
    TYPE_VECTOR,
    _VECTOR_MATH_FLOAT_OUTPUT,
    _VECTOR_MATH_VECTOR_OUTPUT_1,
    _VECTOR_MATH_VECTOR_OUTPUT_2,
)
from .consteval import ConstVector, _as_float_const, _const_eval, _is_const_vector
from .errors import CompileError
from .nf_types import NFType, NUMERIC_NF_TYPES
from .group_context import GroupContextSlot


INPUT_DECLARATION_PLACEMENT_ERROR = (
    "input_*() may only be used as the complete right-hand side of a simple assignment"
)

INPUT_DECLARATION_BUILTIN_NAMES = frozenset({
    "input_geometry",
    "input_float",
    "input_int",
    "input_bool",
    "input_vector",
    "input_material",
    "input_object",
    "input_string",
    "input_bundle",
})

IR_CAPABLE_BUILTIN_NAMES = frozenset(
    {"vector"}
    | set(_VECTOR_MATH_FLOAT_OUTPUT)
    | set(_VECTOR_MATH_VECTOR_OUTPUT_1)
    | set(_VECTOR_MATH_VECTOR_OUTPUT_2)
    | {"position", "normal", "index", "id"}
    | {
        "grid",
        "grid_uv",
        "empty_geometry",
        "points",
        "point",
        "line",
        "set_position",
        "store_named_attribute",
        "capture_attribute",
        "set_material",
        "cube",
        "join",
        "transform",
        "polyline",
        "instance_on_points",
        "realize_instances",
        "bundle",
        "bundle_get",
        "bundle_set",
        "node",
        "geometry_builder",
    }
)


@dataclass(frozen=True)
class BuiltinCallSemantics:
    """AST-free normalized builtin payload plus concrete semantic result contract."""

    operands: tuple[AnalyzedCallOperand, ...]
    options: tuple[tuple[str, object], ...]
    result: RuntimeCallResult | TupleCallResult | NamedOutputsCallResult | ProjectedCallResult | ContextReadCallResult




@dataclass(frozen=True)
class InputDeclarationSemantics:
    """Normalized display-only input declaration metadata for body Semantic IR."""

    display_name: str
    typ: NFType
    default: object | None


def analyze_input_declaration_call(expr, consts):
    """Normalize one direct ``input_*`` call without touching compiler or Blender state."""
    if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)):
        raise TypeError("input declaration analysis requires a simple call")
    name = expr.func.id
    if name not in INPUT_DECLARATION_BUILTIN_NAMES:
        raise ValueError("call is not an input declaration builtin")
    kws = _kw_dict(expr)
    allowed_keywords = {
        "input_geometry": set(),
        "input_material": set(),
        "input_object": set(),
        "input_float": {"default"},
        "input_int": {"default"},
        "input_bool": {"default"},
        "input_vector": {"default"},
        "input_string": {"default"},
        "input_bundle": set(),
    }
    _check_extra(kws, allowed_keywords[name])
    if len(expr.args) != 1:
        raise CompileError(f'{name}(name, ...) expects exactly one name argument')
    display_name = _literal_string(expr.args[0], consts, f"{name}() name")
    type_map = {
        "input_geometry": TYPE_GEOMETRY,
        "input_material": TYPE_MATERIAL,
        "input_object": TYPE_OBJECT,
        "input_bundle": TYPE_BUNDLE,
        "input_float": TYPE_FLOAT,
        "input_int": TYPE_INT,
        "input_bool": TYPE_BOOL,
        "input_vector": TYPE_VECTOR,
        "input_string": TYPE_STRING,
    }
    typ = type_map[name]
    default_expr = kws.get("default")
    if name in {"input_geometry", "input_material", "input_object", "input_bundle"}:
        default = None
    elif name == "input_float":
        default = 0.0 if default_expr is None else _as_float_const(_const_eval(default_expr, consts), "input_float default")
    elif name == "input_int":
        default = 0 if default_expr is None else int(_as_float_const(_const_eval(default_expr, consts), "input_int default"))
    elif name == "input_bool":
        default = False if default_expr is None else bool(_const_eval(default_expr, consts))
    elif name == "input_vector":
        if default_expr is None:
            default = (0.0, 0.0, 0.0)
        else:
            raw = _const_eval(default_expr, consts)
            if _is_const_vector(raw):
                default = tuple(float(item) for item in raw)
            elif isinstance(raw, (tuple, list)) and len(raw) == 3:
                default = tuple(_as_float_const(item, "input_vector default component") for item in raw)
            else:
                raise CompileError("input_vector default= must be vector(x,y,z) or a 3-number tuple/list")
    elif name == "input_string":
        default = "" if default_expr is None else _const_eval(default_expr, consts)
        if not isinstance(default, str):
            raise CompileError("input_string default= must be a compile-time string")
    else:
        raise CompileError(f"Unsupported io builtin: {name}")
    return InputDeclarationSemantics(display_name, typ, _freeze(default))

RuntimeAnalyzer = Callable[[ast.expr, str | None, str], NFType]


def _freeze(value):
    """Convert supported compile-time containers to immutable detached values."""
    if isinstance(value, ConstVector):
        return tuple(float(item) for item in value)
    if _is_const_vector(value):
        return tuple(float(item) for item in value)
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return tuple((str(key), _freeze(item)) for key, item in value.items())
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    raise CompileError(f"Unsupported compile-time call value: {type(value).__name__}")


def _const(expr, consts, context):
    """Evaluate and detach one compile-time call expression."""
    try:
        return _freeze(_const_eval(expr, consts))
    except CompileError:
        raise
    except Exception as exc:
        raise CompileError(context) from exc


def _literal_string(expr, consts, context):
    """Return a non-empty detached compile-time string with legacy diagnostics."""
    try:
        value = _const_eval(expr, consts)
    except CompileError as exc:
        raise CompileError(f"Expected a non-empty compile-time string for {context}") from exc
    if isinstance(value, str) and value:
        return value
    raise CompileError(f"Expected a non-empty compile-time string for {context}")


def _node_literal_string(expr, consts, context):
    """Return a raw-node compile-time string using raw-node diagnostics."""
    try:
        value = _const_eval(expr, consts)
    except CompileError as exc:
        raise CompileError(f"node(...) {context} must be a non-empty compile-time string") from exc
    if isinstance(value, str) and value:
        return value
    raise CompileError(f"node(...) {context} must be a non-empty compile-time string")


def _kw_dict(expr):
    """Build the legacy keyword map and preserve duplicate/**kwargs diagnostics."""
    out = {}
    for kw in expr.keywords:
        if kw.arg is None:
            raise CompileError("**kwargs are not supported")
        if kw.arg in out:
            raise CompileError(f"Duplicate keyword argument: {kw.arg}")
        out[kw.arg] = kw.value
    return out


def _check_extra(kws, allowed):
    """Apply the shared legacy unsupported-keyword diagnostic."""
    extra = set(kws) - set(allowed)
    if extra:
        raise CompileError("Unsupported keyword argument(s): " + ", ".join(sorted(extra)))


def _require_type(typ, expected, message):
    """Require one canonical runtime type or raise the supplied public diagnostic."""
    if typ not in expected:
        raise CompileError(message)


def _const_or_runtime(expr, consts, add_runtime, name, context):
    """Return a normalized const/runtime slot while preserving const-first topology."""
    try:
        value = _freeze(_const_eval(expr, consts))
        return ("const", value), None
    except CompileError:
        typ = add_runtime(expr, name, context)
        return ("runtime", None), typ


def _ordered_vector_args(expr):
    """Return vector component expressions after legacy keyword binding."""
    names = ["x", "y", "z"]
    if len(expr.args) > 3:
        raise CompileError("vector(x, y, z) expects 3 arguments")
    out = list(expr.args)
    used = set(names[:len(expr.args)])
    by_name = {}
    for kw in expr.keywords:
        if kw.arg is None:
            raise CompileError("vector() does not support **kwargs")
        if kw.arg not in names:
            raise CompileError(f"vector() got unknown keyword argument {kw.arg!r}")
        if kw.arg in used or kw.arg in by_name:
            raise CompileError(f"vector() got multiple values for argument {kw.arg!r}")
        by_name[kw.arg] = kw.value
    for param in names[len(expr.args):]:
        if param not in by_name:
            raise CompileError(f"vector() missing argument: {param}")
        out.append(by_name[param])
    return out


def _ordered_vector_helper_args(name, expr):
    """Return positionalized vector-helper arguments with current keyword rules."""
    specs = {
        "rotate2d": ["v", "angle"],
        "polar": ["radius", "angle"],
        "angle_between": ["a", "b"],
        "rotate_around_axis": ["v", "axis", "angle"],
    }
    if not expr.keywords:
        return list(expr.args), [None] * len(expr.args)
    if name not in specs:
        raise CompileError(f"{name}() does not support keyword arguments")
    names = specs[name]
    if len(expr.args) > len(names):
        raise CompileError(f"{name}() got too many positional arguments")
    out = list(expr.args)
    used = set(names[:len(expr.args)])
    by_name = {}
    for kw in expr.keywords:
        if kw.arg is None:
            raise CompileError(f"{name}() does not support **kwargs")
        if kw.arg not in names:
            raise CompileError(f"{name}() got unknown keyword argument {kw.arg!r}")
        if kw.arg in used or kw.arg in by_name:
            raise CompileError(f"{name}() got multiple values for argument {kw.arg!r}")
        by_name[kw.arg] = kw.value
    for param in names[len(expr.args):]:
        if param not in by_name:
            raise CompileError(f"{name}() missing argument: {param}")
        out.append(by_name[param])
    return out, names


def _normalize_polyline_points(value):
    """Validate and freeze the existing compile-time polyline point contract."""
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise CompileError("polyline(points) expects a compile-time list with at least 2 vector points")
    out = []
    for point in value:
        if _is_const_vector(point):
            out.append(tuple(float(component) for component in point))
        elif isinstance(point, (tuple, list)) and len(point) == 3:
            out.append(tuple(_as_float_const(component, "point component") for component in point))
        else:
            raise CompileError("polyline(points) expects vector(x,y,z) points")
    return tuple(out)


def _raw_json_value(value, context="value"):
    """Normalize one raw-node metadata value to immutable JSON-compatible data."""
    if _is_const_vector(value):
        return tuple(float(v) for v in value)
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_raw_json_value(v, context) for v in value)
    raise CompileError(f"{context}: value is not supported by raw-node metadata")


def _raw_input_literal(value, context, socket_name):
    """Normalize one compile-time raw-node input default using current rules."""
    if _is_const_vector(value):
        return tuple(float(v) for v in value)
    if isinstance(value, tuple):
        if len(value) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            return tuple(float(v) for v in value)
        raise CompileError(f"{context}: input {socket_name!r} tuple defaults must be numeric 3-tuples")
    if isinstance(value, list):
        raise CompileError(f"{context}: input {socket_name!r} list syntax is reserved for multi-input fanout")
    if isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and not value:
            raise CompileError(f"{context}: input {socket_name!r} string defaults must be non-empty")
        return value
    raise CompileError(f"{context}: unsupported literal default for input {socket_name!r}")


def _analyze_raw_node(expr, consts, add_runtime):
    """Normalize ``node(...)`` source syntax without querying Blender sockets."""
    if len(expr.args) != 1:
        raise CompileError("node(...) expects exactly one positional bl_idname argument")
    if any(kw.arg is None for kw in expr.keywords):
        raise CompileError("node(...) does not support **kwargs")
    kws = {}
    for kw in expr.keywords:
        if kw.arg in kws:
            raise CompileError(f"Duplicate keyword argument: {kw.arg}")
        kws[kw.arg] = kw.value
    allowed = {"props", "inputs", "output", "typ", "outputs"}
    extra = set(kws) - allowed
    if extra:
        raise CompileError("node(...) got unsupported keyword argument(s): " + ", ".join(sorted(extra)))

    output = _node_literal_string(kws["output"], consts, "output=") if "output" in kws else None
    typ = None
    if "typ" in kws:
        token = kws["typ"]
        if not isinstance(token, ast.Name) or token.id not in TYPE_TOKEN_NAMES:
            raise CompileError(f"node(...) typ= must be one of: {', '.join(sorted(TYPE_TOKEN_NAMES))}")
        typ = TYPE_TOKEN_NAMES[token.id]

    outputs = None
    if "outputs" in kws:
        raw = kws["outputs"]
        if not isinstance(raw, ast.Dict):
            raise CompileError("node(...) outputs= must be written as a literal dict")
        if not raw.keys:
            raise CompileError("node(...) outputs= cannot be empty")
        items = []
        seen = set()
        for key_expr, value_expr in zip(raw.keys, raw.values):
            key = _node_literal_string(key_expr, consts, "outputs key")
            if key in seen:
                raise CompileError(f"node(...) outputs= has duplicate socket {key!r}")
            seen.add(key)
            if not isinstance(value_expr, ast.Name) or value_expr.id not in TYPE_TOKEN_NAMES:
                raise CompileError(
                    f"node(...) outputs[{key!r}] must be one of: {', '.join(sorted(TYPE_TOKEN_NAMES))}"
                )
            items.append((key, TYPE_TOKEN_NAMES[value_expr.id]))
        outputs = tuple(items)

    has_single = output is not None or typ is not None
    has_named = outputs is not None
    if has_single and has_named:
        raise CompileError("node(...) accepts either output=/typ= or outputs=, not both")
    if has_single and (output is None or typ is None):
        raise CompileError("node(...) single-output mode requires both output= and typ=")
    if not has_single and not has_named:
        raise CompileError("node(...) requires output=/typ= or outputs=")

    bl_idname = _node_literal_string(expr.args[0], consts, "bl_idname")

    props = ()
    if "props" in kws:
        raw = kws["props"]
        if not isinstance(raw, ast.Dict):
            raise CompileError("node(...) props= must be written as a literal dict")
        prop_items = []
        seen = set()
        for key_expr, value_expr in zip(raw.keys, raw.values):
            key = _node_literal_string(key_expr, consts, "props= key")
            if key in seen:
                raise CompileError(f"node(...) props= has duplicate key {key!r}")
            seen.add(key)
            try:
                value = _const_eval(value_expr, consts)
            except CompileError as exc:
                raise CompileError("node(...) props= values must be compile-time literals") from exc
            prop_items.append((key, _raw_json_value(value, f"node(...) props={key!r}")))
        props = tuple(prop_items)

    operands = []
    input_specs = []
    if "inputs" in kws:
        raw = kws["inputs"]
        if not isinstance(raw, ast.Dict):
            raise CompileError("node(...) inputs= must be written as a literal dict")
        seen = set()
        for key_expr, value_expr in zip(raw.keys, raw.values):
            key = _node_literal_string(key_expr, consts, "inputs key")
            if key in seen:
                raise CompileError(f"node(...) inputs= has duplicate socket {key!r}")
            seen.add(key)
            if isinstance(value_expr, ast.List):
                if not value_expr.elts:
                    raise CompileError(f"node(...) input {key!r} list cannot be empty")
                refs = []
                for item in value_expr.elts:
                    typ_i = add_runtime(item, key, f"node() multi-input {key!r}")
                    refs.append(len(operands))
                    operands.append(AnalyzedCallOperand(key, typ_i))
                input_specs.append((key, ("multi", tuple(refs))))
                continue
            try:
                literal = _const_eval(value_expr, consts)
            except CompileError:
                typ_i = add_runtime(value_expr, key, f"node() input {key!r}")
                ref = len(operands)
                operands.append(AnalyzedCallOperand(key, typ_i))
                input_specs.append((key, ("runtime", ref)))
            else:
                input_specs.append((key, ("literal", _raw_input_literal(literal, "node()", key))))

    options = (
        ("bl_idname", bl_idname),
        ("props", props),
        ("inputs", tuple(input_specs)),
        ("raw_output_mode", "SINGLE_OUTPUT" if has_single else "NAMED_OUTPUTS"),
        ("output", output),
        ("typ", typ),
        ("outputs", outputs),
    )
    result = RuntimeCallResult(typ) if has_single else NamedOutputsCallResult(outputs)
    return BuiltinCallSemantics(tuple(operands), options, result)


def analyze_builtin_call(name: str, expr: ast.Call, consts, add_runtime: RuntimeAnalyzer) -> BuiltinCallSemantics:
    """Normalize one IR-capable builtin call using detached compile-time state."""
    if name not in IR_CAPABLE_BUILTIN_NAMES:
        raise KeyError(name)
    operands: list[AnalyzedCallOperand] = []

    def runtime(node, parameter_name, context):
        typ = add_runtime(node, parameter_name, context)
        operands.append(AnalyzedCallOperand(parameter_name, typ))
        return typ

    if name == "geometry_builder":
        if expr.args or expr.keywords:
            raise CompileError("geometry_builder() accepts no arguments")
        raise CompileError("geometry_builder() must be assigned to a simple name")

    if name in {"position", "normal", "index", "id"}:
        if expr.keywords:
            raise CompileError(f"{name}() does not support keyword arguments")
        if expr.args:
            raise CompileError(f"{name}() expects no arguments")
        typ = TYPE_VECTOR if name in {"position", "normal"} else TYPE_INT
        return BuiltinCallSemantics((), (), RuntimeCallResult(typ))

    if name == "vector":
        components = []
        runtime_component_types = []
        for index, child in enumerate(_ordered_vector_args(expr)):
            try:
                value = _as_float_const(_const_eval(child, consts), "vector component")
            except CompileError:
                typ = runtime(child, ("x", "y", "z")[index], "vector() component")
                runtime_component_types.append(typ)
                components.append(("runtime", len(operands) - 1))
            else:
                components.append(("const", float(value)))
        for typ in runtime_component_types:
            _require_type(typ, NUMERIC_NF_TYPES, "vector(x, y, z) expects numeric arguments")
        return BuiltinCallSemantics(tuple(operands), (("components", tuple(components)),), RuntimeCallResult(TYPE_VECTOR))

    if name in _VECTOR_MATH_FLOAT_OUTPUT or name in _VECTOR_MATH_VECTOR_OUTPUT_1 or name in _VECTOR_MATH_VECTOR_OUTPUT_2:
        args, parameter_names = _ordered_vector_helper_args(name, expr)
        if name in _VECTOR_MATH_FLOAT_OUTPUT:
            expected = 2 if name in {"distance", "dot"} else 1
            result_type = TYPE_FLOAT
        elif name in _VECTOR_MATH_VECTOR_OUTPUT_1:
            expected = 1
            result_type = TYPE_VECTOR
        else:
            expected = 2
            result_type = TYPE_VECTOR
        argument_types = []
        for index, child in enumerate(args):
            param = parameter_names[index] if index < len(parameter_names) else None
            argument_types.append(runtime(child, param, f"{name}() arguments"))
        if len(args) != expected:
            suffix = "argument" if expected == 1 and name not in _VECTOR_MATH_FLOAT_OUTPUT else "argument(s)"
            raise CompileError(f"{name}() expects {expected} {suffix}")
        for typ in argument_types:
            if typ not in NUMERIC_NF_TYPES and typ != TYPE_VECTOR:
                raise CompileError(f"Vector Math {_vector_operation(name)} got unsupported type {typ}")
        return BuiltinCallSemantics(
            tuple(operands),
            (("vector_operation", _vector_operation(name)),),
            RuntimeCallResult(result_type),
        )

    if name == "grid":
        kws = _kw_dict(expr)
        if kws:
            raise CompileError("grid() does not support keyword arguments")
        if len(expr.args) != 2:
            raise CompileError("grid(width, height) expects two Int arguments")
        slots = []
        for child, label in zip(expr.args, ("width", "height")):
            diagnostic = f"grid() {label} expects Float/Int"
            try:
                value = _const_eval(child, consts)
            except CompileError:
                typ = runtime(child, label, f"grid() {label}")
                _require_type(typ, NUMERIC_NF_TYPES, diagnostic)
                slots.append(("runtime", len(operands) - 1))
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise CompileError(diagnostic)
                slots.append(("const", value))
        return BuiltinCallSemantics(
            tuple(operands),
            (("slots", tuple(slots)),),
            ProjectedCallResult(
                (TYPE_GEOMETRY, TYPE_VECTOR),
                0,
                ((GroupContextSlot.GRID_UV, 1),),
            ),
        )

    if name == "grid_uv":
        kws = _kw_dict(expr)
        if kws:
            raise CompileError("grid_uv() does not support keyword arguments")
        if expr.args:
            raise CompileError("grid_uv() expects no arguments")
        return BuiltinCallSemantics((), (), ContextReadCallResult(GroupContextSlot.GRID_UV, TYPE_VECTOR))

    if name == "empty_geometry":
        kws = _kw_dict(expr)
        if kws:
            raise CompileError("empty_geometry() does not support keyword arguments")
        if expr.args:
            raise CompileError("empty_geometry() expects no arguments")
        return BuiltinCallSemantics((), (), RuntimeCallResult(TYPE_GEOMETRY))

    if name in {"point", "line"}:
        kws = _kw_dict(expr)
        if kws:
            raise CompileError(f"{name}() does not support keyword arguments")
        expected = 1 if name == "point" else 2
        if len(expr.args) != expected:
            raise CompileError("point(position) expects one Vector argument" if name == "point" else "line(start, end) expects two Vector arguments")
        slots = []
        runtime_slot_types = []
        labels = ("position",) if name == "point" else ("start", "end")
        for child, label in zip(expr.args, labels):
            try:
                value = _freeze(_const_eval(child, consts))
            except CompileError:
                typ = runtime(child, label, f"{name}() {label}")
                runtime_slot_types.append((label, typ))
                slots.append(("runtime", len(operands) - 1))
            else:
                slots.append(("const", value))
        for label, typ in runtime_slot_types:
            _require_type(typ, {TYPE_VECTOR}, f"{name}() {label} expects Vector")
        return BuiltinCallSemantics(tuple(operands), (("slots", tuple(slots)),), RuntimeCallResult(TYPE_GEOMETRY))

    if name == "points":
        kws = _kw_dict(expr)
        if kws:
            raise CompileError("points() does not support keyword arguments")
        if len(expr.args) != 1:
            raise CompileError("points(count) expects one Int argument")
        try:
            value = _freeze(_const_eval(expr.args[0], consts))
        except CompileError:
            typ = runtime(expr.args[0], "count", "points() count")
            _require_type(typ, NUMERIC_NF_TYPES, "points(count) expects an Int count")
            slot = ("runtime", 0)
        else:
            slot = ("const", value)
        return BuiltinCallSemantics(tuple(operands), (("count", slot),), RuntimeCallResult(TYPE_GEOMETRY))

    if name == "set_position":
        kws = _kw_dict(expr)
        _check_extra(kws, {"selection"})
        if len(expr.args) != 2:
            raise CompileError("set_position(geo, position, selection=...) expects Geometry and Vector")
        return _analyze_set_position_operation(
            expr.args[0],
            expr.args[1],
            list(expr.keywords),
            add_runtime,
            geometry_error="set_position(geo, position) first argument must be Geometry",
            position_error="set_position(geo, position) second argument must be Vector",
            selection_error="set_position(..., selection=...) expects Bool selection",
        )

    if name == "store_named_attribute":
        kws = _kw_dict(expr)
        _check_extra(kws, {"selection", "domain", "type"})
        if len(expr.args) != 3:
            raise CompileError('store_named_attribute(geometry, name, value, selection=..., domain="POINT", type=...) expects 3 positional arguments')
        return _analyze_store_operation(
            expr.args[0],
            expr.args[1],
            expr.args[2],
            list(expr.keywords),
            consts,
            add_runtime,
            call_name="store_named_attribute",
            name_context="store_named_attribute() name",
            value_context="store_named_attribute() value",
            geometry_type_error="store_named_attribute() first argument must be Geometry",
        )

    if name == "capture_attribute":
        kws = _kw_dict(expr)
        _check_extra(kws, {"selection", "domain", "type"})
        if len(expr.args) != 2:
            raise CompileError('capture_attribute(geometry, value, selection=..., domain="POINT", type=...) expects 2 positional arguments')
        geo_typ = runtime(expr.args[0], "geometry", "capture_attribute() geometry")
        options = []
        value_typ = runtime(expr.args[1], "value", "capture_attribute() value")
        if "selection" in kws:
            selection_typ = runtime(kws["selection"], "selection", "selection= expression")
            if selection_typ != TYPE_BOOL:
                raise CompileError("selection= must be a Bool expression")
        domain = "POINT"
        if "domain" in kws:
            domain = _literal_string(kws["domain"], consts, "domain=")
        data_type = None
        if "type" in kws:
            data_type = _literal_string(kws["type"], consts, "type=")
        options.extend((("domain", domain), ("data_type", data_type)))
        if geo_typ != TYPE_GEOMETRY:
            raise CompileError("capture_attribute() expects Geometry")
        result_typ = _capture_result_type(value_typ, data_type)
        return BuiltinCallSemantics(tuple(operands), tuple(options), TupleCallResult((TYPE_GEOMETRY, result_typ)))

    if name == "set_material":
        kws = _kw_dict(expr)
        if kws:
            raise CompileError("set_material() does not support keyword arguments")
        if len(expr.args) != 2:
            raise CompileError('set_material(geometry, material) expects Geometry and Material or a compile-time material name')
        geo_typ = runtime(expr.args[0], "geometry", "set_material() geometry")
        try:
            material = _literal_string(expr.args[1], consts, "set_material() material name")
        except CompileError:
            material_typ = runtime(expr.args[1], "material", "set_material() material")
            if material_typ != TYPE_MATERIAL:
                raise CompileError("set_material() second argument must be Material or a compile-time material name")
            mode = ("runtime", len(operands) - 1)
        else:
            mode = ("const", material)
        if geo_typ != TYPE_GEOMETRY:
            raise CompileError("set_material() first argument must be Geometry")
        return BuiltinCallSemantics(tuple(operands), (("material", mode),), RuntimeCallResult(TYPE_GEOMETRY))

    if name == "cube":
        kws = _kw_dict(expr)
        _check_extra(kws, {"size"})
        if len(expr.args) > 1:
            raise CompileError("cube(size) expects 0 or 1 positional argument")
        if expr.args and "size" in kws:
            # The current _kw_dict + selection logic effectively prefers positional size;
            # preserve that behavior rather than introducing generic duplicate binding.
            size_expr = expr.args[0]
        elif expr.args:
            size_expr = expr.args[0]
        elif "size" in kws:
            size_expr = kws["size"]
        else:
            return BuiltinCallSemantics((), (("size", ("const", 1.0)),), RuntimeCallResult(TYPE_GEOMETRY))
        try:
            value = _freeze(_const_eval(size_expr, consts))
            slot = ("const", value)
        except CompileError:
            typ = runtime(size_expr, "size", "cube() size")
            if typ not in NUMERIC_NF_TYPES and typ != TYPE_VECTOR:
                raise CompileError("cube(size) expects Float/Int or Vector size")
            slot = ("runtime", len(operands) - 1)
        return BuiltinCallSemantics(tuple(operands), (("size", slot),), RuntimeCallResult(TYPE_GEOMETRY))

    if name == "join":
        kws = _kw_dict(expr)
        if kws:
            raise CompileError("join() does not support keyword arguments")
        if len(expr.args) < 1:
            raise CompileError("join([geo_a, geo_b, ...]) or join(geo_a, geo_b, ...) expects Geometry")
        source_args = list(expr.args[0].elts) if len(expr.args) == 1 and isinstance(expr.args[0], (ast.List, ast.Tuple)) else list(expr.args)
        geometry_types = [runtime(child, "geometry", "join() argument") for child in source_args]
        for typ in geometry_types:
            if typ != TYPE_GEOMETRY:
                raise CompileError("join() expects Geometry arguments")
        return BuiltinCallSemantics(tuple(operands), (), RuntimeCallResult(TYPE_GEOMETRY))

    if name == "transform":
        kws = _kw_dict(expr)
        _check_extra(kws, {"translation", "scale", "rotation"})
        if not expr.args or len(expr.args) > 3:
            raise CompileError("transform(geo, translation=..., scale=..., rotation=...) expects Geometry")
        geo_typ = runtime(expr.args[0], "geometry", "transform() geometry")
        if geo_typ != TYPE_GEOMETRY:
            raise CompileError("transform() first argument must be Geometry")
        entries = []
        runtime_option_types = []
        source_options = {
            "translation": kws.get("translation", expr.args[1] if len(expr.args) > 1 else None),
            "scale": kws.get("scale", expr.args[2] if len(expr.args) > 2 else None),
            "rotation": kws.get("rotation", None),
        }
        for key, child in source_options.items():
            if child is None:
                entries.append((key, None))
                continue
            try:
                value = _freeze(_const_eval(child, consts))
                entries.append((key, ("const", value)))
            except CompileError:
                typ = runtime(child, key, f"transform() {key}")
                runtime_option_types.append((key, typ))
                entries.append((key, ("runtime", len(operands) - 1)))
        runtime_option_type_map = dict(runtime_option_types)
        translation_typ = runtime_option_type_map.get("translation")
        if translation_typ is not None and translation_typ != TYPE_VECTOR:
            raise CompileError("translation= must be Vector")
        rotation_typ = runtime_option_type_map.get("rotation")
        if rotation_typ is not None and rotation_typ != TYPE_VECTOR:
            raise CompileError("rotation= must be Vector in radians")
        scale_typ = runtime_option_type_map.get("scale")
        if scale_typ is not None and scale_typ not in NUMERIC_NF_TYPES and scale_typ != TYPE_VECTOR:
            raise CompileError("scale= must be Float/Int or Vector")
        return BuiltinCallSemantics(tuple(operands), tuple(entries), RuntimeCallResult(TYPE_GEOMETRY))

    if name == "polyline":
        kws = _kw_dict(expr)
        if kws:
            raise CompileError("polyline() does not support keyword arguments")
        if len(expr.args) != 1:
            raise CompileError("polyline(points) expects one compile-time list of vector points")
        try:
            raw = _const_eval(expr.args[0], consts)
        except CompileError as exc:
            raise CompileError("polyline(points) expects one compile-time list of vector points") from exc
        points = _normalize_polyline_points(raw)
        return BuiltinCallSemantics((), (("points", points),), RuntimeCallResult(TYPE_GEOMETRY))

    if name == "instance_on_points":
        kws = _kw_dict(expr)
        _check_extra(kws, {"selection", "scale", "rotation", "realize"})
        if len(expr.args) != 2:
            raise CompileError("instance_on_points(instance, points, ...) expects two Geometry arguments")
        first = runtime(expr.args[0], "instance", "instance_on_points() instance")
        second = runtime(expr.args[1], "points", "instance_on_points() points")
        if first != TYPE_GEOMETRY or second != TYPE_GEOMETRY:
            raise CompileError("instance_on_points(instance, points, ...) expects two Geometry arguments")
        options = []
        if "selection" in kws:
            typ = runtime(kws["selection"], "selection", "selection= expression")
            if typ != TYPE_BOOL:
                raise CompileError("selection= must be a Bool expression")
        runtime_option_types = []
        for key in ("scale", "rotation"):
            if key not in kws:
                options.append((key, None))
                continue
            child = kws[key]
            try:
                value = _freeze(_const_eval(child, consts))
                options.append((key, ("const", value)))
            except CompileError:
                typ = runtime(child, key, "instancing builtin argument")
                runtime_option_types.append((key, typ))
                options.append((key, ("runtime", len(operands) - 1)))
        realize = True
        if "realize" in kws:
            try:
                realize = bool(_const_eval(kws["realize"], consts))
            except CompileError as exc:
                raise CompileError("instance_on_points realize= must be a compile-time bool") from exc
        for key, typ in runtime_option_types:
            if key == "rotation" and typ != TYPE_VECTOR:
                raise CompileError("rotation= must be Vector in radians")
            if key == "scale" and typ not in NUMERIC_NF_TYPES and typ != TYPE_VECTOR:
                raise CompileError("scale= must be Float/Int or Vector")
        options.append(("realize", realize))
        return BuiltinCallSemantics(tuple(operands), tuple(options), RuntimeCallResult(TYPE_GEOMETRY))

    if name == "realize_instances":
        kws = _kw_dict(expr)
        if kws:
            raise CompileError("realize_instances() does not support keyword arguments")
        if len(expr.args) != 1:
            raise CompileError("realize_instances(geo) expects one Geometry")
        typ = runtime(expr.args[0], "geometry", "realize_instances() geometry")
        if typ != TYPE_GEOMETRY:
            raise CompileError("realize_instances() expects Geometry")
        return BuiltinCallSemantics(tuple(operands), (), RuntimeCallResult(TYPE_GEOMETRY))

    if name == "bundle":
        if expr.args:
            raise CompileError("bundle(...) accepts named keyword items only")
        if any(keyword.arg is None for keyword in expr.keywords):
            raise CompileError("bundle(...) does not support **kwargs")
        seen = set()
        item_names = []
        for keyword in expr.keywords:
            item_name = keyword.arg
            if item_name in seen:
                raise CompileError(f"bundle() has duplicate item name {item_name!r}")
            seen.add(item_name)
            runtime(keyword.value, item_name, f"bundle() item {item_name!r}")
            item_names.append(item_name)
        return BuiltinCallSemantics(tuple(operands), (("item_names", tuple(item_names)),), RuntimeCallResult(TYPE_BUNDLE))

    if name == "bundle_get":
        if len(expr.args) != 2:
            raise CompileError("bundle_get(bundle, path, typ=...) expects exactly two positional arguments")
        if any(keyword.arg is None for keyword in expr.keywords):
            raise CompileError("bundle_get(...) does not support **kwargs")
        keywords = {keyword.arg: keyword.value for keyword in expr.keywords}
        if set(keywords) != {"typ"}:
            raise CompileError("bundle_get(...) requires exactly one keyword argument: typ=")
        bundle_typ = runtime(expr.args[0], "bundle", "bundle_get() bundle")
        if bundle_typ != TYPE_BUNDLE:
            raise CompileError(f"bundle_get() first argument expects Bundle, got {bundle_typ}")
        path_typ = runtime(expr.args[1], "path", "bundle_get() path")
        if path_typ != TYPE_STRING:
            raise CompileError(f"bundle_get() path expects String, got {path_typ}")
        token = keywords["typ"]
        if not isinstance(token, ast.Name) or token.id not in TYPE_TOKEN_NAMES:
            raise CompileError(f"bundle_get() typ= must be one of: {', '.join(sorted(TYPE_TOKEN_NAMES))}")
        result_typ = TYPE_TOKEN_NAMES[token.id]
        return BuiltinCallSemantics(tuple(operands), (("item_type", result_typ),), RuntimeCallResult(result_typ))

    if name == "bundle_set":
        if len(expr.args) != 3:
            raise CompileError("bundle_set(bundle, path, value) expects exactly three positional arguments")
        if expr.keywords:
            raise CompileError("bundle_set(...) does not accept keyword arguments")
        bundle_typ = runtime(expr.args[0], "bundle", "bundle_set() bundle")
        if bundle_typ != TYPE_BUNDLE:
            raise CompileError(f"bundle_set() first argument expects Bundle, got {bundle_typ}")
        path_typ = runtime(expr.args[1], "path", "bundle_set() path")
        if path_typ != TYPE_STRING:
            raise CompileError(f"bundle_set() path expects String, got {path_typ}")
        runtime(expr.args[2], "value", "bundle_set() value")
        return BuiltinCallSemantics(tuple(operands), (), RuntimeCallResult(TYPE_BUNDLE))

    if name == "node":
        return _analyze_raw_node(expr, consts, add_runtime)

    raise CompileError(f"Internal error: missing semantic builtin analyzer for {name}")


def _vector_operation(name):
    """Return the existing Blender Vector Math operation for one helper name."""
    if name in _VECTOR_MATH_FLOAT_OUTPUT:
        return _VECTOR_MATH_FLOAT_OUTPUT[name]
    if name in _VECTOR_MATH_VECTOR_OUTPUT_1:
        return _VECTOR_MATH_VECTOR_OUTPUT_1[name]
    return _VECTOR_MATH_VECTOR_OUTPUT_2[name]


def _capture_result_type(value_typ: NFType, data_type: str | None) -> NFType:
    """Return Capture Attribute's exact result type or preserve its diagnostic."""
    value_map = {
        TYPE_FLOAT: TYPE_FLOAT,
        TYPE_INT: TYPE_INT,
        TYPE_BOOL: TYPE_BOOL,
        TYPE_VECTOR: TYPE_VECTOR,
    }
    if data_type is None:
        result = value_map.get(value_typ)
    else:
        result = {
            "FLOAT": TYPE_FLOAT,
            "INT": TYPE_INT,
            "BOOLEAN": TYPE_BOOL,
            "VECTOR": TYPE_VECTOR,
        }.get(data_type.upper())
    if result is None:
        raise CompileError("capture_attribute() supports Float, Int, Bool and Vector values")
    return result


def _analyze_store_operation(
    geometry_expr: ast.expr,
    name_expr: ast.expr,
    value_expr: ast.expr,
    keywords: list[ast.keyword],
    consts,
    add_runtime: RuntimeAnalyzer,
    *,
    call_name: str,
    name_context: str,
    value_context: str,
    geometry_type_error: str,
) -> BuiltinCallSemantics:
    """Normalize one store operation while preserving caller-specific diagnostics."""
    operands: list[AnalyzedCallOperand] = []

    def runtime(child, parameter_name, context):
        typ = add_runtime(child, parameter_name, context)
        operands.append(AnalyzedCallOperand(parameter_name, typ))
        return typ

    kws = _kw_dict(ast.Call(func=ast.Name(id=call_name, ctx=ast.Load()), args=[], keywords=keywords))
    _check_extra(kws, {"selection", "domain", "type"})
    geo_typ = runtime(geometry_expr, "geometry", f"{call_name}() geometry")
    options = []
    try:
        attr_name = _literal_string(name_expr, consts, name_context)
    except CompileError:
        attr_typ = runtime(name_expr, "name", name_context)
        if attr_typ != TYPE_STRING:
            raise CompileError(f"{name_context} must be a compile-time string or runtime String, got {attr_typ}")
        options.append(("name_mode", ("runtime", len(operands) - 1)))
    else:
        options.append(("name_mode", ("const", attr_name)))
    runtime(value_expr, "value", value_context)
    if "selection" in kws:
        selection_typ = runtime(kws["selection"], "selection", "selection= expression")
        if selection_typ != TYPE_BOOL:
            raise CompileError("selection= must be a Bool expression")
    domain = "POINT"
    if "domain" in kws:
        domain = _literal_string(kws["domain"], consts, "domain=")
    data_type = None
    if "type" in kws:
        data_type = _literal_string(kws["type"], consts, "type=")
    options.extend((("domain", domain), ("data_type", data_type)))
    if geo_typ != TYPE_GEOMETRY:
        raise CompileError(geometry_type_error)
    return BuiltinCallSemantics(tuple(operands), tuple(options), RuntimeCallResult(TYPE_GEOMETRY))


def _analyze_set_position_operation(
    geometry_expr: ast.expr,
    position_expr: ast.expr,
    keywords: list[ast.keyword],
    add_runtime: RuntimeAnalyzer,
    *,
    geometry_error: str,
    position_error: str,
    selection_error: str,
) -> BuiltinCallSemantics:
    """Normalize one set-position operation while preserving caller-specific diagnostics."""
    operands: list[AnalyzedCallOperand] = []

    def runtime(child, parameter_name, context):
        typ = add_runtime(child, parameter_name, context)
        operands.append(AnalyzedCallOperand(parameter_name, typ))
        return typ

    kws = _kw_dict(ast.Call(func=ast.Name(id="set_position", ctx=ast.Load()), args=[], keywords=keywords))
    _check_extra(kws, {"selection"})
    geo = runtime(geometry_expr, "geometry", "set_position() geometry")
    pos = runtime(position_expr, "position", "set_position() position")
    selection = runtime(kws["selection"], "selection", "selection= expression") if "selection" in kws else None
    _require_type(geo, {TYPE_GEOMETRY}, geometry_error)
    _require_type(pos, {TYPE_VECTOR}, position_error)
    if selection is not None:
        _require_type(selection, {TYPE_BOOL}, selection_error)
    return BuiltinCallSemantics(tuple(operands), (), RuntimeCallResult(TYPE_GEOMETRY))


def analyze_contextual_store_call(expr: ast.Call, consts, add_runtime: RuntimeAnalyzer) -> BuiltinCallSemantics:
    """Normalize statement-form ``store`` while preserving its public diagnostics."""
    if len(expr.args) != 2:
        raise CompileError('store(attribute_name, value, selection=..., domain="POINT", type="FLOAT") expects 2 positional arguments')
    return _analyze_store_operation(
        ast.Name(id="__nodeforge_current_geometry__", ctx=ast.Load()),
        expr.args[0],
        expr.args[1],
        list(expr.keywords),
        consts,
        add_runtime,
        call_name="store",
        name_context="store() attribute name",
        value_context="store() value",
        geometry_type_error="Internal error: store() requires Geometry context",
    )


def analyze_contextual_set_position_call(expr: ast.Call, consts, add_runtime: RuntimeAnalyzer) -> BuiltinCallSemantics:
    """Normalize statement-form ``set_position`` while preserving its public diagnostics."""
    if len(expr.args) != 1:
        raise CompileError("set_position(position_vector, selection=...) expects exactly one positional argument")
    return _analyze_set_position_operation(
        ast.Name(id="__nodeforge_current_geometry__", ctx=ast.Load()),
        expr.args[0],
        list(expr.keywords),
        add_runtime,
        geometry_error="Internal error: set_position() requires Geometry context",
        position_error="set_position() expects a Vector argument",
        selection_error="selection= must be a Bool expression",
    )


__all__ = [
    "BuiltinCallSemantics",
    "IR_CAPABLE_BUILTIN_NAMES",
    "INPUT_DECLARATION_BUILTIN_NAMES",
    "INPUT_DECLARATION_PLACEMENT_ERROR",
    "analyze_builtin_call",
    "analyze_contextual_store_call",
    "analyze_contextual_set_position_call",
]
