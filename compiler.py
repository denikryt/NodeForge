"""Public compiler facade and high-level Geometry Nodes group assembly."""

from contextlib import nullcontext

from .constants import TYPE_GEOMETRY
from .errors import CompileError
from .compiler_identities import BindingId
from .values import Value, make_value
from .nodes import _new_node, _socket_type_for
from .storage import (
    _store_group_source,
    _extract_group_source,
    _get_or_create_scratch_text,
    _replace_text_contents,
    INPUT_DEFAULTS_PROP,
)
from .interface import _create_group_input_socket
from .update import (
    _apply_group_defaults_to_node,
    _capture_node_external_state,
    _restore_node_external_state,
    _capture_group_external_state,
    _restore_group_external_state,
)
from .library import (
    materialize_library_entry_group_for_record,
    update_materialized_library_entry_group_for_record,
)
from .function_instances import (
    FUNCTION_ROOT_OWNER_ID_PROP,
    function_group_owner_scope as make_function_group_owner_scope,
    interface_contract,
    stamp_function_metadata,
)
from .semantic_group import SemanticGroupCompilation, analyze_group_source
from .source_callables import SourceCallableSession
from .blender_ir_lowering import BlenderIRLoweringContext, lower_body
from .function_materializer import FunctionMaterializationContext, FunctionMaterializer
from .group_context import GroupContextSlot
from .callable_contracts import canonicalize_group_input_default
from .blender_group_backend import BlenderGroupBackend, BlenderGroupBuildRequest
from .resolved_environment import ResolvedEnvironment, resolve_environment


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


def _assert_prepared_interface_parity(group, prepared: SemanticGroupCompilation) -> None:
    """Assert realized public sockets match the frontend-owned final callable contract."""
    physical_inputs = [
        item for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "in_out", None) == "INPUT"
    ]
    physical_outputs = [
        item for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "in_out", None) == "OUTPUT"
    ]
    if len(physical_inputs) != len(prepared.interface.inputs):
        raise CompileError("Internal error: physical input count disagrees with semantic interface")
    if len(physical_outputs) != len(prepared.interface.outputs):
        raise CompileError("Internal error: physical output count disagrees with semantic interface")
    for physical, contract in zip(physical_inputs, prepared.interface.inputs):
        if getattr(physical, "name", "") != contract.display_name:
            raise CompileError("Internal error: physical input order/name disagrees with semantic interface")
        socket_type = getattr(physical, "socket_type", "") or getattr(physical, "bl_socket_idname", "")
        if socket_type != _socket_type_for(contract.typ):
            raise CompileError("Internal error: physical input type disagrees with semantic interface")
        if contract.has_default:
            if not hasattr(physical, "default_value"):
                raise CompileError("Internal error: semantic input default has no physical default")
            physical_default = canonicalize_group_input_default(contract.typ, physical.default_value)
            if physical_default != contract.default:
                raise CompileError("Internal error: physical input default disagrees with semantic interface")
    for physical, contract in zip(physical_outputs, prepared.interface.outputs):
        if getattr(physical, "name", "") != contract.display_name:
            raise CompileError("Internal error: physical output order/name disagrees with semantic interface")
        socket_type = getattr(physical, "socket_type", "") or getattr(physical, "bl_socket_idname", "")
        if socket_type != _socket_type_for(contract.typ):
            raise CompileError("Internal error: physical output type disagrees with semantic interface")


