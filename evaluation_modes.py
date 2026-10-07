"""Public consumer evaluation-mode vocabulary.

Implementation of representation selection belongs to the semantic frontend;
this root module intentionally contains no CTFE dependency.
"""

from enum import Enum, auto


class EvaluationMode(Enum):
    """Representations accepted by one frontend consumer use site."""

    COMPILE_TIME_ONLY = auto()
    RUNTIME_ONLY = auto()
    COMPILE_TIME_OR_RUNTIME = auto()


__all__ = ["EvaluationMode"]
