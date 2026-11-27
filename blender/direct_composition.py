"""Lower prepared physical root IR using an externally managed build attempt."""

from __future__ import annotations

from ..errors import CompileError
from ..function_instances import FunctionCompilationTrace
from .function_materializer import FunctionMaterializationContext, FunctionMaterializer
from .ir_lowering import BlenderIRLoweringContext, lower_body


def validate_prepared_direct(prepared, target):
    """Reject unsupported direct-root contracts before any node/group mutation."""
    if getattr(target, 'bl_idname', None) != 'GeometryNodeTree':
        raise CompileError('Direct composition requires an existing base GeometryNodeTree')
    if prepared.geometry_mode or prepared.interface.inputs or prepared.interface.outputs:
        raise CompileError('Direct composition may not declare a base tree interface')
    if any(owner and owner[0] in {'local-runtime', 'examples-runtime'}
           for owner, _fingerprint in prepared.extension_dependencies):
        raise CompileError('Mesh Compute requires a supported root modifier contract; '
                           'direct base-tree installation is unavailable')


def compose_prepared_initial(prepared, target, backend, session, *,
                             transaction, root_input_endpoint,
                             generated_resource_transaction=None):
    """Lower root IR; the calling root updater owns commit, cleanup and rollback.

    The transaction and canonical input endpoint belong to the same outer root
    attempt, including all nested inline expansions targeting this tree.
    """
    if not prepared.body.statements:
        raise CompileError(
            'Root lowering requires a physical top-level body; '
            'definition-only sources use GROUPS_ONLY publication'
        )
    context = BlenderIRLoweringContext(
        group=target,
        runtime_bindings={},
        source_callable_session=session,
        function_materializer=FunctionMaterializer(group_backend=backend),
        function_materialization_context=FunctionMaterializationContext(
            function_group_cache={},
            function_group_transaction=transaction,
            function_compilation_trace=FunctionCompilationTrace(),
            source_callable_session=session,
        ),
        helper_namespace=target.name,
        extension_registry=session.resolved_environment.extension_registry,
        root_input_endpoint=root_input_endpoint,
        generated_resource_transaction=generated_resource_transaction,
    )
    lower_body(context, prepared.body, {}, base_depth=1)
    return target


def materialize_definition_entries(prepared, materializer, materialization_context, snapshot):
    """Publish only zero-incoming source definitions, without root node placement.

    The caller owns the shared group transaction and its commit/rollback.  The
    same source-function IDs and materializer are used by direct compilation.
    """
    from ..compiler_identities import GroupCompilationIdentity, module_definition_function_id
    from ..function_instances import function_materialization_owner_scope
    from ..semantic.function_signatures import compile_function_signature
    from ..semantic.source_callables import serialize_local_signature
    from ..semantic.ir import IRFunctionMaterialization, IRFunctionMaterializationMode

    graph = prepared.source_call_graph
    if graph is None:
        raise CompileError('Definition-only compilation lacks a resolved call graph')
    session = materialization_context.source_callable_session
    env, compile_time_values = session.callable_environment_for_module(snapshot)
    groups = []
    for reference in graph.entries:
        fn = snapshot.definitions[reference.name]
        signature = compile_function_signature(
            fn, owner_scope=reference.stable_key(),
            declaration_owner=reference.stable_key(),
            compile_time_values=compile_time_values,
        )
        types = {item.target_name: item.typ for item in signature.declarations}
        function_id = module_definition_function_id(
            reference, serialize_local_signature(tuple(item.target_name for item in signature.declarations), types)
        )
        materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
        owner_scope = function_materialization_owner_scope(materialization)
        identity = GroupCompilationIdentity(None, owner_scope, reference.stable_key(), function_id.stable_key())
        prepared_callable = session.prepare_definition(
            function_id=function_id, identity=identity, fn=fn,
            callable_environment=env,
            compile_time_values=compile_time_values, source_module=snapshot,
            helper_namespace=fn.name,
        )
        groups.append(materializer.materialize_module(
            prepared_callable, materialization, materialization_context,
        ).group)
    return tuple(groups)


def place_definition_entries(target, groups, created_nodes):
    """Place missing entry groups and journal new nodes for the caller's rollback."""
    from .library_groups import apply_function_node_display_name

    existing = {
        node.node_tree.as_pointer() for node in target.nodes
        if node.bl_idname == 'GeometryNodeGroup' and node.node_tree is not None
    }
    missing = [group for group in groups if group.as_pointer() not in existing]
    if not missing:
        return

    spacing = 80
    right_edge = max(
        (node.location_absolute.x + max(node.width, node.dimensions.x) for node in target.nodes),
        default=-spacing,
    )
    x = right_edge + spacing
    for group in missing:
        node = target.nodes.new('GeometryNodeGroup')
        created_nodes.append(node)
        node.node_tree = group
        apply_function_node_display_name(node, group)
        node.location = (x, 0)
        node.select = False
        x += node.width + spacing
