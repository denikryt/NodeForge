"""Resolve consumer representation requirements using semantic CTFE.

The public mode vocabulary stays at :mod:`NodeForge.evaluation_modes`; this
module owns the semantic operation that selects an available representation.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass

from ..evaluation_modes import EvaluationMode
from .consteval import ConstEvalUnavailable, _const_eval


@dataclass(frozen=True)
class CompileTimeSelection:
    """A compile-time representation selected for one consumer."""

    value: object


@dataclass(frozen=True)
class RuntimeRequired:
    """Signal that the caller must acquire the normal runtime representation."""


def resolve_argument_evaluation(
    expr: ast.expr,
    consts: Mapping[str, object],
    mode: EvaluationMode,
) -> CompileTimeSelection | RuntimeRequired:
    """Select compile-time or runtime representation under ``mode``.

    Runtime semantic traversal remains the caller's responsibility. In mixed
    mode, only genuine :class:`ConstEvalUnavailable` selects runtime; hard
    compiler errors from compile-time evaluation propagate unchanged.
    """
    if mode is EvaluationMode.RUNTIME_ONLY:
        return RuntimeRequired()
    if mode not in {
        EvaluationMode.COMPILE_TIME_ONLY,
        EvaluationMode.COMPILE_TIME_OR_RUNTIME,
    }:
        raise TypeError(f"Unsupported evaluation mode: {mode!r}")

    try:
        value = _const_eval(expr, consts)
    except ConstEvalUnavailable:
        if mode is EvaluationMode.COMPILE_TIME_OR_RUNTIME:
            return RuntimeRequired()
        raise
    return CompileTimeSelection(value)


__all__ = [
    "CompileTimeSelection",
    "RuntimeRequired",
    "resolve_argument_evaluation",
]
