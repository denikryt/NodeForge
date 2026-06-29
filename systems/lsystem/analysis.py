"""Structural analysis for expanded L-system command streams."""

from ...errors import CompileError
from ...values import Value
from .model import LSystemAnalysis, LSystemModule


def _legacy_modules(stream: str) -> tuple[LSystemModule, ...]:
    return tuple(LSystemModule(ch, (), ch) for ch in stream)


def _arg_runtime(module: LSystemModule) -> bool:
    return bool(module.args and module.args[0].is_runtime)


def analyze(stream, *, angle, step, markers=None) -> LSystemAnalysis:
    """Validate branch brackets and compute backend-selection metrics."""
    modules = _legacy_modules(stream) if isinstance(stream, str) else tuple(stream)
    marker_names = set(markers or ())
    depth = 0
    max_depth = 0
    segment_count = 0
    has_branches = False
    uses_runtime_angle = isinstance(angle, Value)
    uses_runtime_step = isinstance(step, Value)
    marker_count = 0
    marker_param_runtime = False
    for module in modules:
        name = module.name
        if name == "F":
            segment_count += 1
        if name in {"F", "f"}:
            uses_runtime_step = uses_runtime_step or (_arg_runtime(module) if module.args else isinstance(step, Value))
        elif name in {"+", "-"}:
            uses_runtime_angle = uses_runtime_angle or (_arg_runtime(module) if module.args else isinstance(angle, Value))
        elif name == "[":
            has_branches = True
            depth += 1
            max_depth = max(max_depth, depth)
        elif name == "]":
            has_branches = True
            depth -= 1
            if depth < 0:
                raise CompileError("L-system contains unmatched ]")
        elif name in marker_names:
            marker_count += 1
            marker_param_runtime = marker_param_runtime or any(arg.is_runtime for arg in module.args)
    if depth != 0:
        raise CompileError("L-system contains unclosed [")
    return LSystemAnalysis(
        has_branches=has_branches,
        angle_is_runtime=uses_runtime_angle,
        step_is_runtime=uses_runtime_step,
        segment_count=segment_count,
        symbol_count=len(modules),
        max_branch_depth=max_depth,
        marker_count=marker_count,
        marker_param_is_runtime=marker_param_runtime,
    )


__all__ = ["analyze"]
