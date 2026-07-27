"""Input/output related built-ins for NodeForge DSL."""

from ..constants import TYPE_GEOMETRY, TYPE_FLOAT, TYPE_INT, TYPE_BOOL, TYPE_VECTOR, TYPE_MATERIAL
from ..errors import CompileError
from ..statements import _kw_dict, _check_no_extra_keywords
from ..parsing import _literal_string
from ..consteval import _const_eval, _as_float_const, _is_const_vector

NAMES = {"input_geometry", "input_float", "input_int", "input_bool", "input_vector", "input_material"}


def compile_call(comp, expr, depth=0):
    """Compile input socket built-ins and apply compile-time defaults."""
    name = expr.func.id
    kws = _kw_dict(expr)
    _check_no_extra_keywords(kws, {"default"})
    if len(expr.args) != 1:
        raise CompileError(f'{name}(name, ...) expects exactly one name argument')
    input_name = _literal_string(expr.args[0], f"{name}() name", comp.consts)

    if name in {"input_geometry", "input_material"}:
        if kws:
            raise CompileError(f"{name}(name) does not support default=")
        typ = TYPE_GEOMETRY if name == "input_geometry" else TYPE_MATERIAL
        return comp._create_input_socket_value(input_name, typ, None)

    default_expr = kws.get("default", None)
    if name == "input_float":
        default = 0.0 if default_expr is None else _as_float_const(_const_eval(default_expr, comp.consts), "input_float default")
        return comp._create_input_socket_value(input_name, TYPE_FLOAT, default)
    if name == "input_int":
        default = 0 if default_expr is None else int(_as_float_const(_const_eval(default_expr, comp.consts), "input_int default"))
        return comp._create_input_socket_value(input_name, TYPE_INT, default)
    if name == "input_bool":
        default = False if default_expr is None else bool(_const_eval(default_expr, comp.consts))
        return comp._create_input_socket_value(input_name, TYPE_BOOL, default)
    if name == "input_vector":
        if default_expr is None:
            default = (0.0, 0.0, 0.0)
        else:
            default = _const_eval(default_expr, comp.consts)
            if _is_const_vector(default):
                default = tuple(default)
            elif isinstance(default, (tuple, list)) and len(default) == 3:
                default = tuple(_as_float_const(v, "input_vector default component") for v in default)
            else:
                raise CompileError("input_vector default= must be vector(x,y,z) or a 3-number tuple/list")
        return comp._create_input_socket_value(input_name, TYPE_VECTOR, default)

    raise CompileError(f"Unsupported io builtin: {name}")
