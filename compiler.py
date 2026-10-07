"""Public compiler facade and high-level Geometry Nodes group assembly."""

from .errors import CompileError
from .storage import (
    _extract_group_source,
    _get_or_create_scratch_text,
    _replace_text_contents,
)
from .update import (
    _apply_group_defaults_to_node,
    _capture_node_external_state,
    _restore_node_external_state,
    _capture_group_external_state,
    _restore_group_external_state,
)
from .blender.library_groups import (
    materialize_library_entry_group_for_record,
    resolve_reloadable_library_entry,
    update_materialized_library_entry_group_for_record,
)
from .semantic_group import analyze_group_source
from .semantic.source_callable_session import SourceCallableSession
from .blender_group_backend import BlenderGroupBackend, BlenderGroupBuildRequest
from .blender.group_assembly import _populate_group
from .resolved_environment import ResolvedEnvironment
from .environment_resolution import resolve_environment


class _ResolvedEnvironmentSlot:
    """Resolve at most one immutable environment for a compiler-owned backend."""

    def __init__(self, resolved_environment: ResolvedEnvironment | None = None):
        """Optionally seed the slot for direct catalog operations."""
        self._environment = resolved_environment

    def get(self) -> ResolvedEnvironment:
        """Return the seeded snapshot or resolve and retain one complete result."""
        if self._environment is None:
            self._environment = resolve_environment()
        return self._environment


class _ResolvedEnvironmentBoundBackend(BlenderGroupBackend):
    """Bind one immutable environment to semantic preparation and physical publication."""

    def __init__(self, *, populate_candidate, prepare_compilation, resolved_environment_for_session):
        """Bind population, preparation, and one compiler-session environment provider."""
        super().__init__(populate_candidate=populate_candidate, prepare_compilation=prepare_compilation)
        self._resolved_environment_for_session = resolved_environment_for_session

    def new_source_callable_session(self):
        """Create one root-attempt source/preparation session over the bound environment."""
        return SourceCallableSession(resolved_environment=self._resolved_environment_for_session())

    def compile_group_callback(self, source: str, name: str = "NodeForge Group", **kwargs):
        """Ensure public package builds share one source-call session across both phases."""
        kwargs.setdefault("source_callable_session", self.new_source_callable_session())
        return super().compile_group_callback(source, name=name, **kwargs)


def _new_group_backend(resolved_environment: ResolvedEnvironment | None = None):
    """Return a prepared-only physical backend bound to one environment snapshot."""
    slot = _ResolvedEnvironmentSlot(resolved_environment)

    def prepare(source, *, compilation_identity, source_callable_session=None, **kwargs):
        session = source_callable_session or SourceCallableSession(resolved_environment=slot.get())
        return analyze_group_source(
            source,
            compilation_identity=compilation_identity,
            resolved_environment=slot.get(),
            inherited_local_functions=kwargs.get("inherited_local_functions"),
            inherited_imported_library_functions=kwargs.get("inherited_imported_library_functions"),
            helper_namespace=kwargs.get("helper_namespace") or "NodeForge Group",
            source_callable_session=session,
        )

    return _ResolvedEnvironmentBoundBackend(
        populate_candidate=_populate_group,
        prepare_compilation=prepare,
        resolved_environment_for_session=slot.get,
    )


def _prepare_root_build(backend, source: str, *, name: str, existing_group=None):
    """Resolve final root identity and semantic compilation before Blender mutation."""
    session = backend.new_source_callable_session()
    identity = backend.resolve_root_compilation_identity(existing_group)
    prepared = backend.prepare_source_compilation(
        source,
        compilation_identity=identity,
        helper_namespace=name,
        source_callable_session=session,
    )
    return prepared, session


def create_expression_group(source: str, name: str = "NodeForge Group"):
    """Create a new Geometry Nodes group from one prepared NodeForge compilation."""
    backend = _new_group_backend()
    prepared, session = _prepare_root_build(backend, source, name=name)
    return backend.create_or_update(BlenderGroupBuildRequest(
        prepared_compilation=prepared,
        name=name,
        source_callable_session=session,
        helper_namespace=name,
    ))


def create_library_catalog_group(namespace: str, name: str):
    """Create a name-unique catalog group; Functions callers should pass owner identity."""
    environment = resolve_environment()
    if namespace == "functions":
        matches = [record for record in environment.package_function_records() if record.name == name]
        if len(matches) > 1:
            owners = ", ".join(sorted(record.package_id for record in matches))
            raise CompileError(
                f"Ambiguous functions library entry {name!r}: {owners}. Select a package owner explicitly."
            )
        record = matches[0] if matches else None
    else:
        record = environment.catalog(namespace).find(name)
    if record is None:
        raise CompileError(f"Unknown {namespace} library entry: {name}")
    return materialize_library_entry_group_for_record(record, _new_group_backend(environment))


def create_package_function_group(package_id: str, name: str):
    """Create or update one exact package-owned Functions entry."""
    environment = resolve_environment()
    record = environment.package_function_record(package_id, name)
    if record is None:
        raise CompileError(f"Unknown package function {package_id}/{name}")
    return materialize_library_entry_group_for_record(record, _new_group_backend(environment))


def update_library_catalog_group(group, namespace: str | None = None, name: str | None = None):
    """Reload a catalog-backed group using persisted owner identity where required."""
    environment = resolve_environment()
    record = resolve_reloadable_library_entry(
        group, namespace, name, resolved_environment=environment
    )
    return update_materialized_library_entry_group_for_record(
        record, group, _new_group_backend(environment)
    )


def update_expression_group(group, source: str):
    """Rebuild an existing Geometry Nodes group from one prepared compilation."""
    name = getattr(group, "name", "NodeForge Group")
    backend = _new_group_backend()
    prepared, session = _prepare_root_build(backend, source, name=name, existing_group=group)
    return backend.create_or_update(BlenderGroupBuildRequest(
        prepared_compilation=prepared,
        name=name,
        existing_group=group,
        source_callable_session=session,
        helper_namespace=name,
    ))


__all__ = [
    "CompileError",
    "create_expression_group",
    "update_expression_group",
    "update_library_catalog_group",
    "create_library_catalog_group",
    "create_package_function_group",
    "_apply_group_defaults_to_node",
    "_capture_node_external_state",
    "_restore_node_external_state",
    "_capture_group_external_state",
    "_restore_group_external_state",
    "_extract_group_source",
    "_get_or_create_scratch_text",
    "_replace_text_contents",
]
