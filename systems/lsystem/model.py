"""Compile-time data model for embedded L-systems."""

from dataclasses import dataclass

from ...compile_time import CompileTimeObject


@dataclass(frozen=True)
class LSystemPart(CompileTimeObject):
    """Base class for constructor parts accepted by ls_system(...)."""


@dataclass(frozen=True)
class LSystemAxiom(LSystemPart):
    """Compile-time axiom string."""
    value: str


@dataclass(frozen=True)
class LSystemRule(LSystemPart):
    """Compile-time one-symbol rewrite rule."""
    symbol: str
    replacement: str


@dataclass(frozen=True)
class LSystemIterations(LSystemPart):
    """Compile-time rewrite iteration count."""
    value: int


@dataclass(frozen=True)
class LSystemAngle(LSystemPart):
    """Turn angle in degrees, static or backed by a runtime Value."""
    value: object


@dataclass(frozen=True)
class LSystemStep(LSystemPart):
    """Forward distance, static or backed by a runtime Value."""
    value: object


@dataclass(frozen=True)
class LSystemSpec(CompileTimeObject):
    """Validated L-system specification consumed internally by ls_system(...)."""
    axiom: str
    rules: dict[str, str]
    iterations: int
    angle: object
    step: object


@dataclass(frozen=True)
class LSystemAnalysis:
    """Structural metrics for an expanded L-system command stream."""
    has_branches: bool
    angle_is_runtime: bool
    step_is_runtime: bool
    segment_count: int
    symbol_count: int
    max_branch_depth: int


@dataclass(frozen=True)
class TurtlePoint:
    """A point that may contain static floats or runtime Value coordinates."""
    x: object
    y: object
    z: object


@dataclass(frozen=True)
class TurtleSegment:
    """One drawn turtle segment."""
    start: TurtlePoint
    end: TurtlePoint


__all__ = [
    "LSystemPart", "LSystemAxiom", "LSystemRule", "LSystemIterations",
    "LSystemAngle", "LSystemStep", "LSystemSpec", "LSystemAnalysis",
    "TurtlePoint", "TurtleSegment",
]
