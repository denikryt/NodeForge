"""Compiler handlers for public L-system constructor calls."""

from ...compile_time import reject_compile_time_object
from ...constants import TYPE_GEOMETRY, TYPE_INT, TYPE_BOOL
from ...errors import CompileError
from ...nodes import _is_number_type, _compare
from ...values import Value
from .compiler import compile_lsystem
from .model import LSystemAngle, LSystemAxiom, LSystemIterations, LSystemMarker, LSystemParam, LSystemRule, LSystemStep
from .modules import BUILTIN_COMMANDS, marker_identity, validate_identifier
from .validation import validate_rule_symbol, validate_stream


def _require_positional(expr, count: int):
    """Validate constructor arity and keyword-free syntax."""
    name = expr.func.id
    if expr.keywords:
        raise CompileError(f"{name}() does not support keyword arguments")
    if len(expr.args) != count:
        raise CompileError(f"{name}() expects {count} argument(s)")


def _const(comp, expr, context):
    """Evaluate a constructor compile-time constant argument."""
    try:
        return comp._const_eval_macro_arg(expr)
    except CompileError as exc:
        raise CompileError(f"{context} must be a compile-time constant") from exc


def _runtime_numeric_or_const(comp, expr, context):
    """Return a static numeric constant or a numeric runtime Value."""
    try:
        value = comp._const_eval_macro_arg(expr)
    except CompileError:
        value = comp.compile(expr)
        reject_compile_time_object(value, context)
        if not isinstance(value, Value) or not _is_number_type(value.typ):
            raise CompileError(f"{context} must be numeric")
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise CompileError(f"{context} must be numeric")


def compile_axiom(comp, expr, depth=0):
    """Compile ls_axiom("...") into an LSystemAxiom part."""
    _require_positional(expr, 1)
    return LSystemAxiom(validate_stream(_const(comp, expr.args[0], "ls_axiom()"), "ls_axiom()"))


def compile_rule(comp, expr, depth=0):
    """Compile ls_rule("symbol", "replacement") into an LSystemRule part."""
    _require_positional(expr, 2)
    symbol = validate_rule_symbol(_const(comp, expr.args[0], "ls_rule() predecessor"))
    replacement = validate_stream(_const(comp, expr.args[1], "ls_rule() replacement"), "ls_rule() replacement")
    return LSystemRule(symbol, replacement)


def compile_iterations(comp, expr, depth=0):
    """Compile ls_iterations(n) into an LSystemIterations part."""
    _require_positional(expr, 1)
    value = _const(comp, expr.args[0], "ls_iterations()")
    if not isinstance(value, int) or isinstance(value, bool):
        raise CompileError("ls_iterations() must be a compile-time integer")
    if value < 0:
        raise CompileError("ls_iterations() must be non-negative")
    return LSystemIterations(value)


def compile_angle(comp, expr, depth=0):
    """Compile ls_angle(value) into an LSystemAngle part measured in degrees."""
    _require_positional(expr, 1)
    return LSystemAngle(_runtime_numeric_or_const(comp, expr.args[0], "ls_angle()"))


def compile_step(comp, expr, depth=0):
    """Compile ls_step(value) into an LSystemStep part."""
    _require_positional(expr, 1)
    return LSystemStep(_runtime_numeric_or_const(comp, expr.args[0], "ls_step()"))


def compile_param(comp, expr, depth=0):
    """Compile ls_param(name, value) into an LSystemParam part."""
    _require_positional(expr, 2)
    name = validate_identifier(_const(comp, expr.args[0], "ls_param() name"), "ls_param() name")
    value = _runtime_numeric_or_const(comp, expr.args[1], "ls_param()")
    return LSystemParam(name, value)


def compile_marker(comp, expr, depth=0):
    """Compile ls_marker(name, *parameter_names) into an LSystemMarker part."""
    if expr.keywords:
        raise CompileError("ls_marker() does not support keyword arguments")
    if len(expr.args) < 1:
        raise CompileError("ls_marker() expects at least 1 argument")
    name = validate_identifier(_const(comp, expr.args[0], "ls_marker() name"), "ls_marker() name")
    if name in BUILTIN_COMMANDS:
        raise CompileError(f"ls_marker() name {name!r} collides with a built-in L-system command")
    params = []
    seen = set()
    for arg in expr.args[1:]:
        param_name = validate_identifier(_const(comp, arg, "ls_marker() parameter name"), "ls_marker() parameter name")
        if param_name in seen:
            raise CompileError(f"ls_marker() received duplicate parameter name {param_name!r}")
        if param_name.startswith("nf_lsys_"):
            raise CompileError(f"ls_marker() parameter name {param_name!r} uses reserved nf_lsys_ prefix")
        seen.add(param_name)
        params.append(param_name)
    return LSystemMarker(name, tuple(params), marker_identity(name))


def _keyword_marker(comp, expr):
    if len(expr.args) != 1:
        raise CompileError("ls_points() expects exactly one Geometry argument")
    if len(expr.keywords) != 1 or expr.keywords[0].arg != "marker":
        raise CompileError("ls_points() requires marker=\"Name\"")
    marker = validate_identifier(_const(comp, expr.keywords[0].value, "ls_points() marker"), "ls_points() marker")
    return marker


def compile_points(comp, expr, depth=0):
    """Compile ls_points(geometry, marker="Name") marker extraction."""
    marker = _keyword_marker(comp, expr)
    geo = comp.compile(expr.args[0])
    if not isinstance(geo, Value) or geo.typ != TYPE_GEOMETRY:
        raise CompileError("ls_points() first argument must be Geometry")
    from .backends import filter_marker_points
    return filter_marker_points(comp, geo, marker, x=depth * 240 + 260, y=-depth * 120)


HANDLERS = {
    "ls_system": compile_lsystem,
    "ls_axiom": compile_axiom,
    "ls_rule": compile_rule,
    "ls_iterations": compile_iterations,
    "ls_angle": compile_angle,
    "ls_step": compile_step,
    "ls_param": compile_param,
    "ls_marker": compile_marker,
    "ls_points": compile_points,
}
NAMES = frozenset(HANDLERS)

__all__ = ["HANDLERS", "NAMES"]
