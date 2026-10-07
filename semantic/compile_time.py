"""Compile-time value carriers, predicates, snapshots, and merge helpers."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from ..errors import CompileError
from ..numeric_semantics import normalize_float_constant


class ConstVector(tuple):
    """Immutable three-component compile-time vector carrier."""


def _is_const_vector(value):
    """Return True for a three-component compiler-owned constant vector."""
    return isinstance(value, ConstVector) and len(value) == 3


def _is_const_number(value):
    """Return True for static scalar numbers, excluding booleans."""
    return type(value) in {int, float}


def _is_const_vector_like(value):
    """Return True for accepted static three-component vector carriers."""
    return _is_const_vector(value) or (
        isinstance(value, (tuple, list))
        and len(value) == 3
        and all(_is_const_number(component) for component in value)
    )


def _as_float_const(value, context="value"):
    """Return one compile-time Number as a canonical NodeForge Float."""
    if _is_const_number(value):
        return normalize_float_constant(value)
    raise CompileError(f"Expected numeric compile-time {context}")


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


def merge_runtime_if_compile_time(
    base_snapshot: CompileTimeSnapshot,
    true_state: CompileTimeState,
    false_state: CompileTimeState,
) -> CompileTimeSnapshot:
    """Conservatively join compile-time facts from two runtime branch exits.

    Only value categories whose equality contract is already stable are
    merged by value.  Alias-sensitive values may survive only when both branch
    exits still reference the exact object inherited from the incoming snapshot.
    """
    base_values = base_snapshot.values
    true_values = true_state.values
    false_values = false_state.values
    merged = {}

    for name in true_values.keys() & false_values.keys():
        true_value = true_values[name]
        false_value = false_values[name]

        if type(true_value) is type(false_value) and type(true_value) in {bool, int, str}:
            if true_value == false_value:
                merged[name] = true_value
            continue

        # Float and ConstVector equivalence intentionally remains undefined until
        # canonical numeric semantics owns representation-aware equality.
        if isinstance(true_value, (float, ConstVector)) or isinstance(false_value, (float, ConstVector)):
            continue

        if name not in base_values:
            continue
        inherited = base_values[name]
        if true_value is inherited and false_value is inherited:
            merged[name] = inherited

    return CompileTimeSnapshot(merged)


__all__ = [
    "ConstVector",
    "_is_const_vector",
    "_is_const_number",
    "_is_const_vector_like",
    "_as_float_const",
    "CompileTimeSnapshot",
    "CompileTimeState",
    "merge_runtime_if_compile_time",
]
