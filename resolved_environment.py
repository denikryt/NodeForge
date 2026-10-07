"""Immutable package, catalog, and system selections for one compilation session."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Never

from .errors import CompileError


_CATALOG_NAMES = frozenset({"functions", "examples", "local"})


@dataclass(frozen=True)
class ResolvedCompileErrorFailure:
    """Replay a deferred catalog CompileError without retaining traceback state."""

    exception_args: tuple[object, ...]

    def __post_init__(self) -> None:
        """Freeze the captured constructor arguments."""
        object.__setattr__(self, "exception_args", tuple(self.exception_args))

    def raise_error(self) -> Never:
        """Raise a fresh CompileError carrying the captured diagnostic."""
        raise CompileError(*self.exception_args)


@dataclass(frozen=True)
class ResolvedOSErrorFailure:
    """Replay a deferred filesystem error with its complete public diagnostic."""

    exception_type: type[OSError]
    exception_args: tuple[object, ...]
    errno: int | None
    strerror: str | None
    filename: object | None
    filename2: object | None
    winerror: int | None

    def __post_init__(self) -> None:
        """Validate the concrete error class and freeze constructor arguments."""
        if not isinstance(self.exception_type, type) or not issubclass(self.exception_type, OSError):
            raise TypeError("Resolved OSError failure requires an OSError subclass")
        object.__setattr__(self, "exception_args", tuple(self.exception_args))

    def raise_error(self) -> Never:
        """Raise a fresh filesystem error without adding absent path fields."""
        error = self.exception_type(*self.exception_args)
        error.errno = self.errno
        error.strerror = self.strerror
        if self.filename is not None:
            error.filename = self.filename
        if self.filename2 is not None:
            error.filename2 = self.filename2
        if self.winerror is not None:
            error.winerror = self.winerror
        raise error


ResolvedCatalogFailure = ResolvedCompileErrorFailure | ResolvedOSErrorFailure


@dataclass(frozen=True)
class ResolvedCatalog:
    """Immutable success or failure result for one public catalog namespace."""

    namespace: str
    entries: Mapping[str, "LibraryEntryRecord"]
    failure: ResolvedCatalogFailure | None = None

    def __post_init__(self) -> None:
        """Validate record placement and defensively freeze the entry mapping."""
        if self.namespace not in _CATALOG_NAMES:
            raise CompileError(f"Unknown library catalog: {self.namespace}")
        copied = dict(self.entries)
        if self.failure is not None and copied:
            raise ValueError("Failed resolved catalog cannot contain partial entries")
        for name, record in copied.items():
            if record.namespace != self.namespace or record.name != name:
                raise ValueError("Resolved catalog record does not match its namespace/name key")
        object.__setattr__(self, "entries", MappingProxyType(copied))

    def _raise_if_failed(self) -> None:
        """Replay the stored discovery failure before exposing catalog data."""
        if self.failure is not None:
            self.failure.raise_error()

    def names(self) -> frozenset[str]:
        """Return immutable public names or replay the stored discovery failure."""
        self._raise_if_failed()
        return frozenset(self.entries)

    def find(self, name: str) -> "LibraryEntryRecord | None":
        """Return one selected record or replay the stored discovery failure."""
        self._raise_if_failed()
        return self.entries.get(name)

    def records(self) -> tuple["LibraryEntryRecord", ...]:
        """Return selected records in deterministic public-name order."""
        self._raise_if_failed()
        return tuple(sorted(self.entries.values(), key=lambda record: record.name.lower()))


@dataclass(frozen=True)
class PackageCallableExport:
    """One owner-qualified public callable slot in a resolved package namespace."""

    package_id: str
    name: str
    record: "LibraryEntryRecord | None" = None
    extension_callable_id: object | None = None

    def __post_init__(self) -> None:
        """Validate owner/name provenance without retaining implementation objects."""
        from .extensions.contracts import ExtensionCallableId

        if not isinstance(self.package_id, str) or not self.package_id:
            raise ValueError("package callable export requires package_id")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("package callable export requires a public member name")
        record = self.record
        extension_id = self.extension_callable_id
        if record is None and extension_id is None:
            raise ValueError("package callable export requires one semantic target")
        if record is not None:
            if (
                getattr(record, "namespace", None) != "functions"
                or getattr(record, "name", None) != self.name
                or getattr(record, "package_id", None) != self.package_id
            ):
                raise ValueError("package callable export record does not match owner/member")
        if extension_id is not None:
            if not isinstance(extension_id, ExtensionCallableId) or extension_id.name != self.name:
                raise TypeError("package callable export extension target must match member name")
            owner = tuple(extension_id.owner)
            if self.package_id not in owner:
                raise ValueError("package callable export extension target does not match package owner")
        if record is not None and extension_id is not None and getattr(record, "source_path", None) is not None:
            raise ValueError("source-backed package export cannot also select an extension target")


@dataclass(frozen=True)
class ResolvedPackageNamespace:
    """Immutable callable namespace published by one canonical package owner."""

    package_id: str
    import_name: str
    package_name: str
    package_version: str
    exports: Mapping[str, PackageCallableExport]

    def __post_init__(self) -> None:
        """Freeze exports and require every member to belong to this package."""
        copied = dict(self.exports)
        if not self.package_id or not self.import_name:
            raise ValueError("resolved package namespace requires package_id and import_name")
        for name, export in copied.items():
            if name != export.name or export.package_id != self.package_id:
                raise ValueError("resolved package export key does not match package owner/member")
        object.__setattr__(self, "exports", MappingProxyType(copied))

    def find(self, name: str) -> PackageCallableExport | None:
        """Return one exact public member from this package namespace."""
        return self.exports.get(name)


@dataclass(frozen=True)
class ResolvedEnvironment:
    """Immutable external callable selection shared by one root compilation."""

    catalogs: Mapping[str, ResolvedCatalog]
    package_namespaces: Mapping[str, ResolvedPackageNamespace] = field(default_factory=dict)
    extension_registry: object | None = None

    def __post_init__(self) -> None:
        """Validate and freeze catalogs, owner-qualified packages, and extension registry."""
        from .extensions.registry import ExtensionRegistry

        catalogs = dict(self.catalogs)
        if frozenset(catalogs) != _CATALOG_NAMES:
            raise ValueError("Resolved environment requires functions, examples, and local catalogs")
        for namespace, catalog in catalogs.items():
            if catalog.namespace != namespace:
                raise ValueError("Resolved environment catalog key does not match catalog namespace")

        namespaces = dict(self.package_namespaces)
        import_index: dict[str, str] = {}
        for package_id, namespace in namespaces.items():
            if not isinstance(namespace, ResolvedPackageNamespace) or namespace.package_id != package_id:
                raise ValueError("Resolved package namespace key does not match canonical package id")
            previous = import_index.get(namespace.import_name)
            if previous is not None and previous != package_id:
                raise ValueError("Resolved environment package import names must be unique")
            import_index[namespace.import_name] = package_id

        registry = self.extension_registry
        if registry is None:
            registry = ExtensionRegistry.empty()
        if not isinstance(registry, ExtensionRegistry):
            raise TypeError("extension_registry must be an ExtensionRegistry")
        for namespace in namespaces.values():
            for export in namespace.exports.values():
                callable_id = export.extension_callable_id
                if callable_id is not None and not registry.contains(callable_id):
                    raise ValueError("Resolved package extension callable is missing from extension registry")

        object.__setattr__(self, "catalogs", MappingProxyType(catalogs))
        object.__setattr__(self, "package_namespaces", MappingProxyType(namespaces))
        object.__setattr__(self, "_package_import_index", MappingProxyType(import_index))
        object.__setattr__(self, "extension_registry", registry)

    def catalog(self, namespace: str) -> ResolvedCatalog:
        """Return one supported non-package/global catalog snapshot."""
        try:
            return self.catalogs[namespace]
        except KeyError as exc:
            raise CompileError(f"Unknown library catalog: {namespace}") from exc

    def package_by_import_name(self, import_name: str) -> ResolvedPackageNamespace | None:
        """Return the exact active package namespace for one source import name."""
        package_id = self._package_import_index.get(import_name)
        return None if package_id is None else self.package_namespaces[package_id]

    def package_by_id(self, package_id: str) -> ResolvedPackageNamespace | None:
        """Return the exact active package namespace for one canonical package id."""
        return self.package_namespaces.get(package_id)

    def package_function_record(self, package_id: str, name: str) -> "LibraryEntryRecord | None":
        """Return one exact source/native Functions record for a canonical package member."""
        namespace = self.package_by_id(package_id)
        if namespace is None:
            return None
        export = namespace.find(name)
        if export is None:
            return None
        return export.record

    def package_function_records(self) -> tuple["LibraryEntryRecord", ...]:
        """Return all package Functions records without collapsing same-named owners."""
        records = []
        for namespace in self.package_namespaces.values():
            for export in namespace.exports.values():
                record = export.record
                if record is not None:
                    records.append(record)
        return tuple(
            sorted(
                records,
                key=lambda record: (
                    self.package_namespaces[record.package_id].import_name.lower(),
                    record.name.lower(),
                    record.package_id,
                ),
            )
        )


__all__ = [
    "PackageCallableExport",
    "ResolvedCatalog",
    "ResolvedCatalogFailure",
    "ResolvedCompileErrorFailure",
    "ResolvedEnvironment",
    "ResolvedOSErrorFailure",
    "ResolvedPackageNamespace",
]
