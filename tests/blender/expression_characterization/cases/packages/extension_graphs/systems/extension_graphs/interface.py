"""Small, deterministic Extension API v2 fixtures for graph characterization."""

from dataclasses import dataclass
from typing import Annotated
from NodeForge import DynamicResults, EvaluationMode, Float, Geometry, String

EXTENSION_API = 2


@dataclass(frozen=True)
class Part:
    value: Float

EXTENSIONS = {
    "named_one": ".backend:named_one",
    "named_two": ".backend:named_two",
    "positional_one": ".backend:positional_one",
    "shape": ".backend:shape",
    "shape_state": ".backend:shape_state",
    "scale": ".backend:scale",
    "mixed": ".backend:mixed",
    "choose": ".backend:choose",
    "capture": ".backend:capture",
    "make_part": None,
    "consume_part": ".backend:consume_part",
}


def scale(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float:
    """Multiply a runtime Float by two."""
    ...


def mixed(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> DynamicResults:
    """Return a Float and Vector from one physical operation."""
    ...


def choose(
    value: Annotated[Float, EvaluationMode.RUNTIME_ONLY],
    form: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = "FLOAT",
) -> DynamicResults:
    """Return a Float or Vector in a one-key named result."""
    ...


def capture(geometry: Annotated[Geometry, EvaluationMode.RUNTIME_ONLY]) -> Geometry:
    """Capture Rotation and Float fields on geometry."""
    ...


def make_part(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Part:
    """Wrap one runtime value in a semantic record."""
    ...


def consume_part(part: Part) -> Float:
    """Lower one semantic record into a Float operation."""
    ...


@dataclass(frozen=True)
class ShapeState:
    value: Float
    form: str


def named_one(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> {"value": Float}:
    """Return an explicit named singleton."""
    ...


def named_two(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> {"scaled": Float, "original": Float}:
    """Declare two same-type leaves in semantic order."""
    ...


def positional_one(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> tuple[Float]:
    """Preserve explicit positional singleton semantics."""
    ...


def shape(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY], form: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = "DIRECT") -> DynamicResults:
    """Select an explicit direct or named schema."""
    ...


def shape_state(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY], form: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = "DIRECT") -> DynamicResults:
    """Select a result shape and forward runtime semantic state."""
    ...
