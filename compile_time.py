"""Compile-time state, compiler-only object protocol, and rejection helpers."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .errors import CompileError


@dataclass(frozen=True)
class CompileTimeSnapshot:
    """Detached shallow snapshot of const-evaluable source bindings."""

    values: Mapping[str, object]

    def __post_init__(self) -> None:
        """Detach the mapping while preserving contained value identity."""
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


class CompileTimeState:
    """Own mutable const-evaluable source-name bindings for one compilation domain."""

    def __init__(self, values=None, *, adopt_mapping=False):
        """Initialize from *values*, optionally adopting an exact mutable dict object."""
        if adopt_mapping:
            if values is None:
                values = {}
            if not isinstance(values, dict):
                raise TypeError("adopt_mapping requires a dict")
            self._values = values
        else:
            self._values = dict(values or {})
        self._view = MappingProxyType(self._values)

    @property
    def values(self) -> Mapping[str, object]:
        """Return the read-only live mapping view used by const-eval helpers."""
        return self._view

    def contains(self, name: str) -> bool:
        """Return whether *name* has a compile-time binding."""
        return name in self._values

    def get(self, name: str, default=None):
        """Return one compile-time binding, or *default* when absent."""
        return self._values.get(name, default)

    def bind(self, name: str, value) -> None:
        """Publish one compile-time source binding."""
        self._values[name] = value

    def discard(self, name: str) -> None:
        """Remove one compile-time binding when present."""
        self._values.pop(name, None)

    def snapshot(self) -> CompileTimeSnapshot:
        """Return a detached shallow snapshot of the current mapping."""
        return CompileTimeSnapshot(self._values)

    def fork(self) -> "CompileTimeState":
        """Return a shallow child state retaining contained object identity."""
        return CompileTimeState(self._values)

    def replace(self, snapshot_or_state) -> None:
        """Replace contents in-place without replacing the owned mapping object."""
        if isinstance(snapshot_or_state, CompileTimeState):
            values = snapshot_or_state.values
        elif isinstance(snapshot_or_state, CompileTimeSnapshot):
            values = snapshot_or_state.values
        else:
            values = snapshot_or_state
        replacement = dict(values)
        self._values.clear()
        self._values.update(replacement)


@dataclass(frozen=True)
class CompileTimeObject:
    """Base class for compiler-only non-runtime objects.

    This protocol is distinct from :class:`CompileTimeState`: subclasses such as
    GeometryBuilder can carry compiler/backend state and therefore must remain outside
    the const-evaluable source-binding environment. Runtime node-value consumers must
    reject them before socket or type handling.
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
    """Return True when *value* is a compiler-only non-runtime object."""
    return isinstance(value, CompileTimeObject)


def reject_compile_time_object(value, context: str):
    """Raise a controlled error if *value* is compiler-only.

    Lists are checked recursively because arrays are script-level containers that
    can otherwise carry compiler-only objects into runtime consumers.
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


__all__ = [
    "CompileTimeObject",
    "CompileTimeSnapshot",
    "CompileTimeState",
    "is_compile_time_object",
    "reject_compile_time_object",
]
