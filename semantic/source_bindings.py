"""Semantic records for source import and package namespace bindings."""

from __future__ import annotations

from dataclasses import dataclass

from ..extensions.contracts import ExtensionCallableId
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


@dataclass(frozen=True)
class LocalDirectoryNamespaceBinding:
    """Bind a source alias to a directory selected from one frozen Local index."""

    source_name: str
    index_snapshot: object
    directory: object

    def __post_init__(self):
        """Reject descriptors from a different or ambiguous snapshot at binding time."""
        from ..local_index import LocalSourceIndex, LocalDirectoryCandidate
        if not self.source_name or not isinstance(self.index_snapshot, LocalSourceIndex) or not isinstance(self.directory, LocalDirectoryCandidate):
            raise ValueError("Invalid Local directory namespace binding")
        if self.index_snapshot.resolve_directory(self.directory.import_path) != self.directory:
            raise ValueError("Local namespace directory does not belong to its snapshot")


__all__ = ["LibraryBinding", "PackageNamespaceBinding", "LocalDirectoryNamespaceBinding"]
