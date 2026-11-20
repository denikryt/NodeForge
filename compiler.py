"""Public compiler facade and root build orchestration."""

from dataclasses import dataclass

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
        """Reject the removed raw-source backend entry point."""
        raise CompileError(
            'Raw-source backend compilation is unsupported; '
            'supply prepared typed IR via BlenderGroupBuildRequest'
        )


def _new_group_backend(resolved_environment: ResolvedEnvironment | None = None):
    """Build a root environment per session, or reuse a supplied eager snapshot."""
    def environment_for_session():
        """Reuse a supplied eager value or resolve a fresh compiler root view."""
        return resolved_environment if resolved_environment is not None else _make_compilation_environment()

    def prepare(source, *, compilation_identity, source_callable_session=None, **kwargs):
        """Raw-source backend compilation no longer has a supported root contract."""
        raise CompileError(
            'Raw-source backend preparation is unsupported; '
            'compile an individual def to typed IR before Blender publication'
        )

    return _ResolvedEnvironmentBoundBackend(
        populate_candidate=_populate_group,
        prepare_compilation=prepare,
        resolved_environment_for_session=environment_for_session,
    )


def _prepare_mesh_compute_root(backend, source: str, *, name: str, existing_group=None):
    """Prepare one typed modifier entry as a physical ROOT, never a nested group.

    An explicitly installed Mesh Compute modifier owns its own GeometryNodeTree.
    Unlike an ordinary function Node Group it can therefore host root-only
    Extension API callbacks, while every function it calls remains NESTED.
    """
    from .compiler_identities import ModuleId, module_definition_function_id
    from .semantic.source_modules import (
        parse_source_module, resolve_source_module, resolve_source_call_graph,
    )
    from .semantic.function_signatures import compile_function_signature
    from .semantic.source_callables import serialize_local_signature
    from .semantic.runtime_capabilities import RuntimeRole
    from .nf_types import NFType

    identity = backend.resolve_root_compilation_identity(existing_group)
    module_id = ModuleId("ROOT_TEXT", f"mesh-compute:{identity.root_owner_id}")
    snapshot = parse_source_module(source, module_id)
    resolved = resolve_source_module(snapshot)
    if resolved.has_executable_statements:
        raise CompileError(
            "Mesh Compute modifier requires a typed function entry; "
            "executable top-level statements and old input_*()/output() are unsupported"
        )
    graph = resolve_source_call_graph(resolved)
    if len(graph.entries) != 1:
        raise CompileError(
            f"Mesh Compute modifier requires exactly one entry function; found {len(graph.entries)}"
        )
    definition = graph.entries[0]
    fn = snapshot.definitions[definition.name]
    session = backend.new_source_callable_session()
    environment, compile_time_values = session.callable_environment_for_module(snapshot)
    signature = compile_function_signature(
        fn, owner_scope=identity.owner_scope,
        declaration_owner=identity.declaration_owner,
        compile_time_values=compile_time_values,
    )
    types = {item.target_name: item.typ for item in signature.declarations}
    function_id = module_definition_function_id(
        definition, serialize_local_signature(tuple(item.target_name for item in signature.declarations), types),
    )
    prepared = session.prepare_definition(
        function_id=function_id, identity=identity, fn=fn,
        callable_environment=environment, compile_time_values=compile_time_values,
        source_module=snapshot, helper_namespace=name,
        runtime_role=RuntimeRole.ROOT,
    ).group
    inputs, outputs = prepared.interface.inputs, prepared.interface.outputs
    if _requires_mesh_compute(prepared) and (
            not inputs or inputs[0].typ is not NFType.GEOMETRY
            or inputs[0].has_default
            or any(item.typ is NFType.GEOMETRY for item in inputs[1:])
            or len(outputs) != 1 or outputs[0].typ is not NFType.GEOMETRY):
        raise CompileError(
            "Mesh Compute modifier entry must accept a required first Geometry parameter "
            "and return exactly one Geometry value"
        )
    return prepared, session


