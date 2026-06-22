"""Orchestration for ls_system(...) compilation."""

from ...compile_time import reject_compile_time_object
from ...errors import CompileError
from .analysis import analyze
from .backends import (
    branch_aware_vectorized_runtime_backend,
    branch_free_vectorized_runtime_backend,
    select_backend_category,
    static_baked_backend,
    validate_branch_aware_backend_available,
)
from .expander import expand
from .model import LSystemAngle, LSystemAxiom, LSystemIterations, LSystemPart, LSystemRule, LSystemSpec, LSystemStep
from .runtime_tables import build_branch_aware_command_table, build_branch_free_command_table
from .turtle import interpret_static


def build_spec(parts: list[LSystemPart]) -> LSystemSpec:
    """Parse constructor parts into a complete, validated L-system spec."""
    axiom = None
    iterations = None
    angle = None
    step = None
    rules = {}
    for part in parts:
        if isinstance(part, LSystemAxiom):
            if axiom is not None:
                raise CompileError("ls_system() received duplicate ls_axiom()")
            axiom = part.value
        elif isinstance(part, LSystemIterations):
            if iterations is not None:
                raise CompileError("ls_system() received duplicate ls_iterations()")
            iterations = part.value
        elif isinstance(part, LSystemAngle):
            if angle is not None:
                raise CompileError("ls_system() received duplicate ls_angle()")
            angle = part.value
        elif isinstance(part, LSystemStep):
            if step is not None:
                raise CompileError("ls_system() received duplicate ls_step()")
            step = part.value
        elif isinstance(part, LSystemRule):
            if part.symbol in rules:
                raise CompileError(f"ls_system() received duplicate rule for {part.symbol!r}")
            rules[part.symbol] = part.replacement
        else:
            raise CompileError("ls_system() accepts only L-system constructor parts")
    if axiom is None:
        raise CompileError("ls_system() requires ls_axiom(...)")
    if iterations is None:
        raise CompileError("ls_system() requires ls_iterations(...)")
    if angle is None:
        raise CompileError("ls_system() requires ls_angle(...)")
    if step is None:
        raise CompileError("ls_system() requires ls_step(...)")
    return LSystemSpec(axiom=axiom, rules=rules, iterations=iterations, angle=angle, step=step)


def compile_lsystem(comp, expr, depth=0):
    """Compile ls_system(...) into a normal Geometry Value."""
    if expr.keywords:
        raise CompileError("ls_system() does not support keyword arguments")
    parts = []
    for arg in expr.args:
        value = comp.compile(arg)
        if not isinstance(value, LSystemPart):
            reject_compile_time_object(value, "ls_system()")
            raise CompileError("ls_system() arguments must be L-system constructor parts")
        parts.append(value)
    spec = build_spec(parts)
    stream = expand(spec.axiom, spec.rules, spec.iterations)
    metrics = analyze(stream, angle=spec.angle, step=spec.step)
    category = select_backend_category(metrics)
    if category == "static":
        segments = interpret_static(stream, angle_degrees=spec.angle, step=spec.step)
        return static_baked_backend(comp, segments, x=depth * 240 + 260, y=-depth * 120)
    if category == "branch_free_runtime":
        table = build_branch_free_command_table(stream)
        return branch_free_vectorized_runtime_backend(
            comp,
            table,
            angle_degrees=spec.angle,
            step=spec.step,
            x=depth * 240 + 260,
            y=-depth * 120,
        )
    if category == "branched_runtime":
        validate_branch_aware_backend_available(metrics, category)
        table = build_branch_aware_command_table(stream)
        return branch_aware_vectorized_runtime_backend(
            comp,
            table,
            angle_degrees=spec.angle,
            step=spec.step,
            x=depth * 240 + 260,
            y=-depth * 120,
        )
    raise CompileError(f"Unsupported L-system backend category: {category}")


__all__ = ["build_spec", "compile_lsystem"]
