"""Blender-independent compiler ownership for active runtime source bindings."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .compiler_identities import BindingId
from .nf_types import NFType


@dataclass(frozen=True)
class RuntimeBindingSymbol:
    """Frontend metadata for one resolved runtime binding slot."""

    binding_id: BindingId
    typ: NFType

    def __post_init__(self):
        """Reject non-canonical compiler identity and runtime type values."""
        if not isinstance(self.binding_id, BindingId):
            raise TypeError("binding_id must be a BindingId")
        if not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType")


class FrontendRuntimeBindings:
    """Own active runtime symbols and historical BindingId reservations for one compiler."""

    def __init__(self, owner_scope: str):
        """Initialize an empty source-name table for one exact compiler owner scope."""
        if not isinstance(owner_scope, str) or not owner_scope:
            raise ValueError("owner_scope must be a non-empty string")
        self._owner_scope = owner_scope
        self._active: dict[str, RuntimeBindingSymbol] = {}
        self._reserved_ids: dict[str, BindingId] = {}
        self._next_local_id = 0

    @property
    def owner_scope(self) -> str:
        """Return the compiler owner scope used by newly allocated BindingIds."""
        return self._owner_scope

    def bind(self, name: str, typ: NFType) -> RuntimeBindingSymbol:
        """Activate a runtime name, eagerly reserving its stable BindingId if needed."""
        if not isinstance(name, str) or not name:
            raise ValueError("runtime binding name must be a non-empty string")
        if not isinstance(typ, NFType):
            raise TypeError("typ must be an NFType")
        binding_id = self._reserved_ids.get(name)
        if binding_id is None:
            binding_id = BindingId(self._owner_scope, self._next_local_id)
            self._reserved_ids[name] = binding_id
            self._next_local_id += 1
        symbol = RuntimeBindingSymbol(binding_id, typ)
        self._active[name] = symbol
        return symbol

    def get(self, name: str) -> RuntimeBindingSymbol | None:
        """Return the active symbol for a source name, if any."""
        return self._active.get(name)

    def unbind(self, name: str) -> None:
        """Deactivate a runtime name while retaining its historical BindingId reservation."""
        self._active.pop(name, None)

    def snapshot(self) -> Mapping[str, RuntimeBindingSymbol]:
        """Return a detached immutable snapshot of active runtime symbols."""
        return MappingProxyType(dict(self._active))

    def validate_active_state(self, state: Mapping[str, RuntimeBindingSymbol]) -> None:
        """Validate a candidate active-state snapshot against historical reservations."""
        if not isinstance(state, Mapping):
            raise TypeError("runtime binding state must be a mapping")
        for name, symbol in state.items():
            if not isinstance(name, str) or not name:
                raise ValueError("runtime binding state contains an invalid name")
            if not isinstance(symbol, RuntimeBindingSymbol):
                raise TypeError("runtime binding state contains a non-RuntimeBindingSymbol")
            if symbol.binding_id.owner_scope != self._owner_scope:
                raise ValueError("runtime binding state uses another owner scope")
            reserved = self._reserved_ids.get(name)
            if reserved is None or reserved != symbol.binding_id:
                raise ValueError("runtime binding state does not match historical BindingId reservation")

    def restore_active_state(self, state: Mapping[str, RuntimeBindingSymbol]) -> None:
        """Replace active symbols after validation without rewinding historical allocation."""
        self.validate_active_state(state)
        self._active = dict(state)


def reserved_binding_label(reserved_name_labels, name: str):
    """Return the registered-name label for one binding target, if any."""
    return reserved_name_labels.get(name)


def format_reserved_binding_label(label: str) -> str:
    """Return the existing diagnostic suffix for one registered-name conflict."""
    if label == "DSL builtin":
        return "reserved by DSL builtin"
    if label == "imported function":
        return "already registered as imported function"
    if label == "local function":
        return "already registered as local function"
    if label == "type token":
        return "reserved by type token"
    return f"reserved by {label}"


def allows_existing_top_level_shadow(label: str | None) -> bool:
    """Return whether current top-level semantics permit rebinding this label."""
    return label in {"DSL builtin", "compile-time constant"}


def validate_runtime_binding_target(name: str, reserved_name_labels) -> None:
    """Apply the canonical assignment-target reservation rule with legacy diagnostics."""
    label = reserved_binding_label(reserved_name_labels, name)
    if label is not None and not allows_existing_top_level_shadow(label):
        from .errors import CompileError
        raise CompileError(f"Cannot assign to {name}: name is {format_reserved_binding_label(label)}")


__all__ = ["FrontendRuntimeBindings", "RuntimeBindingSymbol", "reserved_binding_label", "format_reserved_binding_label", "allows_existing_top_level_shadow", "validate_runtime_binding_target"]
