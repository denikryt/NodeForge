"""Compile-time data model for embedded L-systems."""

from dataclasses import dataclass
from typing import Mapping

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
class LSystemParam(LSystemPart):
    """Named numeric value available to parameterized modules in L-system strings."""
    name: str
    value: object


@dataclass(frozen=True)
class LSystemMarker(LSystemPart):
    """Declared marker module that can emit point-domain placement data."""
    name: str
    parameter_names: tuple[str, ...]
    marker_identity: tuple[int, int, int, int]


@dataclass(frozen=True)
class ModuleArg:
    """Resolved module argument, either a numeric literal or a named runtime/static parameter."""
    source: str
    value: object
    is_runtime: bool
    param_name: str | None = None


@dataclass(frozen=True)
class LSystemModule:
    """One parsed L-system token with optional resolved argument values."""
    name: str
    args: tuple[ModuleArg, ...] = ()
    raw: str = ""


@dataclass(frozen=True)
class LSystemSpec(CompileTimeObject):
    """Validated L-system specification consumed internally by ls_system(...)."""
    axiom: tuple[LSystemModule, ...]
    rules: dict[str, tuple[LSystemModule, ...]]
    iterations: int
    angle: object
    step: object
    params: dict[str, object]
    markers: dict[str, LSystemMarker]


@dataclass(frozen=True)
class LSystemAnalysis:
    """Structural metrics for an expanded L-system command stream."""
    has_branches: bool
    angle_is_runtime: bool
    step_is_runtime: bool
    segment_count: int
    symbol_count: int
    max_branch_depth: int
    marker_count: int = 0
    marker_param_is_runtime: bool = False


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


@dataclass(frozen=True)
class TurtleMarkerPoint:
    """One emitted L-system marker point and its point-domain attributes."""
    position: TurtlePoint
    heading: object
    tangent: TurtlePoint
    branch_depth: int
    path_id: int
    creation_iteration: int | None
    marker_name: str
    marker_identity: tuple[int, int, int, int]
    parameters: Mapping[str, object]


@dataclass(frozen=True)
class TurtleInterpretation:
    """Static turtle interpretation output: drawn curve segments plus marker points."""
    segments: tuple[TurtleSegment, ...]
    markers: tuple[TurtleMarkerPoint, ...]


__all__ = [
    "LSystemPart", "LSystemAxiom", "LSystemRule", "LSystemIterations",
    "LSystemAngle", "LSystemStep", "LSystemParam", "LSystemMarker",
    "ModuleArg", "LSystemModule", "LSystemSpec", "LSystemAnalysis",
    "TurtlePoint", "TurtleSegment", "TurtleMarkerPoint", "TurtleInterpretation",
]
