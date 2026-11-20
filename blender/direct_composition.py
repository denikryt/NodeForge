"""Initial NodeForge composition into a caller-owned base GeometryNodeTree.

This is an internal target-specific lowering primitive. It does not replace or
manage nodes inserted during an earlier successful composition operation.
"""

from __future__ import annotations

from ..errors import CompileError
from ..function_instances import FunctionCompilationTrace
from .function_materializer import FunctionMaterializationContext, FunctionMaterializer
from .group_backend import BlenderGroupBuildTransaction
from .ir_lowering import BlenderIRLoweringContext, RootInputEndpoint, lower_body


def validate_prepared_direct(prepared, target):
    """Reject unsupported direct-root contracts before any node/group mutation."""
    if getattr(target, 'bl_idname', None) != 'GeometryNodeTree':
        raise CompileError('Direct composition requires an existing base GeometryNodeTree')
    if prepared.geometry_mode or prepared.interface.inputs or prepared.interface.outputs:
        raise CompileError('Direct composition may not declare a base tree interface')
    from ..semantic.runtime_capabilities import preflight_direct_base_operations
    preflight_direct_base_operations(prepared.body)
    if any(owner and owner[0] in {'local-runtime', 'examples-runtime'}
           for owner, _fingerprint in prepared.extension_dependencies):
        raise CompileError('Mesh Compute requires a supported root modifier contract; '
                           'direct base-tree installation is unavailable')


def compose_prepared_initial(prepared, target, backend, session, *, source_module,
                             transaction=None, root_input_endpoint=None):
    """Lower prepared source in place; a supplied nested transaction remains caller-owned.

    Every attempt cleans up only its own newly inserted root nodes. An
    internally created transaction is committed or rolled back here; an
    externally supplied one is neither committed nor rolled back here.
    """
    validate_prepared_direct(prepared, target)
    preexisting_nodes = {node.as_pointer() for node in target.nodes}
    own_transaction = transaction is None
    if transaction is None:
        transaction = BlenderGroupBuildTransaction()
    cache = {}
    trace = FunctionCompilationTrace()
    context = BlenderIRLoweringContext(
        group=target,
        runtime_bindings={},
        source_callable_session=session,
        function_materializer=FunctionMaterializer(group_backend=backend),
        function_materialization_context=FunctionMaterializationContext(
            function_group_cache=cache,
            function_group_transaction=transaction,
            function_compilation_trace=trace,
            source_callable_session=session,
        ),
        helper_namespace=target.name,
        extension_registry=session.resolved_environment.extension_registry,
        root_input_endpoint=root_input_endpoint if root_input_endpoint is not None else RootInputEndpoint(),
    )
    try:
        if prepared.body.statements:
            # NODEFORGE-TEMP-ISSUE61-ROOT-STAGING: Root-producing Compile still lowers in the authoritative tree while physical Extension API v2 is rejected in root. Issue #61 PR 2 must replace this call with native-copy private staging and strict subgraph transfer; delete this marker when no backend can receive the authoritative root.
            lower_body(context, prepared.body, {}, base_depth=1)
        else:
            _place_definition_entries(prepared, context, source_module)
        if own_transaction:
            transaction.commit()
    except Exception as error:
        cleanup_failure = None
        for node in tuple(target.nodes):
            if node.as_pointer() not in preexisting_nodes:
                try:
                    target.nodes.remove(node)
                except Exception as exc:
                    if cleanup_failure is None:
                        cleanup_failure = exc
        if own_transaction:
            try:
                transaction.rollback()
            except Exception as exc:
                if cleanup_failure is None:
                    cleanup_failure = exc
        if cleanup_failure is not None:
            error.add_note(f"Direct-composition node cleanup or rollback also failed: {cleanup_failure}")
            raise error from cleanup_failure
        raise
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


def _place_definition_entries(prepared, context, snapshot):
    """Place the shared source groups in the legacy in-place composition path."""
    from .library_groups import apply_function_node_display_name

    groups = materialize_definition_entries(
        prepared, context.function_materializer,
        context.function_materialization_context, snapshot,
    )
    for index, group in enumerate(groups):
        node = context.group.nodes.new('GeometryNodeGroup')
        node.node_tree = group
        node.location = (index * 260, 0)
        apply_function_node_display_name(node, group)
