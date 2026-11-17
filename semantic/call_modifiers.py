"""Semantic parsing of compiler-reserved source-call modifiers."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ..errors import CompileError
from .consteval import ConstEvalUnavailable, _const_eval


@dataclass(frozen=True)
class FunctionCallModifiers:
    """Compiler-reserved options extracted from one call before normal binding."""

    unique: bool = False
    unique_was_explicit: bool = False
    inline: bool | None = None


def extract_function_call_modifiers(
    expr: ast.Call, function_name: str, const_eval_values,
) -> tuple[ast.Call, FunctionCallModifiers]:
    """Strip reserved keywords, evaluating both in the caller's CT snapshot."""
    modifiers = {}
    ordinary = []
    for keyword in expr.keywords:
        if keyword.arg not in {"__unique__", "__inline__"}:
            ordinary.append(keyword)
            continue
        name = keyword.arg
        if name in modifiers:
            raise CompileError(f"{function_name}() got duplicate {name}")
        try:
            value = _const_eval(keyword.value, const_eval_values)
        except ConstEvalUnavailable as exc:
            raise CompileError(f"{function_name}() {name} must be a compile-time Bool") from exc
        if type(value) is not bool:
            raise CompileError(f"{function_name}() {name} must be a compile-time Bool")
        modifiers[name] = value
    cleaned = ast.copy_location(
        ast.Call(func=expr.func, args=list(expr.args), keywords=ordinary), expr,
    )
    return cleaned, FunctionCallModifiers(
        unique=modifiers.get("__unique__", False),
        unique_was_explicit="__unique__" in modifiers,
        inline=modifiers.get("__inline__"),
    )


def unsupported_unique(function_name: str) -> CompileError:
    """Return the established diagnostic for unsupported compiler options."""
    return CompileError(f"{function_name}() does not support __unique__")


def reject_unsupported_modifiers(function_name: str, modifiers: FunctionCallModifiers) -> None:
    """Reject compiler options on callables with no source-body boundary."""
    if modifiers.inline is not None:
        raise CompileError(f"{function_name}() does not support __inline__")
    if modifiers.unique_was_explicit:
        raise unsupported_unique(function_name)


__all__ = [
    "FunctionCallModifiers", "extract_function_call_modifiers",
    "unsupported_unique", "reject_unsupported_modifiers",
]
