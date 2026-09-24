"""Immutable package, catalog, and system selections for one compilation session."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Never, TYPE_CHECKING

from .errors import CompileError
from . import packages
from .systems import registry as systems_registry

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
class ResolvedEnvironment:
    """Immutable external callable selection shared by one root compilation."""

    catalogs: Mapping[str, ResolvedCatalog]
    system_constructors: Mapping[str, systems_registry.ResolvedSystemConstructor]
    extension_registry: object | None = None
    extension_system_callables: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate and defensively freeze catalogs and both system generations."""
        from .extension_contracts import ExtensionCallableId
        from .extension_registry import ExtensionRegistry

        catalogs = dict(self.catalogs)
        if frozenset(catalogs) != _CATALOG_NAMES:
            raise ValueError("Resolved environment requires functions, examples, and local catalogs")
        for namespace, catalog in catalogs.items():
            if catalog.namespace != namespace:
                raise ValueError("Resolved environment catalog key does not match catalog namespace")
        systems = dict(self.system_constructors)
        for name, binding in systems.items():
            if binding.name != name:
                raise ValueError("Resolved system mapping key does not match constructor name")
        extension_systems = dict(self.extension_system_callables)
        for name, callable_id in extension_systems.items():
            if not isinstance(callable_id, ExtensionCallableId) or callable_id.name != name:
                raise ValueError("Resolved v2 system mapping key does not match extension callable identity")
        overlap = set(systems) & set(extension_systems)
        if overlap:
            raise ValueError(f"Resolved environment contains duplicate v1/v2 system names: {sorted(overlap)!r}")
        registry = self.extension_registry
        if registry is None:
            registry = ExtensionRegistry.empty()
        if not isinstance(registry, ExtensionRegistry):
            raise TypeError("extension_registry must be an ExtensionRegistry")
        for callable_id in extension_systems.values():
            if not registry.contains(callable_id):
                raise ValueError("Resolved v2 system callable is missing from extension registry")
        object.__setattr__(self, "catalogs", MappingProxyType(catalogs))
        object.__setattr__(self, "system_constructors", MappingProxyType(systems))
        object.__setattr__(self, "extension_registry", registry)
        object.__setattr__(self, "extension_system_callables", MappingProxyType(extension_systems))

    def catalog(self, namespace: str) -> ResolvedCatalog:
        """Return one supported resolved catalog."""
        try:
            return self.catalogs[namespace]
        except KeyError as exc:
            raise CompileError(f"Unknown library catalog: {namespace}") from exc

    def system_names(self) -> tuple[str, ...]:
        """Return retained-v1 and v2 system callable names in deterministic order."""
        return tuple(sorted(set(self.system_constructors) | set(self.extension_system_callables)))

    def system(self, name: str) -> systems_registry.ResolvedSystemConstructor | None:
        """Return the retained-v1 resolved owner for a constructor name."""
        return self.system_constructors.get(name)


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


def _core_validate_system_name(name: str, package_id: str) -> None:
    """Apply the package-owned core callable collision rule during root admission."""
    try:
        packages._validate_not_core_callable_name(name, package_id)
    except packages.PackageError as exc:
        raise CompileError(str(exc)) from exc


