"""Compiler handlers for public L-system constructor calls."""

from ...compile_time import reject_compile_time_object
from ...errors import CompileError
from ...nodes import _is_number_type
from ...values import Value
from .compiler import compile_lsystem
from .model import LSystemAngle, LSystemAxiom, LSystemIterations, LSystemRule, LSystemStep
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


HANDLERS = {
    "ls_system": compile_lsystem,
    "ls_axiom": compile_axiom,
    "ls_rule": compile_rule,
    "ls_iterations": compile_iterations,
    "ls_angle": compile_angle,
    "ls_step": compile_step,
}
NAMES = frozenset(HANDLERS)

__all__ = ["HANDLERS", "NAMES"]