def create_expression_group(source: str, name: str = "NodeForge Group"):
    """Materialize exactly one standalone unified `.nf` definition.

    This API no longer accepts a generated-root script or implicit root I/O.
    Use ``compose_source_initial`` for executable top-level composition.
    """
    from uuid import uuid4
    from .compiler_identities import ModuleId
    from .semantic.source_modules import parse_source_module, resolve_source_module

    snapshot = parse_source_module(source, ModuleId("ROOT_TEXT", f"standalone:{uuid4().hex}"))
    resolved = resolve_source_module(snapshot)
    if resolved.has_executable_statements or len(snapshot.definitions) != 1:
        raise CompileError(
            "Creating a standalone Function Node Group requires exactly one def "
            "and no executable top-level statements; use direct composition otherwise"
        )
    definition = snapshot.definition_ref(next(iter(snapshot.definitions)))
    group = _materialize_standalone_module_definition(snapshot, definition, _new_group_backend())
    group.name = name
    return group


def _prepare_direct_source(source: str, target, *, resolved_environment=None,
                           module_context: str | None = None):
    """Prepare typed root statements once, without materializing Blender nodes."""
    from .compiler_identities import GroupCompilationIdentity, ModuleId
    from .function_instances import function_group_owner_scope
    from .semantic.source_modules import parse_source_module
    from .blender.direct_composition import validate_prepared_direct

    if getattr(target, "bl_idname", None) != "GeometryNodeTree":
        raise CompileError("Direct composition requires an existing base GeometryNodeTree")
    backend = _new_group_backend(resolved_environment)
    session = backend.new_source_callable_session()
    module_id = ModuleId("ROOT_TEXT", module_context if isinstance(module_context, str) and module_context.strip() else f"base-tree:{target.name}")
    snapshot = parse_source_module(source, module_id)
    if snapshot.definitions and (
        not isinstance(module_context, str)
        or not module_context.strip()
        or module_context != module_context.strip()
    ):
        raise CompileError("Source definitions require a stable module_context")
    owner_scope = function_group_owner_scope("COMPOSITION", module_id.stable_key())
    identity = GroupCompilationIdentity(None, owner_scope, owner_scope, owner_scope)
    prepared = analyze_group_source(
        source, compilation_identity=identity,
        resolved_environment=session.resolved_environment,
        source_callable_session=session, source_module=snapshot,
        direct_composition=True,
    )
    validate_prepared_direct(prepared, target)
    return prepared, backend, session, snapshot


@dataclass(frozen=True)
class RootCompileResult:
    """One unified root compilation outcome and its boundary-link losses."""

    target: object
    outcome: str
    skipped_boundary_links: int = 0


def compile_source_in_tree(source: str, target, *, module_context: str | None = None,
                           resolved_environment=None) -> RootCompileResult:
    """Replace root nodes for physical statements or publish definition groups only.

    The semantic body is the sole mode selector; no source text or historical
    root state decides which physical graph owns this invocation.
    """
    from .blender.root_update import validate_base_target, reject_node_local_animation, replace_prepared_root
    from .blender.direct_composition import materialize_definition_entries
    from .blender.function_materializer import FunctionMaterializationContext, FunctionMaterializer
    from .blender.group_backend import BlenderGroupBuildTransaction
    from .function_instances import FunctionCompilationTrace

    validate_base_target(target)
    if not isinstance(source, str) or not source.strip():
        raise CompileError('Compile Script requires a nonblank selected Text source')
    prepared, backend, session, snapshot = _prepare_direct_source(
        source, target, resolved_environment=resolved_environment,
        module_context=module_context,
    )
    if prepared.body.statements:
        reject_node_local_animation(target)
        updated = replace_prepared_root(
            prepared, target, backend, session, source_module=snapshot,
        )
        return RootCompileResult(target, 'ROOT_REPLACEMENT', updated.skipped_boundary_links)

    transaction = BlenderGroupBuildTransaction()
    context = FunctionMaterializationContext(
        function_group_cache={}, function_group_transaction=transaction,
        function_compilation_trace=FunctionCompilationTrace(),
        source_callable_session=session,
    )
    try:
        materialize_definition_entries(
            prepared, FunctionMaterializer(group_backend=backend), context, snapshot,
        )
        transaction.commit()
    except Exception:
        transaction.rollback()
        raise
    return RootCompileResult(target, 'GROUPS_ONLY', 0)


def compose_source_initial(source: str, target, *, resolved_environment=None,
                           module_context: str | None = None):
    """Append one typed `.nf` composition directly to an existing base tree."""
    from .blender.direct_composition import compose_prepared_initial

    prepared, backend, session, snapshot = _prepare_direct_source(
        source, target, resolved_environment=resolved_environment,
        module_context=module_context,
    )
    return compose_prepared_initial(prepared, target, backend, session, source_module=snapshot)


