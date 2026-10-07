"""Physical Blender materialization and reload behavior for catalog entries."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import bpy

from ..errors import CompileError
from .group_authority import is_authority_ineligible_group
from ..compiler_identities import CORE_PACKAGE_ID, FunctionId, GroupCompilationIdentity, library_function_id, normalize_library_package_id
from ..semantic.ir import IRFunctionMaterialization
from .function_materializer import (
    FunctionMaterializationContext,
    FunctionMaterializer,
    MaterializedFunctionGroup,
    LibraryFunctionMaterializationSpec,
    LibraryFunctionUpdateSpec,
)
from ..function_instances import (
    FUNCTION_INSTANCE_KEY_PROP,
    direct_library_owner_scope,
)
from ..catalog import LibraryEntryRecord, _validate_resolved_record, load_library_entry_source_for_record

if TYPE_CHECKING:
    from ..resolved_environment import ResolvedEnvironment

def display_name_for_function(name: str) -> str:
    """Return the user-facing node title for a library entry name."""
    parts = [p for p in re.split(r"[_\s]+", name or "") if p]
    if not parts:
        return name or "Function"
    return "".join(part[:1].upper() + part[1:] for part in parts)


def display_name_for_group(group) -> str:
    """Return a clean title for a generated function node group."""
    try:
        local_name = group.get("nodeforge_local_function_name")
    except Exception:
        local_name = None
    if local_name:
        parts = [part for part in re.split(r"[_\s]+", str(local_name)) if part]
        return " ".join(part[:1].upper() + part[1:] for part in parts) or "Function"
    for key in ("nodeforge_library_name", "nodeforge_function_module"):
        try:
            value = group.get(key)
        except Exception:
            value = None
        if value:
            return display_name_for_function(str(value))
    name = getattr(group, "name", "") or "Function"
    for prefix in ("NodeForge.fn.", "NodeForge.example.", "NodeForge.local."):
        if name.startswith(prefix):
            return display_name_for_function(name[len(prefix):])
    return name


def apply_function_node_display_name(node, function_group) -> None:
    """Set the visible node title to a clean entry name instead of an internal id."""
    title = display_name_for_group(function_group)
    try:
        node.name = title
    except Exception:
        pass
    try:
        node.label = title
    except Exception:
        pass


def apply_function_group_display_name(group, function_name: str):
    """Rename legacy function groups to a clean user-facing name when safe."""
    title = display_name_for_function(function_name)
    try:
        if group.name.startswith("NodeForge.fn.") or group.name == function_name or group.name == _safe_group_name(function_name):
            group.name = title
    except Exception:
        pass
    try:
        group["nodeforge_function_display_name"] = title
    except Exception:
        pass
    return group


def _safe_group_name(name: str) -> str:
    """Create the reusable group name for a function entry."""
    return display_name_for_function(name)


def _safe_package_component(value: str) -> str:
    """Return a stable datablock-name component for package-qualified groups."""
    return re.sub(r"[^A-Za-z0-9_]+", "_", value or "package").strip("_") or "package"


def _group_name(namespace: str, name: str) -> str:
    """Return the generated GeometryNodeTree name for an unowned catalog entry."""
    if namespace == "functions":
        return _safe_group_name(name)
    if namespace == "examples":
        return f"NodeForge.example.{name}"
    if namespace == "local":
        return f"NodeForge.local.{name}"
    return f"NodeForge.{namespace}.{name}"


def _group_name_for_record(record: "LibraryEntryRecord") -> str:
    """Return the package-aware GeometryNodeTree base name for a catalog record."""
    if record.namespace == "local":
        return _group_name("local", record.name)
    if not record.package_id:
        return _group_name(record.namespace, record.name)
    package_part = _safe_package_component(record.package_id)
    return f"NodeForge.package.{package_part}.{record.namespace}.{record.name}"
def _input_sockets(node):
    """Return real, non-hidden input sockets for a group node."""
    return [s for s in node.inputs if not getattr(s, "is_output", False) and getattr(s, "enabled", True) and not getattr(s, "hide", False)]


def _output_sockets(node):
    """Return real, non-hidden output sockets for a group node."""
    return [s for s in node.outputs if getattr(s, "is_output", True) and getattr(s, "enabled", True) and not getattr(s, "hide", False)]


def _group_catalog_provenance(group) -> tuple[str, str]:
    """Return authoritative catalog namespace/name stored on a materialized group."""
    if is_authority_ineligible_group(group):
        raise CompileError("Selected node group is transaction-private or was not published")
    try:
        namespace = str(group.get("nodeforge_library_namespace") or "")
        name = str(group.get("nodeforge_library_name") or "")
    except Exception as exc:
        raise CompileError("Selected node group has no readable NodeForge library provenance") from exc
    if not namespace or not name:
        raise CompileError("Selected node group has no NodeForge library provenance")
    return namespace, name


def _find_unique_package_function_record(environment, name: str) -> LibraryEntryRecord | None:
    """Return one live package function record by name, rejecting owner ambiguity."""
    matches = [record for record in environment.package_function_records() if record.name == name]
    if not matches:
        return None
    if len(matches) > 1:
        owners = ", ".join(sorted(record.package_id for record in matches))
        raise CompileError(
            f"Ambiguous functions library entry {name!r}: {owners}. Select a package owner explicitly."
        )
    return matches[0]


def resolve_reloadable_library_entry(
    group, namespace: str | None = None, name: str | None = None,
    *, resolved_environment: ResolvedEnvironment | None = None,
) -> LibraryEntryRecord:
    """Resolve persisted ownership from the supplied compilation snapshot or UI discovery."""
    stored_namespace, stored_name = _group_catalog_provenance(group)
    namespace = stored_namespace if namespace is None else namespace
    name = stored_name if name is None else name
    if namespace != stored_namespace or name != stored_name:
        raise CompileError(
            f"Selected node group belongs to {stored_namespace}/{stored_name}, not {namespace}/{name}"
        )
    package_id = None
    if namespace == "functions":
        try:
            package_id = str(group.get("nodeforge_package_id") or "")
        except Exception as exc:
            raise CompileError("Selected package function has no readable package provenance") from exc
        if not package_id or package_id == CORE_PACKAGE_ID:
            raise CompileError("Selected package function has no canonical package owner")
    if resolved_environment is None:
        if namespace == "local":
            from ..local_sources import find_local_entry_record

            record = find_local_entry_record(name)
        else:
            from ..environment_resolution import resolve_environment

            environment = resolve_environment()
            record = (
                environment.package_function_record(package_id, name)
                if namespace == "functions"
                else environment.catalog(namespace).find(name)
            )
    elif namespace == "functions":
        record = resolved_environment.package_function_record(package_id, name)
    else:
        record = resolved_environment.catalog(namespace).find(name)
    if record is None:
        owner = f" from package {package_id!r}" if package_id else ""
        raise CompileError(
            f"Current source for {namespace} library entry {name!r}{owner} is unavailable"
        )
    return validate_reloadable_library_entry_record(group, record)


def validate_reloadable_library_entry_record(group, record: LibraryEntryRecord) -> LibraryEntryRecord:
    """Validate an exact selected record against an existing group's provenance."""
    _validate_resolved_record(record)
    stored_namespace, stored_name = _group_catalog_provenance(group)
    if record.namespace != stored_namespace or record.name != stored_name:
        raise CompileError(
            f"Selected node group belongs to {stored_namespace}/{stored_name}, not {record.namespace}/{record.name}"
        )
    if record.source_path is None:
        raise CompileError(f"{record.namespace} library entry {record.name!r} has no reloadable .nf source")

    try:
        stored_package_id = str(group.get("nodeforge_package_id") or CORE_PACKAGE_ID)
    except Exception:
        stored_package_id = CORE_PACKAGE_ID
    current_package_id = normalize_library_package_id(record.package_id)
    if stored_package_id != current_package_id:
        raise CompileError(
            f"Current source for {record.namespace} library entry {record.name!r} belongs to a different package"
        )
    return record


