"""Immutable package, catalog, and system selections for one compilation session."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Never, TYPE_CHECKING

from .errors import CompileError
from . import packages

if TYPE_CHECKING:
    from .library import LibraryEntryRecord


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
        from .extension_contracts import ExtensionCallableId

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
        from .extension_registry import ExtensionRegistry

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


def _resolved_failure_from_exception(exc: Exception) -> ResolvedCatalogFailure:
    """Detach one supported discovery/normalization failure for catalog replay."""
    if isinstance(exc, CompileError):
        return ResolvedCompileErrorFailure(tuple(exc.args))
    if isinstance(exc, OSError):
        return ResolvedOSErrorFailure(
            exception_type=type(exc),
            exception_args=tuple(exc.args),
            errno=exc.errno,
            strerror=exc.strerror,
            filename=exc.filename,
            filename2=exc.filename2,
            winerror=getattr(exc, "winerror", None),
        )
    return ResolvedCompileErrorFailure((str(exc),))



def resolve_environment() -> ResolvedEnvironment:
    """Resolve one coherent owner-qualified external callable environment snapshot."""
    from . import library
    from .extension_registry import (
        ExtensionOwnerSession,
        ExtensionRegistry,
        capture_owner_code_snapshot,
        library_owner_key,
        system_owner_key,
    )

    manifests = tuple(packages.active_package_manifest_snapshot())
    manifests_by_id = {manifest.package_id: manifest for manifest in manifests}
    import_owners: dict[str, str] = {}
    for manifest in manifests:
        previous = import_owners.get(manifest.import_name)
        if previous is not None and previous != manifest.package_id:
            raise CompileError(
                f"Package import name {manifest.import_name!r} is owned by both {previous} and {manifest.package_id}"
            )
        import_owners[manifest.import_name] = manifest.package_id

    raw_system_records = tuple(packages.system_package_records_from_manifests(manifests))

    raw_candidates: dict[str, tuple[object, ...]] = {}
    discovery_failures: dict[str, Exception] = {}
    deferred_package_discovery_failures: dict[tuple[str, str], Exception] = {}
    for namespace in ("functions", "examples"):
        collected: list[object] = []
        if namespace == "examples":
            try:
                collected.extend(
                    library._candidate_records_from_inputs(
                        namespace,
                        package_roots=(),
                        include_builtin_catalog=True,
                    )
                )
            except (CompileError, OSError) as exc:
                discovery_failures[namespace] = exc
        for root in packages.library_roots_from_manifests(namespace, manifests):
            try:
                collected.extend(
                    library._candidate_records_from_inputs(
                        namespace,
                        package_roots=(root,),
                        include_builtin_catalog=False,
                    )
                )
            except (CompileError, OSError) as exc:
                deferred_package_discovery_failures[(namespace, root.package_id)] = exc
        raw_candidates[namespace] = tuple(collected)

    try:
        local_registry = tuple(library._read_local_source_registry())
        raw_candidates["local"] = tuple(
            library._candidate_records_from_inputs(
                "local",
                package_roots=(),
                local_registry=local_registry,
            )
        )
    except (CompileError, OSError) as exc:
        raw_candidates["local"] = ()
        discovery_failures["local"] = exc

    system_sessions: dict[tuple[str, ...], ExtensionOwnerSession] = {}
    system_snapshot_failures: dict[str, Exception] = {}
    for record in raw_system_records:
        try:
            snapshot = capture_owner_code_snapshot(system_owner_key(record), record.root)
            system_sessions[system_owner_key(record)] = ExtensionOwnerSession(snapshot)
        except (CompileError, OSError) as exc:
            system_snapshot_failures.setdefault(record.package_id, exc)

    library_sessions: dict[tuple[str, ...], ExtensionOwnerSession] = {}
    deferred_library_failures: dict[tuple[str, ...], Exception] = {}
    for namespace in ("functions", "examples"):
        for record in raw_candidates.get(namespace, ()):
            if getattr(record, "interface_path", None) is None or getattr(record, "source_path", None) is not None:
                continue
            key = library_owner_key(record)
            try:
                snapshot = capture_owner_code_snapshot(key, record.interface_path.parent)
                library_sessions[key] = ExtensionOwnerSession(snapshot)
            except (CompileError, OSError) as exc:
                deferred_library_failures[key] = exc

    systems_by_package: dict[str, list[object]] = {package_id: [] for package_id in manifests_by_id}
    for record in raw_system_records:
        systems_by_package.setdefault(record.package_id, []).append(record)
    functions_by_package: dict[str, list[object]] = {package_id: [] for package_id in manifests_by_id}
    for record in raw_candidates.get("functions", ()):
        functions_by_package.setdefault(record.package_id, []).append(record)

    package_namespaces: dict[str, ResolvedPackageNamespace] = {}
    admitted_packages: set[str] = set()
    registry_sessions: list[ExtensionOwnerSession] = []

    for package_id in sorted(manifests_by_id):
        manifest = manifests_by_id[package_id]
        try:
            discovery_failure = deferred_package_discovery_failures.get(("functions", package_id))
            if discovery_failure is not None:
                raise discovery_failure
            snapshot_failure = system_snapshot_failures.get(package_id)
            if snapshot_failure is not None:
                raise snapshot_failure

            exports: dict[str, PackageCallableExport] = {}
            package_sessions: list[ExtensionOwnerSession] = []

            for record in systems_by_package.get(package_id, ()):
                if record.interface_path is None:
                    raise CompileError(
                        f"Package {package_id!r} contains unsupported Extension API v1 system owner"
                    )
                session = system_sessions[system_owner_key(record)]
                families, _refs = session.normalize_interface()
                package_sessions.append(session)
                for callable_id in families:
                    name = callable_id.name
                    if name in exports:
                        raise CompileError(f"Duplicate package callable {name!r} inside {package_id}")
                    exports[name] = PackageCallableExport(package_id, name, extension_callable_id=callable_id)

            for record in functions_by_package.get(package_id, ()):
                name = record.name
                if name in exports:
                    raise CompileError(f"Duplicate package callable {name!r} inside {package_id}")
                extension_callable_id = None
                if getattr(record, "interface_path", None) is not None and getattr(record, "source_path", None) is None:
                    key = library_owner_key(record)
                    deferred = deferred_library_failures.get(key)
                    if deferred is not None:
                        raise deferred
                    session = library_sessions.get(key)
                    if session is None:
                        raise CompileError(f"Extension library owner {record.name!r} has no captured owner snapshot")
                    families, _refs = session.normalize_interface()
                    family_names = [callable_id.name for callable_id in families]
                    if family_names != [record.name]:
                        raise CompileError(
                            f"Native-only functions extension {record.name!r} must declare exactly one EXTENSIONS key with the same name"
                        )
                    extension_callable_id = next(iter(families))
                    package_sessions.append(session)
                exports[name] = PackageCallableExport(
                    package_id,
                    name,
                    record=record,
                    extension_callable_id=extension_callable_id,
                )

            namespace = ResolvedPackageNamespace(
                package_id=package_id,
                import_name=manifest.import_name,
                package_name=manifest.name,
                package_version=manifest.version,
                exports=exports,
            )
        except (CompileError, OSError, packages.PackageError):
            continue

        package_namespaces[package_id] = namespace
        admitted_packages.add(package_id)
        registry_sessions.extend(package_sessions)

    catalogs: dict[str, ResolvedCatalog] = {"functions": ResolvedCatalog("functions", {})}
    for namespace in ("examples", "local"):
        failure = discovery_failures.get(namespace)
        candidates = []
        if failure is None and namespace == "examples":
            for package_id in sorted(admitted_packages):
                deferred = deferred_package_discovery_failures.get((namespace, package_id))
                if deferred is not None:
                    failure = deferred
                    break
        if failure is None:
            for record in raw_candidates.get(namespace, ()):
                package_id = str(getattr(record, "package_id", ""))
                if package_id and package_id not in admitted_packages:
                    continue
                candidates.append(record)
        if failure is not None:
            catalogs[namespace] = ResolvedCatalog(
                namespace, {}, _resolved_failure_from_exception(failure)
            )
            continue
        try:
            entries = library._unique_records_from_candidates(namespace, tuple(candidates))
            selected_sessions = []
            for record in entries.values():
                if getattr(record, "interface_path", None) is None or getattr(record, "source_path", None) is not None:
                    continue
                key = library_owner_key(record)
                deferred = deferred_library_failures.get(key)
                if deferred is not None:
                    raise deferred
                session = library_sessions.get(key)
                if session is None:
                    raise CompileError(f"Extension library owner {record.name!r} has no captured owner snapshot")
                families, _refs = session.normalize_interface()
                if [callable_id.name for callable_id in families] != [record.name]:
                    raise CompileError(
                        f"Native-only {namespace} extension {record.name!r} must declare exactly one EXTENSIONS key with the same name"
                    )
                selected_sessions.append(session)
        except (CompileError, OSError) as exc:
            catalogs[namespace] = ResolvedCatalog(
                namespace, {}, _resolved_failure_from_exception(exc)
            )
        else:
            catalogs[namespace] = ResolvedCatalog(namespace, entries)
            registry_sessions.extend(selected_sessions)

    registry = ExtensionRegistry(tuple(registry_sessions))
    return ResolvedEnvironment(
        catalogs=catalogs,
        package_namespaces=package_namespaces,
        extension_registry=registry,
    )


__all__ = [
    "PackageCallableExport",
    "ResolvedCatalog",
    "ResolvedCatalogFailure",
    "ResolvedCompileErrorFailure",
    "ResolvedEnvironment",
    "ResolvedOSErrorFailure",
    "ResolvedPackageNamespace",
    "resolve_environment",
]