def resolve_environment() -> ResolvedEnvironment:
    """Resolve one coherent v1/v2 external callable environment snapshot."""
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
    raw_system_records = tuple(packages.system_package_records_from_manifests(manifests))

    raw_candidates: dict[str, tuple[object, ...]] = {}
    discovery_failures: dict[str, Exception] = {}
    deferred_package_discovery_failures: dict[tuple[str, str], Exception] = {}
    for namespace in ("functions", "examples"):
        collected: list[object] = []
        # Built-in examples are not package-owned and retain ordinary immediate
        # catalog failure behavior. Functions have no built-in package root.
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
        if record.interface_path is None:
            continue
        try:
            snapshot = capture_owner_code_snapshot(system_owner_key(record), record.root)
            system_sessions[system_owner_key(record)] = ExtensionOwnerSession(snapshot)
        except (CompileError, OSError) as exc:
            system_snapshot_failures.setdefault(record.package_id, exc)

    library_sessions: dict[tuple[str, ...], ExtensionOwnerSession] = {}
    deferred_library_failures: dict[tuple[str, ...], Exception] = {}
    immediate_library_failures: dict[str, Exception] = {}
    for namespace in ("functions", "examples"):
        for record in raw_candidates.get(namespace, ()):
            if getattr(record, "interface_path", None) is None or getattr(record, "source_path", None) is not None:
                continue
            key = library_owner_key(record)
            try:
                snapshot = capture_owner_code_snapshot(key, record.interface_path.parent)
                library_sessions[key] = ExtensionOwnerSession(snapshot)
            except (CompileError, OSError) as exc:
                if record.package_id:
                    deferred_library_failures[key] = exc
                else:
                    immediate_library_failures.setdefault(namespace, exc)

    records_by_package: dict[str, list[object]] = {package_id: [] for package_id in manifests_by_id}
    for record in raw_system_records:
        records_by_package.setdefault(record.package_id, []).append(record)

    admitted_packages: set[str] = set()
    admitted_v1_records: list[object] = []
    admitted_v2_sessions: list[ExtensionOwnerSession] = []
    v2_system_map: dict[str, object] = {}
    for package_id in sorted(manifests_by_id):
        package_records = records_by_package.get(package_id, [])
        try:
            snapshot_failure = system_snapshot_failures.get(package_id)
            if snapshot_failure is not None:
                raise snapshot_failure
            local_names: dict[str, str] = {}
            package_v1 = [record for record in package_records if record.interface_path is None]
            package_v2 = [record for record in package_records if record.interface_path is not None]
            if package_v1:
                legacy = systems_registry.resolve_constructors(package_v1)
                for name, binding in legacy.items():
                    _core_validate_system_name(name, package_id)
                    if name in local_names:
                        raise CompileError(f"Duplicate system constructor {name!r} inside {package_id}")
                    local_names[name] = binding.record.system_id
            normalized_sessions: list[ExtensionOwnerSession] = []
            normalized_ids: list[object] = []
            for record in package_v2:
                session = system_sessions[system_owner_key(record)]
                families, _refs = session.normalize_interface()
                normalized_sessions.append(session)
                for callable_id in families:
                    name = callable_id.name
                    _core_validate_system_name(name, package_id)
                    if name in local_names:
                        raise CompileError(f"Duplicate system constructor {name!r} inside {package_id}")
                    local_names[name] = record.system_id
                    normalized_ids.append(callable_id)
        except (CompileError, OSError, packages.PackageError):
            # Declaration-invalid packages are admission-filtered atomically. Their
            # package-owned library candidates/failures are discarded below.
            continue
        admitted_packages.add(package_id)
        admitted_v1_records.extend(package_v1)
        admitted_v2_sessions.extend(normalized_sessions)
        for callable_id in normalized_ids:
            if callable_id.name in v2_system_map:
                raise CompileError(f"Duplicate system constructor {callable_id.name!r} across active packages")
            v2_system_map[callable_id.name] = callable_id

    legacy_systems = dict(systems_registry.resolve_constructors(admitted_v1_records))
    for name in legacy_systems:
        if name in v2_system_map:
            owner = legacy_systems[name].record
            raise CompileError(
                f"Duplicate system constructor {name!r}: {owner.package_id}/{owner.system_id} and v2 owner"
            )
    complete_system_names = frozenset(set(legacy_systems) | set(v2_system_map))

    catalogs: dict[str, ResolvedCatalog] = {}
    selected_library_sessions: list[ExtensionOwnerSession] = []
    for namespace in ("functions", "examples", "local"):
        failure = discovery_failures.get(namespace) or immediate_library_failures.get(namespace)
        candidates = []
        if failure is None and namespace in {"functions", "examples"}:
            for package_id in sorted(admitted_packages):
                deferred_discovery = deferred_package_discovery_failures.get((namespace, package_id))
                if deferred_discovery is not None:
                    failure = deferred_discovery
                    break
        if failure is None:
            for record in raw_candidates.get(namespace, ()):
                package_id = str(getattr(record, "package_id", ""))
                if package_id and package_id not in admitted_packages:
                    continue
                candidates.append(record)
                deferred = deferred_library_failures.get(library_owner_key(record))
                if deferred is not None:
                    failure = deferred
                    break
        if failure is not None:
            catalogs[namespace] = ResolvedCatalog(namespace, {}, _resolved_failure_from_exception(failure))
            continue
        try:
            entries = library._unique_records_from_candidates(
                namespace,
                tuple(candidates),
                system_constructor_names=complete_system_names,
            )
            for record in entries.values():
                # Hybrid source/interface records remain discoverable so an unused
                # entry cannot poison the entire catalog. Semantic preparation owns
                # the deterministic migration diagnostic when such a callable is used.
                if getattr(record, "interface_path", None) is not None and getattr(record, "source_path", None) is not None:
                    continue
                if getattr(record, "interface_path", None) is None:
                    continue
                session = library_sessions.get(library_owner_key(record))
                if session is None:
                    raise CompileError(f"Extension library owner {record.name!r} has no captured owner snapshot")
                families, _refs = session.normalize_interface()
                family_names = [callable_id.name for callable_id in families]
                if family_names != [record.name]:
                    raise CompileError(
                        f"Native-only {namespace} extension {record.name!r} must declare exactly one EXTENSIONS key with the same name"
                    )
                selected_library_sessions.append(session)
        except (CompileError, OSError) as exc:
            catalogs[namespace] = ResolvedCatalog(namespace, {}, _resolved_failure_from_exception(exc))
        else:
            catalogs[namespace] = ResolvedCatalog(namespace, entries)

    registry = ExtensionRegistry((*admitted_v2_sessions, *selected_library_sessions))
    return ResolvedEnvironment(
        catalogs=catalogs,
        system_constructors=legacy_systems,
        extension_registry=registry,
        extension_system_callables=v2_system_map,
    )


__all__ = [
    "ResolvedCatalog",
    "ResolvedCatalogFailure",
    "ResolvedCompileErrorFailure",
    "ResolvedEnvironment",
    "ResolvedOSErrorFailure",
    "resolve_environment",
]