def update_source_in_tree(source: str, target, *, module_context: str | None = None):
    """Replace ordinary nodes of an eligible base GeometryNodeTree in place.

    Returns ``RootUpdateResult`` with the number of skipped old boundary links.
    A ``RootUpdateError.may_have_mutated`` flag distinguishes errors that require
    an Undo step from preflight or fully cleaned staging failures.
    """
    from .blender.root_update import (
        validate_base_target, reject_node_local_animation, replace_prepared_root,
    )

    validate_base_target(target)
    if not isinstance(source, str) or not source.strip():
        raise CompileError('Update Script requires a nonblank selected Text source')
    reject_node_local_animation(target)
    prepared, backend, session, snapshot = _prepare_direct_source(
        source, target, module_context=module_context,
    )
    return replace_prepared_root(
        prepared, target, backend, session, source_module=snapshot,
    )


def _requires_mesh_compute(prepared) -> bool:
    """Classify used runtime dependencies after semantic resolution, not import text."""
    return any(owner and owner[0] in {"local-runtime", "examples-runtime"}
               for owner, _ in prepared.extension_dependencies)


def compile_script_auto(source: str, owner=None, name: str = "NodeForge Group"):
    """Compile top-level `.nf` only into the explicitly supplied base node tree.

    ``name`` is retained for callers, not used to create a generated root.
    Mesh Object/modifier installation belongs to the separate Mesh Compute
    runtime contract and is not an implicit fallback for this operation.
    """
    if getattr(owner, 'bl_idname', None) != 'GeometryNodeTree':
        raise CompileError(
            'Unified Compile Script requires an existing base GeometryNodeTree; '
            'generated-root and modifier compilation are unsupported'
        )
    compose_source_initial(source, owner)
    return owner, False


def _materialize_standalone_module_definition(snapshot, definition, backend):
    """Build one selected `.nf` definition through the shared nested-group owner.

    This is a user-requested Add, not a module import or initial base-tree
    composition. Executable top-level source statements are never evaluated.
    """
    from .compiler_identities import GroupCompilationIdentity, module_definition_function_id
    from .function_instances import function_materialization_owner_scope
    from .semantic.function_signatures import compile_function_signature
    from .semantic.source_callables import serialize_local_signature
    from .semantic.ir import IRFunctionMaterialization, IRFunctionMaterializationMode
    from .blender.function_materializer import (
        FunctionMaterializer, FunctionMaterializationContext,
    )
    from .blender.group_backend import BlenderGroupBuildTransaction
    from .function_instances import FunctionCompilationTrace

    fn = snapshot.definitions[definition.name]
    session = backend.new_source_callable_session()
    environment, compile_time = session.callable_environment_for_module(snapshot)
    signature = compile_function_signature(
        fn, owner_scope=definition.stable_key(),
        declaration_owner=definition.stable_key(),
        compile_time_values=compile_time,
    )
    types = {item.target_name: item.typ for item in signature.declarations}
    function_id = module_definition_function_id(
        definition, serialize_local_signature(tuple(item.target_name for item in signature.declarations), types),
    )
    materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    owner_scope = function_materialization_owner_scope(materialization)
    identity = GroupCompilationIdentity(None, owner_scope, definition.stable_key(), function_id.stable_key())
    prepared = session.prepare_definition(
        function_id=function_id, identity=identity, fn=fn,
        callable_environment=environment, compile_time_values=compile_time,
        source_module=snapshot, helper_namespace=fn.name,
    )
    tx = BlenderGroupBuildTransaction()
    context = FunctionMaterializationContext(
        function_group_cache={}, function_group_transaction=tx,
        function_compilation_trace=FunctionCompilationTrace(),
        source_callable_session=session,
    )
    try:
        materialized = FunctionMaterializer(group_backend=backend).materialize_module(
            prepared, materialization, context,
        )
        tx.commit()
    except Exception:
        tx.rollback()
        raise
    return materialized.group


