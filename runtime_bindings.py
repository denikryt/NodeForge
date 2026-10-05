"""Blender-independent compiler ownership for active runtime source bindings."""

from __future__ import annotations

from dataclasses import dataclass

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
    """Apply the canonical assignment-target reservation rule with established diagnostics."""
    label = reserved_binding_label(reserved_name_labels, name)
    if label is not None and not allows_existing_top_level_shadow(label):
        from .errors import CompileError
        raise CompileError(f"Cannot assign to {name}: name is {format_reserved_binding_label(label)}")


__all__ = ["RuntimeBindingSymbol", "reserved_binding_label", "format_reserved_binding_label", "allows_existing_top_level_shadow", "validate_runtime_binding_target"]
