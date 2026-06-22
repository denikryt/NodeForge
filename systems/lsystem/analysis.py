"""Structural analysis for expanded L-system command streams."""

from ...errors import CompileError
from ...values import Value
from .model import LSystemAnalysis


def analyze(stream: str, *, angle, step) -> LSystemAnalysis:
    """Validate branch brackets and compute backend-selection metrics."""
    depth = 0
    max_depth = 0
    segment_count = 0
    has_branches = False
    for ch in stream:
        if ch == "F":
            segment_count += 1
        elif ch == "[":
            has_branches = True
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch == "]":
            has_branches = True
            depth -= 1
            if depth < 0:
                raise CompileError("L-system contains unmatched ]")
    if depth != 0:
        raise CompileError("L-system contains unclosed [")
    return LSystemAnalysis(
        has_branches=has_branches,
        angle_is_runtime=isinstance(angle, Value),
        step_is_runtime=isinstance(step, Value),
        segment_count=segment_count,
        symbol_count=len(stream),
        max_branch_depth=max_depth,
    )


__all__ = ["analyze"]