def create_selected_library_group_auto(namespace: str, source_origin: str, owner=None,
                                       package_id: str = "", *, editor_available: bool = True,
                                       resolved_environment=None):
    """Compile the exact selected Local/Examples root without guessing its owner.

    Ordinary catalog entries keep their existing reusable materialization path;
    an explicitly used Python callback installs the root modifier instead.
    """
    if namespace not in {"local", "examples"}:
        raise CompileError("Automatic library routing supports Local and Examples only")
    from .catalog import _path_key, load_library_entry_source_for_record
    environment = resolved_environment if resolved_environment is not None else _make_compilation_environment()
    catalog = environment.catalog(namespace)
    catalog._raise_if_failed()
    origin = _path_key(source_origin)
    index = catalog.local_index if namespace == "local" else catalog.examples_index
    record = None if index is None else index.find_origin(origin)
    if record is None or (namespace == "examples" and record.package_id != package_id):
        raise CompileError(f"Current {namespace} source is unavailable: {source_origin}")
    if (record.source_path is not None and record.source_path.suffix == ".nf"
            and record.kind == "script"):
        if _path_key(record.source_path) != record.source_origin:
            raise CompileError(f"Selected {namespace} module changed identity during compilation")
        from .semantic.source_modules import parse_source_module, resolve_source_module, resolve_source_call_graph
        from .compiler_identities import ModuleId

        source = load_library_entry_source_for_record(record)
        module_id = ModuleId(namespace.upper(), record.source_origin)
        snapshot = parse_source_module(source, module_id)
        graph = resolve_source_call_graph(resolve_source_module(snapshot))
        if len(graph.entries) != 1:
            raise CompileError(
                f"Selected {namespace} module has {len(graph.entries)} entry definitions; "
                "Add a specific function from a script instead of guessing its entry"
            )
        backend = _new_group_backend(environment)
        session = backend.new_source_callable_session()
        bindings, _compile_time = session.callable_environment_for_module(snapshot)
        if any(getattr(getattr(binding, "record", None), "kind", None) == "runtime"
               for binding in bindings.imported_functions.values()):
            prepared, runtime_session = _prepare_mesh_compute_root(
                backend, source, name=record.name,
            )
            if _requires_mesh_compute(prepared):
                if getattr(owner, "type", None) != "MESH":
                    raise CompileError("Mesh Compute requires a selected Mesh Object")
                return _install_prepared_mesh_compute_modifier(
                    backend, prepared, runtime_session, owner, name=record.name,
                ), True
        if not editor_available:
            raise CompileError("Open a Geometry Nodes editor to add a module function")
        return _materialize_standalone_module_definition(
            snapshot, graph.entries[0], backend,
        ), False
    if not editor_available:
        raise CompileError("Open a Geometry Nodes editor to add a library node group")
    return materialize_library_entry_group_for_record(record, _new_group_backend(environment)), False


def create_library_catalog_group(namespace: str, name: str):
    """Create a name-unique catalog group; Functions callers should pass owner identity."""
    environment = resolve_environment()
    if namespace == "functions":
        matches = [(package_id, export) for package_id, owner in environment.package_namespaces.items()
                   for export in owner.canonical_records()
                   if export.qualified_name == name or (
                       "." not in name and export.name == name
                   )]
        if len(matches) > 1:
            owners = ", ".join(sorted(f"{package_id}/{item.qualified_name}" for package_id, item in matches))
            raise CompileError(
                f"Ambiguous functions library entry {name!r}: {owners}. Select a package owner explicitly."
            )
        if matches:
            return create_package_function_group(matches[0][0], matches[0][1].qualified_name)
        record = None
    else:
        record = environment.catalog(namespace).find(name)
    if record is None:
        raise CompileError(f"Unknown {namespace} library entry: {name}")
    if (namespace in {"local", "examples"} and record.kind == "script"
            and record.source_path is not None and record.source_path.suffix == ".nf"):
        group, _ = create_selected_library_group_auto(
            namespace, record.source_origin, package_id=record.package_id or "",
            resolved_environment=environment,
        )
        return group
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
        raise CompileError(f"Current Examples source is unavailable: {source_origin}")
    if record.kind == "script" and record.source_path.suffix == ".nf":
        group, _ = create_selected_library_group_auto(
            "examples", record.source_origin, package_id=package_id,
            resolved_environment=environment,
        )
        return group
    return materialize_library_entry_group_for_record(record, _new_group_backend(environment))


