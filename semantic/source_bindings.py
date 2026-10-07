"""Semantic records for source import and package namespace bindings."""

from __future__ import annotations

from dataclasses import dataclass

from ..extension_contracts import ExtensionCallableId
from ..resolved_environment import ResolvedPackageNamespace


@dataclass(frozen=True)
class LibraryBinding:
    """Resolve one source-local imported name to an exact catalog record."""

    namespace: str
    canonical_name: str
    record: object
    extension_callable_id: ExtensionCallableId | None = None

    def __post_init__(self) -> None:
        """Require the binding namespace/name to match its selected record."""
        if getattr(self.record, "namespace", None) != self.namespace or getattr(self.record, "name", None) != self.canonical_name:
            raise ValueError("Library binding does not match its resolved record")
        if self.extension_callable_id is not None and not isinstance(self.extension_callable_id, ExtensionCallableId):
            raise TypeError("LibraryBinding.extension_callable_id must be ExtensionCallableId or None")


@dataclass(frozen=True)
class PackageNamespaceBinding:
    """Bind one source-local alias to an exact resolved package namespace."""

    source_name: str
    namespace: object

    def __post_init__(self) -> None:
        """Require a source alias and canonical resolved package namespace."""
        if not isinstance(self.source_name, str) or not self.source_name:
            raise ValueError("Package namespace binding requires a non-empty source name")
        if not isinstance(self.namespace, ResolvedPackageNamespace):
            raise TypeError("Package namespace binding requires ResolvedPackageNamespace")

    @property
    def package_id(self) -> str:
        """Return canonical package identity independently from source alias spelling."""
        return self.namespace.package_id


__all__ = ["LibraryBinding", "PackageNamespaceBinding"]