def update_materialized_library_entry_group(namespace: str, name: str, group, group_backend):
    """Recompile the current editable catalog source into an existing root group."""
    session = group_backend.new_source_callable_session()
    record = resolve_reloadable_library_entry(
        group, namespace, name, resolved_environment=session.resolved_environment
    )
    return update_materialized_library_entry_group_for_record(
        record, group, group_backend, source_callable_session=session
    )


def update_materialized_library_entry_group_for_record(
    record: LibraryEntryRecord, group, group_backend, *, source_callable_session=None,
):
    """Update an existing root group through one prepared semantic compilation."""
    validate_reloadable_library_entry_record(group, record)
    source = load_library_entry_source_for_record(record)
    function_id = library_function_id(record.namespace, record.package_id, record.name)
    identity = _direct_library_compilation_identity(function_id)
    session = source_callable_session or group_backend.new_source_callable_session()
    prepared = group_backend.prepare_source_compilation(
        source,
        compilation_identity=identity,
        helper_namespace=record.name,
        source_callable_session=session,
    )
    spec = LibraryFunctionUpdateSpec(
        namespace=record.namespace,
        name=record.name,
        record=record,
        prepared_compilation=prepared,
        function_id=function_id,
        source_callable_session=session,
        group=group,
        group_name=getattr(group, "name", _group_name_for_record(record)),
        write_package_metadata=_write_package_metadata,
    )
    return FunctionMaterializer(group_backend=group_backend).update_library_group(spec)


