"""Public compiler facade and root build orchestration."""

from .errors import CompileError
from .blender.library_groups import (
    materialize_library_entry_group_for_record,
    resolve_reloadable_library_entry,
    update_materialized_library_entry_group_for_record,
)
from .semantic.source_callable_session import SourceCallableSession
from .semantic.group import analyze_group_source
from .blender.group_assembly import _populate_group
from .blender.group_backend import BlenderGroupBackend
from .blender.group_build_request import BlenderGroupBuildRequest
from .resolved_environment import ResolvedEnvironment
from .environment_resolution import resolve_environment, _make_compilation_environment


class _ResolvedEnvironmentBoundBackend(BlenderGroupBackend):
    """Bind root-session environment selection to preparation and publication."""

    def __init__(self, *, populate_candidate, prepare_compilation, resolved_environment_for_session):
        """Bind population, preparation, and one compiler-session environment provider."""
        super().__init__(populate_candidate=populate_candidate, prepare_compilation=prepare_compilation)
        self._resolved_environment_for_session = resolved_environment_for_session

    def new_source_callable_session(self):
        """Create one root-attempt source/preparation session over the bound environment."""
        return SourceCallableSession(resolved_environment=self._resolved_environment_for_session())

    def compile_group_callback(self, source: str, name: str = "NodeForge Group", **kwargs):
        """Ensure public package builds share one source-call session across both phases."""
        if kwargs.get("source_callable_session") is None:
            kwargs["source_callable_session"] = self.new_source_callable_session()
        return super().compile_group_callback(source, name=name, **kwargs)


