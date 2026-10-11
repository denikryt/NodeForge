"""Semantic parsing of compiler-reserved source-call modifiers."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ..errors import CompileError
from .consteval import ConstEvalUnavailable, _const_eval

@dataclass(frozen=True)
class FunctionCallModifiers:
    """Compiler-reserved metadata extracted from one simple function call."""

    unique: bool = False
    unique_was_explicit: bool = False


def extract_function_call_modifiers(expr: ast.Call, function_name: str, const_eval_values) -> tuple[ast.Call, FunctionCallModifiers]:
    """Return a copy of *expr* without compiler-reserved call modifiers.

    Only the exact keyword ``__unique__`` is reserved.  Its value must be a
    compile-time ``bool`` evaluated from the detached semantic constant snapshot, and
    the keyword is removed before ordinary argument binding sees the call.
    """

    unique_seen = False
    unique_value = False
    cleaned_keywords = []
    for kw in expr.keywords:
        if kw.arg != "__unique__":
            cleaned_keywords.append(kw)
            continue
        if unique_seen:
            raise CompileError(f"{function_name}() got duplicate __unique__")
        unique_seen = True
        if kw.arg is None:
            raise CompileError(f"{function_name}() does not support **kwargs")
        try:
            value = _const_eval(kw.value, const_eval_values)
        except ConstEvalUnavailable as exc:
            raise CompileError(f"{function_name}() __unique__ must be a compile-time Bool") from exc
        if type(value) is not bool:
            raise CompileError(f"{function_name}() __unique__ must be a compile-time Bool")
        unique_value = value
    cleaned = ast.copy_location(
        ast.Call(func=expr.func, args=list(expr.args), keywords=cleaned_keywords),
        expr,
    )
    return cleaned, FunctionCallModifiers(unique=unique_value, unique_was_explicit=unique_seen)


def unsupported_unique(function_name: str) -> CompileError:
    """Create the standard capability diagnostic for unsupported call kinds."""

    return CompileError(f"{function_name}() does not support __unique__")




__all__ = ["FunctionCallModifiers", "extract_function_call_modifiers", "unsupported_unique"]
