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


def compose_prepared_initial(prepared, target, backend, session):
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
        lower_body(context, prepared.body, {}, base_depth=1)
        transaction.commit()
    except Exception:
        transaction.rollback()
        raise
    return target
