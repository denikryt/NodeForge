"""Shared NodeForge attribute-domain semantics for frontend and backend checks."""

from __future__ import annotations

from ..errors import CompileError


ATTRIBUTE_DOMAINS = frozenset({"POINT", "EDGE", "FACE", "CORNER", "CURVE", "INSTANCE"})


def normalize_attribute_domain(domain: str, context: str) -> str:
    """Return one canonical NodeForge attribute-domain token.

    Source-language default selection belongs to the consuming semantic
    analyzer. This helper only normalizes and validates an explicitly supplied
    domain value, so falsey input is never converted into a default domain.
    """
    if not isinstance(domain, str) or not domain:
        raise CompileError(
            f"Unsupported {context} domain. Use POINT, EDGE, FACE, CORNER, CURVE or INSTANCE"
        )
    normalized = domain.upper()
    if normalized not in ATTRIBUTE_DOMAINS:
        raise CompileError(
            f"Unsupported {context} domain. Use POINT, EDGE, FACE, CORNER, CURVE or INSTANCE"
        )
    return normalized


__all__ = ["ATTRIBUTE_DOMAINS", "normalize_attribute_domain"]
