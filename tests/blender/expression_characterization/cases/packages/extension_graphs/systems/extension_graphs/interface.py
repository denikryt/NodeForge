"""Small, deterministic Extension API v2 fixtures for graph characterization."""

from dataclasses import dataclass
from typing import Annotated
from NodeForge import DynamicResults, EvaluationMode, Float, Geometry, String

EXTENSION_API = 2


@dataclass(frozen=True)
class Part:
    value: Float

EXTENSIONS = {
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
    """Return a Float or Vector in a one-element tuple."""
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