def create_examples_mesh_compute_modifier(source_origin: str, package_id: str, owner):
    """Compile an exact Examples .nf source as a root Mesh Compute modifier."""
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
    if (record.kind == "script" and record.source_path is not None
            and record.source_path.suffix == ".nf"):
        group, _ = create_selected_library_group_auto(
            "local", record.source_origin, resolved_environment=environment,
        )
        return group
    return materialize_library_entry_group_for_record(record, _new_group_backend(environment))


def create_package_function_group(package_id: str, name: str):
    """Add one exact package function, using its frozen `.nf` definition when present."""
    environment = resolve_environment()
    namespace = environment.package_by_id(package_id)
    export = None if namespace is None else (
        namespace.find_path(name) if "." in name else namespace.find(name)
    )
    if export is None or export.record is None:
        raise CompileError(f"Unknown package function {package_id}/{name}")
    if export.definition is None:
        raise CompileError(
            f"Native Extension API v2 callable {package_id}/{name} is callable from "
            "source but cannot be added as a standalone Library group; "
            "declare a source-backed .nf wrapper"
        )
    backend = _new_group_backend(environment)
    return _materialize_standalone_module_definition(
        export.source_snapshot, export.definition, backend,
    )


def ensure_source_text_context(text) -> str:
    """Return one save/reopen-stable live Text origin for new source definitions.

    The Text ID owns the binding, not the user-managed base node tree. Copied
    Text IDs inheriting the same property are ambiguous until explicitly fixed.
    """
    import bpy
    import uuid
    from .compiler_identities import TEXT_MODULE_CONTEXT_PROP

    if text is None or not any(item is text for item in bpy.data.texts):
        raise CompileError('A live Blender Text is required for source binding')
    identity = str(text.get(TEXT_MODULE_CONTEXT_PROP) or '')
    if not identity:
        identity = 'text:' + uuid.uuid4().hex
        text[TEXT_MODULE_CONTEXT_PROP] = identity
    if not identity.startswith('text:') or sum(
        str(candidate.get(TEXT_MODULE_CONTEXT_PROP) or '') == identity
        for candidate in bpy.data.texts
    ) != 1:
        raise CompileError('Source Text identity is duplicated or invalid')
    return identity


def _reload_selected_module_definition(
    group, backend, *, explicit_source=None, module_context=None,
):
    """Reload an exact MODULE_DEF specialization into its selected datablock."""
    from .compiler_identities import (
        GroupCompilationIdentity, parse_module_definition_function_id,
    )
    from .semantic.source_callables import deserialize_module_parameter_signature
    from .semantic.function_signatures import compile_function_signature
    from .semantic.ir import IRFunctionMaterialization, IRFunctionMaterializationMode
    from .function_instances import (
        FUNCTION_DEFINITION_OWNER_PROP, FUNCTION_INSTANCE_KEY_PROP,
        FunctionCompilationTrace, module_definition_owner_scope,
    )
    from .blender.group_authority import find_unique_authoritative_group
    from .blender.function_materializer import (
        FunctionMaterializer, FunctionMaterializationContext,
    )
    from .blender.group_backend import BlenderGroupBuildTransaction

    if getattr(group, 'bl_idname', None) != 'GeometryNodeTree':
        raise CompileError('Select an existing GeometryNodeTree function')
    stored_definition = str(group.get('nodeforge_module_definition') or '')
    function_id = parse_module_definition_function_id(stored_definition)
    instance_key = str(group.get(FUNCTION_INSTANCE_KEY_PROP) or '')
    if (str(group.get(FUNCTION_DEFINITION_OWNER_PROP) or '') != stored_definition
            or group.get('nodeforge_module_origin') != function_id.module.stable_key()
            or group.get('nodeforge_module_name') != function_id.name
            or (instance_key and (len(instance_key) != 32
                                  or any(ch not in '0123456789abcdef' for ch in instance_key)))):
        raise CompileError('Selected MODULE_DEF metadata is incomplete, detached or inconsistent')
    # This lookup is the authoritative pre-source boundary, including user ID copies.
    find_unique_authoritative_group(
        lambda candidate: (
            candidate.get(FUNCTION_DEFINITION_OWNER_PROP) == stored_definition
            and str(candidate.get(FUNCTION_INSTANCE_KEY_PROP) or '') == instance_key
        ),
        description=f'MODULE_DEF {function_id.name} / {instance_key or "SHARED"}',
        selected=group,
    )

    session = backend.new_source_callable_session()
    snapshot = session.snapshot_for_selected_definition(
        function_id, explicit_source=explicit_source, module_context=module_context,
    )
    fn = snapshot.definitions[function_id.name]
    signature_items = deserialize_module_parameter_signature(function_id.signature)
    types = dict(signature_items)
    environment, compile_time = session.callable_environment_for_module(snapshot)
    owner_scope = module_definition_owner_scope(function_id, instance_key=instance_key)
    identity = GroupCompilationIdentity(
        None, owner_scope, snapshot.definition_ref(function_id.name).stable_key(),
        function_id.stable_key(),
    )
    verified = compile_function_signature(
        fn, owner_scope=owner_scope, declaration_owner=function_id.stable_key(),
        compile_time_values=compile_time,
    )
    if tuple(item.target_name for item in verified.declarations) != tuple(name for name, _ in signature_items):
        raise CompileError('Selected module function signature is incompatible with saved specialization')
    if any(item.typ is not types[item.target_name] for item in verified.declarations):
        raise CompileError('Selected module function types are incompatible with saved specialization')
    prepared = session.prepare_definition(
        function_id=function_id, identity=identity, fn=fn,
        callable_environment=environment,
        compile_time_values=compile_time, source_module=snapshot,
        helper_namespace=fn.name,
    )
    transaction = BlenderGroupBuildTransaction()
    context = FunctionMaterializationContext(
        function_group_cache={}, function_group_transaction=transaction,
        function_compilation_trace=FunctionCompilationTrace(),
        source_callable_session=session,
    )
    materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
    try:
        result = FunctionMaterializer(group_backend=backend).materialize_module(
            prepared, materialization, context,
            selected_group=group, selected_instance_key=instance_key,
        )
        if result.group is not group or result.owner_scope != owner_scope:
            raise CompileError('Selected module Reload changed physical group ownership')
        transaction.commit()
    except Exception:
        transaction.rollback()
        raise
    return group