def _write_package_metadata(group, record: LibraryEntryRecord) -> None:
    """Record package ownership metadata on materialized library groups."""
    group["nodeforge_library_namespace"] = record.namespace
    group["nodeforge_library_name"] = record.name
    group["nodeforge_function_kind"] = record.kind
    group["nodeforge_package_id"] = record.package_id or CORE_PACKAGE_ID
    if record.package_id:
        group["nodeforge_package_name"] = record.package_name
        group["nodeforge_package_version"] = record.package_version


def _library_metadata_matches(group, record: LibraryEntryRecord, *, instance_key: str | None) -> bool:
    """Return True when *group* is the exact metadata owner for *record*."""
    try:
        if group.get("nodeforge_library_namespace") != record.namespace:
            return False
        if group.get("nodeforge_library_name") != record.name:
            return False
        stored_package_id = str(group.get("nodeforge_package_id") or CORE_PACKAGE_ID)
        if stored_package_id != normalize_library_package_id(record.package_id):
            return False
        stored_key = str(group.get(FUNCTION_INSTANCE_KEY_PROP) or "")
        wanted_key = str(instance_key or "")
        return stored_key == wanted_key
    except Exception:
        return False


def _find_owned_library_entry_group(record: LibraryEntryRecord, *, instance_key: str | None = None, transaction=None):
    """Find the sole live imported function group by ownership metadata."""
    matches = []
    for group in bpy.data.node_groups:
        if getattr(group, "bl_idname", None) != "GeometryNodeTree":
            continue
        if transaction is not None and hasattr(transaction, "owns_group") and transaction.owns_group(group):
            continue
        if is_authority_ineligible_group(group):
            continue
        if _library_metadata_matches(group, record, instance_key=instance_key):
            matches.append(group)
    if len(matches) > 1:
        mode = "unique" if instance_key else "shared"
        raise CompileError(f"Multiple {mode} {record.namespace} library groups match {record.name!r}")
    return matches[0] if matches else None


def _validate_library_function_id(function_id: FunctionId, record: LibraryEntryRecord) -> None:
    """Validate one upstream canonical imported identity against a resolved record."""
    if (
        function_id.kind != "LIBRARY"
        or function_id.namespace != record.namespace
        or function_id.package_id != normalize_library_package_id(record.package_id)
        or function_id.name != record.name
    ):
        raise CompileError(
            f"Internal error: reusable-call FunctionId does not match {record.namespace} library entry {record.name!r}"
        )


def _direct_library_compilation_identity(function_id: FunctionId) -> GroupCompilationIdentity:
    """Return the established direct-catalog owner before semantic preparation."""
    owner_scope = direct_library_owner_scope(
        function_id.namespace,
        function_id.package_id,
        function_id.name,
    )
    definition_owner = owner_scope if function_id.namespace == "local" else function_id.stable_key()
    return GroupCompilationIdentity(
        root_owner_id=None,
        owner_scope=owner_scope,
        definition_owner=definition_owner,
        declaration_owner=function_id.stable_key(),
    )


def materialize_prepared_library_callable(
    record: LibraryEntryRecord,
    materializer: FunctionMaterializer,
    prepared_callable,
    *,
    materialization: IRFunctionMaterialization | None,
    materialization_context: FunctionMaterializationContext,
) -> MaterializedFunctionGroup:
    """Materialize one source-call callee from its captured prepared semantic artifact."""
    _validate_resolved_record(record)
    function_id = prepared_callable.contract.function_id
    _validate_library_function_id(function_id, record)
    if record.namespace == "local":
        if materialization is not None:
            raise CompileError("Internal error: Local catalog source call cannot carry reusable materialization")
    else:
        if materialization is None or materialization.callee != function_id:
            raise CompileError("Internal error: reusable source call has inconsistent materialization identity")
    spec = LibraryFunctionMaterializationSpec(
        namespace=record.namespace,
        name=record.name,
        record=record,
        prepared_compilation=prepared_callable.group,
        function_id=function_id,
        source_callable_session=materialization_context.source_callable_session,
        materialization=materialization,
        group_name=_group_name_for_record(record),
        find_existing=_find_owned_library_entry_group,
        write_package_metadata=_write_package_metadata,
    )
    return materializer.materialize_library(spec, context=materialization_context)


