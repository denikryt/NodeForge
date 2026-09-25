"""Public frontend-semantic value protocol for declarative Python extensions."""

from __future__ import annotations

from .nf_types import NFType


class RuntimeRef:
    """Opaque semantic reference to one runtime NodeForge value for the active invocation."""

    __slots__ = ("_token", "_typ")

    def __init__(self, token: object, typ: NFType):
        """Create one compiler-issued runtime reference token."""
        if not isinstance(typ, NFType):
            raise TypeError("RuntimeRef typ must be an NFType")
        self._token = token
        self._typ = typ

    @property
    def typ(self) -> NFType:
        """Return the actual canonical runtime type represented by this reference."""
        return self._typ

    def __repr__(self) -> str:
        """Return a diagnostic representation without exposing the internal token."""
        return f"RuntimeRef({self._typ.value})"


__all__ = ["RuntimeRef"]