def update_library_catalog_group(group, namespace: str | None = None, name: str | None = None,
                                 *, source: str | None = None, module_context: str | None = None):
    """Reload the exact selected source function, retaining its physical identity."""
    environment = resolve_environment()
    if str(group.get('nodeforge_module_definition') or ''):
        if namespace is not None or name is not None:
            raise CompileError('MODULE_DEF Reload uses its canonical source definition, not a catalog name')
        return _reload_selected_module_definition(
            group, _new_group_backend(environment),
            explicit_source=source, module_context=module_context,
        )
    if source is not None or module_context is not None:
        raise CompileError('Explicit source Reload is supported only for MODULE_DEF groups')
    record = resolve_reloadable_library_entry(
        group, namespace, name, resolved_environment=environment
    )
    return update_materialized_library_entry_group_for_record(
        record, group, _new_group_backend(environment)
    )


def update_expression_group(group, source: str):
    """Update a selected standalone Function Node Group from unified source.

    Updating generated-root scripts is deliberately unsupported. Canonical
    module definitions instead use the same selected in-place Reload boundary.
    """
    if not str(group.get('nodeforge_module_definition') or ''):
        raise CompileError(
            'Update requires a canonical MODULE_DEF Function Node Group; '
            'legacy generated-root scripts are unsupported'
        )
    from .compiler_identities import parse_module_definition_function_id
    function_id = parse_module_definition_function_id(group['nodeforge_module_definition'])
    if function_id.module.kind != 'ROOT_TEXT':
        raise CompileError('Use source Reload for Local, Examples and package definitions')
    return update_library_catalog_group(
        group, source=source, module_context=function_id.module.origin,
    )


