"""Build immutable package/catalog selections for one compilation session."""

from __future__ import annotations

from .errors import CompileError
from . import packages, catalog, local_sources
from .resolved_environment import (
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



def resolve_environment() -> ResolvedEnvironment:
    """Resolve one coherent owner-qualified external callable environment snapshot."""
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

    try:
        local_roots, local_files = local_sources._catalog_input_paths()
        raw_candidates["local"] = tuple(
            catalog.candidate_records_from_inputs(
                "local",
                package_roots=(),
                local_roots=local_roots,
                local_files=local_files,
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
    return ResolvedEnvironment(
        catalogs=catalogs,
        package_namespaces=package_namespaces,
        extension_registry=registry,
    )


__all__ = ["resolve_environment"]
