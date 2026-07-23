"""Compile-time-only object protocol and rejection helpers."""

from dataclasses import dataclass

from .errors import CompileError


@dataclass(frozen=True)
class CompileTimeObject:
    """Base class for values that exist only while compiling DSL source.

    Compile-time objects can be assigned and passed through the compiler until a
    subsystem consumes them. Runtime node-value consumers must reject them before
    socket or type handling. The properties below are a final fail-closed guard
    for repository-owned code paths that accidentally miss a contextual check.
    """

    @property
    def typ(self):
        """Reject accidental use as a typed runtime node value."""
        raise CompileError(f"{type(self).__name__} is compile-time only and cannot be used as a runtime node value")

    @property
    def socket(self):
        """Reject accidental use as a runtime node socket."""
        raise CompileError(f"{type(self).__name__} is compile-time only and cannot be used as a runtime node socket")


def is_compile_time_object(value) -> bool:
    """Return True when *value* is a compile-time-only compiler object."""
    return isinstance(value, CompileTimeObject)


def reject_compile_time_object(value, context: str):
    """Raise a controlled error if *value* is compile-time-only.

    Lists are checked recursively because arrays are script-level containers that
    can otherwise carry compile-time-only objects into runtime consumers.
    """
    if isinstance(value, CompileTimeObject):
        usage_error = getattr(value, "usage_error", None)
        if callable(usage_error):
            raise CompileError(usage_error(context))
        raise CompileError(f"{type(value).__name__} is compile-time only and cannot be used in {context}")
    if isinstance(value, list):
        for item in value:
            reject_compile_time_object(item, context)
    return value


__all__ = ["CompileTimeObject", "is_compile_time_object", "reject_compile_time_object"]