def _blank_mesh_compute_modifier(owner):
    """Return an unused GN modifier only when its graph is Blender's default passthrough.

    Never edit the existing node group. Replacing its modifier reference is
    reversible, and other users of the original group remain unaffected.
    """
    if not owner.modifiers:
        return None
    if len(owner.modifiers) != 1:
        raise CompileError("Mesh Compute requires a Mesh Object without previous modifiers or with one empty Geometry Nodes modifier")
    modifier = owner.modifiers[0]
    if modifier.type != 'NODES':
        raise CompileError("Mesh Compute requires a Mesh Object without previous modifiers or with one empty Geometry Nodes modifier")
    group = modifier.node_group
    if group is None:
        return modifier
    nodes = tuple(group.nodes)
    sockets = tuple(group.interface.items_tree)
    if (group.bl_idname != 'GeometryNodeTree' or group.keys() or
            len(nodes) != 2 or
            {node.bl_idname for node in nodes} != {'NodeGroupInput', 'NodeGroupOutput'} or
            len(sockets) != 2 or
            {(item.item_type, item.in_out, item.name, item.socket_type) for item in sockets} != {
                ('SOCKET', 'INPUT', 'Geometry', 'NodeSocketGeometry'),
                ('SOCKET', 'OUTPUT', 'Geometry', 'NodeSocketGeometry'),
            } or len(group.links) != 1):
        raise CompileError("Mesh Compute requires a Mesh Object without previous modifiers or with one empty Geometry Nodes modifier")
    link = group.links[0]
    if (link.from_node.bl_idname != 'NodeGroupInput' or
            link.to_node.bl_idname != 'NodeGroupOutput' or
            link.from_socket.name != 'Geometry' or link.to_socket.name != 'Geometry'):
        raise CompileError("Mesh Compute requires a Mesh Object without previous modifiers or with one empty Geometry Nodes modifier")
    return modifier


def create_mesh_compute_modifier(source: str, owner, name: str = "NodeForge Mesh Compute", *,
                                 resolved_environment=None):
    """Compile a trusted root onto a private modifier with transactional rollback."""
    if owner is None or getattr(owner, "type", None) != "MESH":
        raise CompileError("Mesh Compute requires a selected Mesh Object")
    _blank_mesh_compute_modifier(owner)
    backend = _new_group_backend(resolved_environment)
    prepared, session = _prepare_mesh_compute_root(backend, source, name=name)
    if not _requires_mesh_compute(prepared):
        raise CompileError("Mesh Compute modifier requires an imported Mesh Compute function")
    return _install_prepared_mesh_compute_modifier(backend, prepared, session, owner, name=name)


def _install_prepared_mesh_compute_modifier(backend, prepared, session, owner, *, name: str):
    """Install an already-prepared runtime root using the existing atomic cutover."""
    from .blender.group_backend import BlenderGroupBuildTransaction
    from .blender.mesh_compute_runtime import (
        prepare_modifier_binding, release_modifier_binding, commit_modifier_binding,
    )
    if owner is None or getattr(owner, "type", None) != "MESH":
        raise CompileError("Mesh Compute requires a selected Mesh Object")
    target_modifier = _blank_mesh_compute_modifier(owner)
    if not _requires_mesh_compute(prepared):
        raise CompileError("Mesh Compute modifier requires an imported runtime function")
    build_tx = BlenderGroupBuildTransaction()

    def finalize(group):
        """Complete the modifier/runtime candidate and register exact rollback."""
        candidates = [resource_tx for candidate, resource_tx in build_tx.created_groups
                      if candidate is group and resource_tx.mesh_compute_stage is not None]
        if len(candidates) != 1 or owner.type != "MESH":
            raise CompileError("Mesh Compute target or generated stage changed before finalization")
        # Revalidate after compilation: a user may have edited the target meanwhile.
        current = _blank_mesh_compute_modifier(owner)
        if (target_modifier is None and current is not None or
                target_modifier is not None and (current is None or
                current.as_pointer() != target_modifier.as_pointer())):
            raise CompileError("Mesh Compute target modifier changed before finalization")
        stage = candidates[0].mesh_compute_stage
        created = current is None
        modifier = owner.modifiers.new(name, "NODES") if created else current
        previous_group = None if created else modifier.node_group
        binding = None

        def restore_modifier():
            """Undo only the modifier reference installed by this compilation."""
            if any(item.as_pointer() == modifier.as_pointer() for item in owner.modifiers):
                if created:
                    owner.modifiers.remove(modifier)
                else:
                    modifier.node_group = previous_group

        try:
            modifier.node_group = group
            binding = prepare_modifier_binding(owner, modifier, group, stage)
        except BaseException:
            if binding is not None:
                release_modifier_binding(binding)
            restore_modifier()
            raise

        def revert():
            """Restore the original modifier on a failed transaction."""
            restore_modifier()
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
    "compile_script_auto",
    "create_selected_library_group_auto",
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
        prepared, session = _prepare_mesh_compute_root(
            backend, source, name=old_group.name, existing_group=old_group,
        )
        if not _requires_mesh_compute(prepared):
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
