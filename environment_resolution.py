"""Build immutable package/catalog selections for one compilation session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .errors import CompileError
from . import packages, catalog, local_sources
from .resolved_environment import (
    _freeze_package_selection,
    PackageCallableExport,
    ResolvedCatalog,
    ResolvedCatalogFailure,
    ResolvedCompileErrorFailure,
    ResolvedEnvironment,
    ResolvedOSErrorFailure,
    ResolvedPackageNamespace,
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



def _resolve_environment_parts(*, capture_local: bool):
    """Resolve shared package/extension selections with optional eager Local capture."""
    from .extensions.registry import (
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
                    catalog.candidate_records_from_inputs(
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
                    catalog.candidate_records_from_inputs(
                        namespace,
                        package_roots=(root,),
                        include_builtin_catalog=False,
                    )
                )
            except (CompileError, OSError) as exc:
                deferred_package_discovery_failures[(namespace, root.package_id)] = exc
        raw_candidates[namespace] = tuple(collected)

    local_catalog = _capture_local_catalog() if capture_local else None

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
    namespace = "examples"
    failure = discovery_failures.get(namespace)
    candidates = []
    if failure is None:
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
    else:
        try:
            entries = catalog.unique_records_from_candidates(namespace, tuple(candidates))
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
    return _ResolvedNonLocalParts(
        functions_catalog=catalogs["functions"],
        examples_catalog=catalogs["examples"],
        package_namespaces=package_namespaces,
        extension_registry=registry,
    ), local_catalog


def _capture_local_catalog() -> ResolvedCatalog:
    """Capture a complete immutable Local inventory or a replayable failure."""
    from .local_index import LocalSourceIndex

    try:
        roots, files = local_sources._catalog_input_paths()
        discovery = catalog.discover_local_sources(roots, files)
        index = LocalSourceIndex.from_discovery(*discovery)
        return ResolvedCatalog("local", {}, local_index=index)
    except (CompileError, OSError) as exc:
        return ResolvedCatalog("local", {}, _resolved_failure_from_exception(exc))


@dataclass(frozen=True)
class _ResolvedNonLocalParts:
    """Immutable package/extension selection shared by eager and compiler paths."""

    functions_catalog: ResolvedCatalog
    examples_catalog: ResolvedCatalog
    package_namespaces: Mapping[str, ResolvedPackageNamespace]
    extension_registry: object

    def __post_init__(self) -> None:
        """Validate catalogs and freeze package ownership at the resolution boundary."""
        if self.functions_catalog.namespace != "functions" or self.examples_catalog.namespace != "examples":
            raise ValueError("Non-Local catalog selection requires functions and examples")
        namespaces, import_index, registry = _freeze_package_selection(
            self.package_namespaces, self.extension_registry
        )
        object.__setattr__(self, "package_namespaces", namespaces)
        object.__setattr__(self, "_package_import_index", import_index)
        object.__setattr__(self, "extension_registry", registry)

    def package_by_import_name(self, import_name: str) -> ResolvedPackageNamespace | None:
        """Return the selected owner namespace without consulting package state."""
        package_id = self._package_import_index.get(import_name)
        return None if package_id is None else self.package_namespaces[package_id]


class _LocalCatalogSlot:
    """Capture Local once on semantic demand, including empty and failed results."""

    def __init__(self) -> None:
        """Start an unrequested slot owned by one root compilation attempt."""
        self._captured: ResolvedCatalog | None = None

    def get(self) -> ResolvedCatalog:
        """Publish one immutable capture result and reuse its exact object."""
        if self._captured is None:
            self._captured = _capture_local_catalog()
        return self._captured


@dataclass(frozen=True)
class _CompilationEnvironment:
    """Root-scoped non-Local selection with one semantic-demand Local slot."""

    _parts: _ResolvedNonLocalParts
    _local_slot: _LocalCatalogSlot

    @property
    def extension_registry(self):
        """Expose the registry selected before root semantic preparation."""
        return self._parts.extension_registry

    def catalog(self, namespace: str) -> ResolvedCatalog:
        """Select a catalog, capturing Local only when that namespace is requested."""
        if namespace == "local":
            return self._local_slot.get()
        if namespace == "functions":
            return self._parts.functions_catalog
        if namespace == "examples":
            return self._parts.examples_catalog
        raise CompileError(f"Unknown library catalog: {namespace}")

    def package_by_import_name(self, import_name: str) -> ResolvedPackageNamespace | None:
        """Delegate canonical package lookup to the captured non-Local selection."""
        return self._parts.package_by_import_name(import_name)


def _make_compilation_environment() -> _CompilationEnvironment:
    """Create fresh package selections and an unrequested Local slot for a root."""
    parts, _local = _resolve_environment_parts(capture_local=False)
    return _CompilationEnvironment(parts, _LocalCatalogSlot())


def resolve_environment() -> ResolvedEnvironment:
    """Resolve a complete eager package/extension/Local environment snapshot."""
    parts, local_catalog = _resolve_environment_parts(capture_local=True)
    assert local_catalog is not None
    return ResolvedEnvironment(
        catalogs={"functions": parts.functions_catalog,
                  "examples": parts.examples_catalog,
                  "local": local_catalog},
        package_namespaces=parts.package_namespaces,
        extension_registry=parts.extension_registry,
    )


__all__ = ["resolve_environment"]