def _populate_group(
    group,
    request: BlenderGroupBuildRequest,
    *,
    generated_resource_transaction=None,
    group_backend=None,
):
    """Materialize one already-prepared group without parsing or semantic reanalysis."""
    if not isinstance(request, BlenderGroupBuildRequest):
        raise TypeError("request must be BlenderGroupBuildRequest")
    prepared = request.prepared_compilation
    identity = prepared.identity
    try:
        group.color_tag = "CONVERTER"
    except Exception:
        pass
    if identity.root_owner_id is not None:
        group[FUNCTION_ROOT_OWNER_ID_PROP] = identity.root_owner_id
    _store_group_source(group, prepared.source)
    try:
        group[INPUT_DEFAULTS_PROP] = {}
    except Exception:
        pass

    geometry_iface = None
    if prepared.geometry_mode:
        geometry_iface = group.interface.new_socket(
            name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
        )
    group_input = _new_node(group, "NodeGroupInput", -1100, 0)
    group_output = _new_node(group, "NodeGroupOutput", 1100, 0)
    group_output.is_active_output = True

    runtime_values = {}
    interface_items = {}
    for contract in prepared.interface.inputs:
        binding_id = contract.interface_origin
        if not isinstance(binding_id, BindingId):
            continue
        value, iface = _create_group_input_socket(
            group,
            group_input,
            contract.display_name,
            contract.typ,
            contract.default if contract.has_default else None,
        )
        runtime_values[binding_id] = value
        interface_items[binding_id] = iface

    geometry_value = None
    if prepared.geometry_mode:
        geometry_socket = next((socket for socket in group_input.outputs if socket.name == "Geometry"), None)
        if geometry_socket is None:
            raise CompileError("Internal error: prepared Geometry input did not materialize")
        geometry_value = make_value(geometry_socket, TYPE_GEOMETRY)

    session = request.source_callable_session
    materializer = FunctionMaterializer(group_backend=group_backend)
    materialization_context = FunctionMaterializationContext(
        function_group_cache=request.function_group_cache if request.function_group_cache is not None else {},
        function_group_transaction=request.function_group_transaction,
        function_compilation_trace=request.function_compilation_trace,
        source_callable_session=session,
    )
    lowering_context = BlenderIRLoweringContext(
        group=group,
        runtime_bindings=runtime_values,
        group_context_values=(
            {GroupContextSlot.CURRENT_GEOMETRY: geometry_value}
            if geometry_value is not None else {}
        ),
        interface_input_items=interface_items,
        source_callable_session=session,
        function_materializer=materializer,
        function_materialization_context=materialization_context,
        helper_namespace=request.helper_namespace or request.name,
        extension_registry=(
            session.resolved_environment.extension_registry
            if session is not None
            else None
        ),
        generated_resource_transaction=generated_resource_transaction,
    )

    trace_context = nullcontext(None)
    if request.function_compilation_inputs is not None and request.function_compilation_trace is not None:
        own_inputs = dict(request.function_compilation_inputs)
        own_inputs["lowered_source"] = prepared.normalized_lowered_source
        trace_context = request.function_compilation_trace.group(identity.declaration_owner, own_inputs)

    with trace_context as frame:
        if frame is not None:
            for owner_key, fingerprint in prepared.extension_dependencies:
                frame.record_dependency_identity(
                    make_function_group_owner_scope("EXTENSION", *owner_key),
                    fingerprint,
                )
        body_result = lower_body(
            lowering_context,
            prepared.body,
            runtime_values,
            base_depth=1,
            group_input=group_input,
        )

        if prepared.geometry_mode:
            current_geometry = body_result.group_context_values.get(GroupContextSlot.CURRENT_GEOMETRY)
            if not isinstance(current_geometry, Value) or current_geometry.typ is not TYPE_GEOMETRY:
                raise CompileError("Internal error: prepared geometry body lost current Geometry value")
            group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
            group.links.new(current_geometry.socket, group_output.inputs["Geometry"])

        body_outputs = (
            body_result.explicit_outputs
            if body_result.explicit_outputs
            else ((body_result.auto_output,) if body_result.auto_output is not None else ())
        )
        expected_outputs = prepared.interface.outputs[1:] if prepared.geometry_mode else prepared.interface.outputs
        if len(body_outputs) != len(expected_outputs):
            raise CompileError("Internal error: physical body output count disagrees with semantic summary")
        for (_source_name, value), contract in zip(body_outputs, expected_outputs):
            if value.typ is not contract.typ:
                raise CompileError("Internal error: physical output type disagrees with semantic output contract")
            group.interface.new_socket(
                name=contract.display_name,
                in_out="OUTPUT",
                socket_type=_socket_type_for(contract.typ),
            )
            group.links.new(value.socket, group_output.inputs[contract.display_name])

        _assert_prepared_interface_parity(group, prepared)
        if frame is not None:
            result = frame.finish(interface_contract(group))
            if not result.freshness_unproven:
                stamp_function_metadata(
                    group,
                    instance_key=request.function_instance_key,
                    definition_owner=identity.definition_owner,
                    fingerprint=result.fingerprint,
                )
    return group


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
    """Create or update a reusable node group for a catalog entry."""
    environment = resolve_environment()
    record = environment.catalog(namespace).find(name)
    if record is None:
        raise CompileError(f"Unknown {namespace} library entry: {name}")
    return materialize_library_entry_group_for_record(record, _new_group_backend(environment))


def create_library_function_group(name: str):
    """Create or update a reusable node group for a function-library entry."""
    return create_library_catalog_group("functions", name)


def update_library_catalog_group(group, namespace: str, name: str):
    """Reload a catalog-backed group through prepared semantic compilation."""
    environment = resolve_environment()
    record = environment.catalog(namespace).find(name)
    if record is None:
        raise CompileError(f"Current source for {namespace} library entry {name!r} is unavailable")
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
    "create_library_function_group",
    "_apply_group_defaults_to_node",
    "_capture_node_external_state",
    "_restore_node_external_state",
    "_capture_group_external_state",
    "_restore_group_external_state",
    "_extract_group_source",
    "_get_or_create_scratch_text",
    "_replace_text_contents",
]