def get_or_create_library_entry_group(
    namespace: str,
    name: str,
    group_backend,
    *,
    materialization: IRFunctionMaterialization | None = None,
    function_id: FunctionId | None = None,
    materialization_context: FunctionMaterializationContext | None = None,
) -> MaterializedFunctionGroup:
    """Resolve an editable catalog definition and return its materialization result."""
    if namespace == "local":
        from ..local_sources import find_local_entry_record

        record = find_local_entry_record(name)
    else:
        from ..environment_resolution import resolve_environment

        environment = resolve_environment()
        record = (
            _find_unique_package_function_record(environment, name)
            if namespace == "functions"
            else environment.catalog(namespace).find(name)
        )
    if record is None:
        raise CompileError(f"{namespace} library entry {name!r} has no editable .nf source")
    return get_or_create_library_entry_group_for_record(
        record,
        group_backend,
        materialization=materialization,
        function_id=function_id,
        materialization_context=materialization_context,
    )


def get_or_create_library_entry_group_for_record(
    record: LibraryEntryRecord,
    group_backend,
    *,
    materialization: IRFunctionMaterialization | None = None,
    function_id: FunctionId | None = None,
    materialization_context: FunctionMaterializationContext | None = None,
    prepared_callable=None,
) -> MaterializedFunctionGroup:
    """Materialize an editable source definition from prepared semantics."""
    _validate_resolved_record(record)
    namespace = record.namespace
    name = record.name
    if record.source_path is None:
        raise CompileError(f"{namespace} library entry {name!r} has no editable .nf source")

    if prepared_callable is not None:
        if materialization_context is None:
            raise CompileError("Internal error: prepared source call requires materialization context")
        return materialize_prepared_library_callable(
            record,
            group_backend,
            prepared_callable,
            materialization=materialization,
            materialization_context=materialization_context,
        )

    if materialization is not None or function_id is not None:
        raise CompileError("Internal error: source-call materialization must provide PreparedSourceCallable")
    function_id = library_function_id(namespace, record.package_id, name)
    source = load_library_entry_source_for_record(record)
    identity = _direct_library_compilation_identity(function_id)
    session = group_backend.new_source_callable_session()
    prepared = group_backend.prepare_source_compilation(
        source,
        compilation_identity=identity,
        helper_namespace=name,
        source_callable_session=session,
    )
    spec = LibraryFunctionMaterializationSpec(
        namespace=namespace,
        name=name,
        record=record,
        prepared_compilation=prepared,
        function_id=function_id,
        source_callable_session=session,
        materialization=None,
        group_name=_group_name_for_record(record),
        find_existing=_find_owned_library_entry_group,
        write_package_metadata=_write_package_metadata,
    )
    return FunctionMaterializer(group_backend=group_backend).materialize_library(
        spec, context=materialization_context
    )


def materialize_library_entry_group(namespace: str, name: str, group_backend):
    """Create/update a GeometryNodeTree for a catalog entry."""
    if namespace == "local":
        from ..local_sources import find_local_entry_record

        record = find_local_entry_record(name)
    else:
        from ..environment_resolution import resolve_environment

        environment = resolve_environment()
        record = (
            _find_unique_package_function_record(environment, name)
            if namespace == "functions"
            else environment.catalog(namespace).find(name)
        )
    if record is None:
        raise CompileError(f"Unknown {namespace} library entry: {name}")
    return materialize_library_entry_group_for_record(record, group_backend)


def materialize_library_entry_group_for_record(record: LibraryEntryRecord, group_backend):
    """Create/update a GeometryNodeTree from one exact resolved catalog record."""
    _validate_resolved_record(record)
    namespace = record.namespace
    name = record.name
    if record.module_path is not None:
        raise CompileError(
            f"{namespace} library entry {name!r} uses unsupported Extension API v1 native execution; "
            "migrate the owner to interface.py (EXTENSION_API = 2)"
        )
    if record.source_path is not None:
        materialized = get_or_create_library_entry_group_for_record(record, group_backend)
        group = materialized.group
        return apply_function_group_display_name(group, name) if namespace == "functions" else group
    raise CompileError(f"Unknown {namespace} library entry: {name}")