def _new_group_backend(resolved_environment: ResolvedEnvironment | None = None):
    """Build a root environment per session, or reuse a supplied eager snapshot."""
    def environment_for_session():
        """Reuse a supplied eager value or resolve a fresh compiler root view."""
        return resolved_environment if resolved_environment is not None else _make_compilation_environment()

    def prepare(source, *, compilation_identity, source_callable_session=None, **kwargs):
        session = source_callable_session or SourceCallableSession(resolved_environment=environment_for_session())
        if resolved_environment is not None and session.resolved_environment is not resolved_environment:
            raise CompileError("Source callable session does not match supplied resolved environment")
        return analyze_group_source(
            source,
            compilation_identity=compilation_identity,
            resolved_environment=session.resolved_environment,
            inherited_local_functions=kwargs.get("inherited_local_functions"),
            inherited_imported_library_functions=kwargs.get("inherited_imported_library_functions"),
            helper_namespace=kwargs.get("helper_namespace") or "NodeForge Group",
            source_callable_session=session,
        )

    return _ResolvedEnvironmentBoundBackend(
        populate_candidate=_populate_group,
        prepare_compilation=prepare,
        resolved_environment_for_session=environment_for_session,
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


def create_examples_source_group(source_origin: str, package_id: str = ""):
    """Materialize exactly the Examples leaf/owner selected by its UI source path."""
    environment = resolve_environment()
    selected = environment.catalog("examples")
    selected._raise_if_failed()
    from .catalog import _path_key
    origin = _path_key(source_origin)
    record = None if selected.examples_index is None else selected.examples_index.find_origin(origin)
    if (record is None or record.package_id != package_id or record.source_path is None
            or record.kind == "runtime"):
        raise CompileError(f"Current Examples .nf library source is unavailable: {source_origin}")
    return materialize_library_entry_group_for_record(record, _new_group_backend(environment))


def create_examples_mesh_compute_modifier(source_origin: str, package_id: str, owner):
    """Compile a specifically selected Examples .nf root using its one captured environment."""
    environment = _make_compilation_environment()
    selected = environment.catalog("examples")
    selected._raise_if_failed()
    from .catalog import _path_key, load_library_entry_source_for_record
    origin = _path_key(source_origin)
    record = None if selected.examples_index is None else selected.examples_index.find_origin(origin)
    if (record is None or record.package_id != package_id or record.kind != "script"
            or record.source_path is None or record.source_path.suffix != ".nf"):
        raise CompileError(f"Current Examples .nf root is unavailable: {source_origin}")
    # The selected record's physical source path must still agree with the frozen origin.
    if _path_key(record.source_path) != record.source_origin:
        raise CompileError("Selected Examples root changed identity during compilation")
    source = load_library_entry_source_for_record(record)
    return create_mesh_compute_modifier(source, owner, resolved_environment=environment)


def create_local_source_group(source_origin: str):
    """Materialize the exact Local source selected by its canonical browser path."""
    environment = resolve_environment()
    selected = environment.catalog("local")
    selected._raise_if_failed()
    import os
    origin = os.path.normcase(os.path.normpath(source_origin))
    record = None if selected.local_index is None else selected.local_index.find_origin(origin)
    if record is None:
        raise CompileError(f"Current Local source is unavailable: {source_origin}")
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


def create_mesh_compute_modifier(source: str, owner, name: str = "NodeForge Mesh Compute", *,
                                 resolved_environment=None):
    """Compile one trusted Local runtime root onto a private first GN modifier.

    All fallible Blender installation and callback preparation occurs before
    `BlenderGroupBuildTransaction.commit()`. A failed attempt removes the
    provisional group, modifier and private prefix scene as one unit.
    """
    from .blender.group_backend import BlenderGroupBuildTransaction
    from .blender.mesh_compute_runtime import (
        prepare_modifier_binding, release_modifier_binding, commit_modifier_binding,
    )

    if owner is None or getattr(owner, "type", None) != "MESH":
        raise CompileError("Mesh Compute requires a selected Mesh Object")
    if len(owner.modifiers):
        raise CompileError("Mesh Compute requires a Mesh Object without previous modifiers")
    backend = _new_group_backend(resolved_environment)
    prepared, session = _prepare_root_build(backend, source, name=name)
    if not any(key and key[0] in {"local-runtime", "examples-runtime"} for key, _ in prepared.extension_dependencies):
        raise CompileError("Compile to Modifier requires an imported Mesh Compute function")
    build_tx = BlenderGroupBuildTransaction()

    def finalize(group):
        """Complete the modifier/runtime candidate and register exact rollback."""
        candidates = [resource_tx for candidate, resource_tx in build_tx.created_groups
                      if candidate is group and resource_tx.mesh_compute_stage is not None]
        if len(candidates) != 1 or owner.type != "MESH" or len(owner.modifiers):
            raise CompileError("Mesh Compute target or generated stage changed before finalization")
        stage = candidates[0].mesh_compute_stage
        modifier = owner.modifiers.new(name, "NODES")
        binding = None
        try:
            modifier.node_group = group
            binding = prepare_modifier_binding(owner, modifier, group, stage)
        except BaseException:
            if binding is not None:
                release_modifier_binding(binding)
            if any(current.as_pointer() == modifier.as_pointer() for current in owner.modifiers):
                owner.modifiers.remove(modifier)
            raise

        def revert():
            """Restore the exact precompile modifier state before group deletion."""
            if any(current.as_pointer() == modifier.as_pointer() for current in owner.modifiers):
                owner.modifiers.remove(modifier)
            release_modifier_binding(binding)

        build_tx.register_external_cutover(rollback=revert, commit=lambda: commit_modifier_binding(binding))

    request = BlenderGroupBuildRequest(
        prepared_compilation=prepared,
        name=name,
        source_callable_session=session,
        helper_namespace=name,
        function_group_transaction=build_tx,
        mesh_compute_modifier_target=owner,
    )
    try:
        group = backend.create_or_update(request, finalize_before_commit=finalize)
        build_tx.commit()
        return group
    except BaseException:
        build_tx.rollback()
        raise


__all__ = [
    "CompileError",
    "create_expression_group",
    "create_mesh_compute_modifier",
    "update_mesh_compute_modifier",
    "update_expression_group",
    "update_library_catalog_group",
    "create_library_catalog_group",
    "create_package_function_group",
]


def update_mesh_compute_modifier(source: str, owner):
    """Reload a private Mesh Compute root by swapping a fresh, prepared group.

    The previous root's nodes, published proxy Mesh and callback remain untouched
    through compilation. The existing group transaction coordinates the reversible
    modifier/FCurve cutover with binding activation at its single commit PONR.
    """
    import warnings
    import bpy
    from .blender.group_backend import BlenderGroupBuildTransaction
    from .blender import generated_resources
    from .blender.mesh_compute_runtime import (
        suspend_modifier_binding, suspend_saved_runtime_binding,
        resume_modifier_binding, saved_runtime_status,
        prepare_modifier_binding, release_modifier_binding, commit_modifier_binding,
        prepare_modifier_input_migration,
    )

    # An ordinary Reload retains the existing callback until commit. After a
    # .blend reopen there is no trusted callback; the validated saved root is
    # instead held in MISSING_RUNTIME with an already empty published Mesh.
    if saved_runtime_status(owner) == 'MISSING_RUNTIME':
        previous = None
        modifier, old_group = suspend_saved_runtime_binding(owner)
    else:
        previous = suspend_modifier_binding(owner)
        modifier, old_group = previous.modifier, previous.group
    build_tx = BlenderGroupBuildTransaction()
    captured_old = ()
    old_manifest = None
    try:
        if (len(owner.modifiers) != 1 or owner.modifiers[0].as_pointer() != modifier.as_pointer() or
                modifier.node_group.as_pointer() != old_group.as_pointer()):
            raise CompileError("Reload Compute requires the current trusted private first modifier")
        # A user-held root, copied runtime group or foreign GroupNode referencing
        # the old root is not eligible for exclusive replacement/retirement.
        owners = [(obj, m) for obj in bpy.data.objects for m in obj.modifiers
                  if m.type == "NODES" and m.node_group is not None and
                  m.node_group.as_pointer() == old_group.as_pointer()]
        external_nodes = [node for group in bpy.data.node_groups for node in group.nodes
                          if getattr(node, "node_tree", None) is not None and
                          node.node_tree.as_pointer() == old_group.as_pointer()]
        if (len(owners) != 1 or owners[0][0].as_pointer() != owner.as_pointer() or
                owners[0][1].as_pointer() != modifier.as_pointer() or
                external_nodes or old_group.use_fake_user or old_group.users != 1):
            raise CompileError("Reload Compute requires one exclusively owned root group")
        old_manifest = generated_resources.read_group_manifest(old_group)
        captured_old = generated_resources.capture_owned_resource_ids(old_group, old_manifest)
        backend = _new_group_backend()
        # Keep the durable root owner ID but build a *new* provisional physical
        # Tree; do not mutate the authoritative group or its actual mesh output.
        session = backend.new_source_callable_session()
        identity = backend.resolve_root_compilation_identity(old_group)
        prepared = backend.prepare_source_compilation(
            source, compilation_identity=identity, helper_namespace=old_group.name,
            source_callable_session=session,
        )
        if not any(key and key[0] in {"local-runtime", "examples-runtime"} for key, _ in prepared.extension_dependencies):
            raise CompileError("Reload Compute requires an imported Mesh Compute function")

        def finalize(new_group):
            """Migrate and validate all fallible inputs before runtime commit."""
            if (modifier.node_group is None or modifier.node_group.as_pointer() != old_group.as_pointer() or
                    owner.modifiers[0].as_pointer() != modifier.as_pointer()):
                raise CompileError("Mesh Compute modifier was replaced during Reload")
            stages = [tx.mesh_compute_stage for group, tx in build_tx.created_groups
                      if group is new_group and tx.mesh_compute_stage is not None]
            if len(stages) != 1:
                raise CompileError("Reload Compute requires one candidate runtime stage")
            journal = prepare_modifier_input_migration(owner, modifier, old_group, new_group)
            candidate = None
            try:
                journal.install()
                candidate = prepare_modifier_binding(owner, modifier, new_group, stages[0])
            except BaseException:
                if candidate is not None:
                    release_modifier_binding(candidate)
                journal.rollback()
                raise

            def revert():
                """Return to old modifier/FCurve values before deleting candidates."""
                journal.rollback()
                release_modifier_binding(candidate)

            build_tx.register_external_cutover(
                rollback=revert, commit=lambda: commit_modifier_binding(candidate),
            )

        request = BlenderGroupBuildRequest(
            prepared_compilation=prepared, name=old_group.name,
            source_callable_session=session, helper_namespace=old_group.name,
            function_group_transaction=build_tx, mesh_compute_modifier_target=owner,
            mesh_compute_replaced_group=old_group,
        )
        result = backend.create_or_update(request, finalize_before_commit=finalize)
        build_tx.commit()
    except BaseException:
        try:
            build_tx.rollback()
        finally:
            resume_modifier_binding(modifier)
        raise

    # Commit has completed; failures from resource retirement are warnings, not
    # precommit rollbacks. This stage cannot touch the active new runtime group.
    try:
        if previous is not None:
            release_modifier_binding(previous)
        if any(group is old_group for group in bpy.data.node_groups):
            if old_group.users == 0:
                bpy.data.node_groups.remove(old_group)
            else:
                warnings.warn("Mesh Compute retained old root with external users", RuntimeWarning)
        if old_manifest is not None and not any(group is old_group for group in bpy.data.node_groups):
            new_manifest = generated_resources.read_group_manifest(result)
            generated_resources.cleanup_previous_after_commit(
                old_manifest, new_manifest or generated_resources.build_manifest(old_manifest["owner_group_uuid"], []),
                captured_ids=captured_old,
            )
    except Exception as exc:
        warnings.warn(f"Mesh Compute Reload committed; old resources retained: {exc}", RuntimeWarning)
    finally:
        resume_modifier_binding(modifier)
    return result
