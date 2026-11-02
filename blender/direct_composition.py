"""Initial NodeForge composition into a caller-owned base GeometryNodeTree.

This is an internal target-specific lowering primitive. It does not replace or
manage nodes inserted during an earlier successful composition operation.
"""

from __future__ import annotations

from ..errors import CompileError
from ..function_instances import FunctionCompilationTrace
from .function_materializer import FunctionMaterializationContext, FunctionMaterializer
from .group_backend import BlenderGroupBuildTransaction
from .ir_lowering import BlenderIRLoweringContext, lower_body


def compose_prepared_initial(prepared, target, backend, session, *, source_module):
    """Lower executable top-level IR without changing the base tree interface.

    No user-facing operator calls this internal entry until both the failure
    journal and direct-extension safety preflight are in place.
    """
    if getattr(target, 'bl_idname', None) != 'GeometryNodeTree':
        raise CompileError('Direct composition requires an existing base GeometryNodeTree')
    if prepared.geometry_mode or prepared.interface.inputs or prepared.interface.outputs:
        raise CompileError('Direct composition may not declare a base tree interface')

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
    )
    try:
        if prepared.body.statements:
            lower_body(context, prepared.body, {}, base_depth=1)
        else:
            _place_definition_entries(prepared, context, source_module)
        transaction.commit()
    except Exception:
        transaction.rollback()
        raise
    return target


def _place_definition_entries(prepared, context, snapshot):
    """Place zero-incoming source functions without treating them as DSL calls.

    Required inputs deliberately remain unconnected. Type specializations are
    determined from annotations/defaults; ambiguous untyped entries must be
    annotated rather than silently specialized as Float.
    """
    from ..compiler_identities import GroupCompilationIdentity, module_definition_function_id
    from ..function_instances import function_materialization_owner_scope
    from ..semantic.function_signatures import compile_function_signature
    from ..semantic.source_callables import serialize_local_signature
    from ..semantic.ir import IRFunctionMaterialization, IRFunctionMaterializationMode
    from .library_groups import apply_function_node_display_name

    graph = prepared.source_call_graph
    if graph is None:
        raise CompileError('Definition-only initial placement lacks a resolved call graph')
    session = context.source_callable_session
    env, compile_time_values = session.callable_environment_for_module(snapshot)
    for index, reference in enumerate(graph.entries):
        fn = snapshot.definitions[reference.name]
        signature = compile_function_signature(
            fn, owner_scope=reference.stable_key(),
            declaration_owner=reference.stable_key(),
            compile_time_values=compile_time_values,
        )
        types = {item.target_name: item.typ for item in signature.declarations}
        function_id = module_definition_function_id(
            reference, serialize_local_signature([arg.arg for arg in fn.args.args], types)
        )
        materialization = IRFunctionMaterialization(function_id, IRFunctionMaterializationMode.SHARED)
        owner_scope = function_materialization_owner_scope(materialization)
        identity = GroupCompilationIdentity(None, owner_scope, reference.stable_key(), function_id.stable_key())
        prepared_callable = session.prepare_definition(
            function_id=function_id, identity=identity, fn=fn,
            parameter_types=types, callable_environment=env,
            compile_time_values=compile_time_values, source_module=snapshot,
            helper_namespace=fn.name,
        )
        nested = context.function_materializer.materialize_module(
            prepared_callable, materialization, context.function_materialization_context,
        )
        node = context.group.nodes.new('GeometryNodeGroup')
        node.node_tree = nested.group
        node.location = (index * 260, 0)
        apply_function_node_display_name(node, nested.group)
