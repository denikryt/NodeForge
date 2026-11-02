"""Semantic records for source import and package namespace bindings."""

from __future__ import annotations

from dataclasses import dataclass

from ..compiler_identities import SourceDefinitionRef
from ..errors import CompileError

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
class SourceModuleNamespaceBinding:
    """Bind one selected physical .nf file as an importable definition namespace."""

    source_name: str
    record: object
    snapshot: object
    namespace: str = "local"

    def __post_init__(self) -> None:
        """Require a module snapshot of the exact selected canonical source owner."""
        from .source_modules import SourceModuleSnapshot
        if not self.source_name or not isinstance(self.snapshot, SourceModuleSnapshot):
            raise TypeError("Module namespace requires one captured .nf source snapshot")
        if self.record.source_origin != self.snapshot.module_id.origin:
            raise ValueError("Module binding snapshot does not match the selected source origin")

    def member(self, name: str) -> "SourceDefinitionBinding":
        """Resolve only definitions physically declared within this source file."""
        return SourceDefinitionBinding(self.namespace, name, self.record, self.snapshot.definition_ref(name), self.snapshot)


@dataclass(frozen=True)
class SourceDefinitionBinding:
    """Bind one exact source definition, independently of import alias spelling."""

    namespace: str
    canonical_name: str
    record: object
    definition: SourceDefinitionRef
    snapshot: object

    def __post_init__(self) -> None:
        """Ensure the source member and physical module snapshot agree."""
        from .source_modules import SourceModuleSnapshot
        if (not isinstance(self.snapshot, SourceModuleSnapshot)
                or not isinstance(self.definition, SourceDefinitionRef)
                or self.definition.module != self.snapshot.module_id
                or self.definition.name != self.canonical_name
                or self.definition.name not in self.snapshot.definitions):
            raise ValueError("Source definition binding needs a matching declared member")


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
    expose_unqualified: bool = False
    namespace: str = "local"

    def __post_init__(self):
        """Reject descriptors from a different or ambiguous snapshot at binding time."""
        from ..local_index import DirectorySourceIndex, LocalDirectoryCandidate
        if type(self.expose_unqualified) is not bool:
            raise TypeError("Directory namespace bare-call capability must be Bool")
        if (not self.source_name or not isinstance(self.index_snapshot, DirectorySourceIndex)
                or self.namespace not in {"local", "examples"}
                or self.index_snapshot.namespace != self.namespace
                or not isinstance(self.directory, LocalDirectoryCandidate)):
            raise ValueError("Invalid directory namespace binding")
        if self.index_snapshot.resolve_directory(self.directory.import_path) != self.directory:
            raise ValueError("Local namespace directory does not belong to its snapshot")


__all__ = ["LibraryBinding", "PackageNamespaceBinding", "LocalDirectoryNamespaceBinding",
           "SourceModuleNamespaceBinding", "SourceDefinitionBinding"]
