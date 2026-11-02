"""Blender regressions for prepared prepared source-call materialization."""

import bpy
import pytest

from helpers import check, compile_group
from NodeForge import compiler, local_sources
from NodeForge.blender import group_assembly as blender_group_assembly
from NodeForge.blender import ir_lowering as blender_ir_lowering
from NodeForge.errors import CompileError


def _remove_group(group):
    """Remove one live node group without assuming it still owns its original name."""
    if group is not None and any(candidate is group for candidate in bpy.data.node_groups):
        bpy.data.node_groups.remove(group, do_unlink=True)










def test_selected_local_catalog_reload_reuses_direct_library_owner_policy():
    """Selected Local reload uses the same direct LIBRARY owner before and after preparation."""
    local_dir = local_sources.ensure_local_catalog_dir()
    source = local_dir / "source_callable_selected_local_reload.nf"
    group = None
    try:
        source.write_text(
            'x = input_float("X", default=1.0)\noutput("Value", x + 1.0)\n',
            encoding="utf-8",
        )
        group = compiler.create_library_catalog_group("local", "source_callable_selected_local_reload")
        pointer = group.as_pointer()
        source.write_text(
            'x = input_float("X", default=1.0)\noutput("Value", x + 2.0)\n',
            encoding="utf-8",
        )

        compiler.update_library_catalog_group(group, "local", "source_callable_selected_local_reload")

        check(group.as_pointer() == pointer, "selected Local reload replaced the root datablock")
        check(
            "x + 2.0" in str(group.get("nodeforge_library_source") or ""),
            "selected Local reload did not publish the prepared source snapshot",
        )
    finally:
        source.unlink(missing_ok=True)
        _remove_group(group)


def test_public_materialize_group_callback_prepares_before_backend_publication():
    """Package-style materialize_group callbacks keep raw source outside the physical backend."""
    root = None
    try:
        backend = compiler._new_group_backend()

        def materialize_group(compile_group_callback):
            """Exercise the existing package-facing raw-source callback signature."""
            return compile_group_callback(
                'x = input_float("X", default=1.0)\noutput("X", x + 1.0)\n',
                name="NFTest_source_callable_public_compile_callback",
            )

        root = materialize_group(backend.compile_group_callback)
        check(root is not None, "package materialize_group callback returned no node group")
        check(root.get("nodeforge_function_root_owner_id"), "callback publication lost prepared root identity")
        check(any(node.bl_idname == "ShaderNodeMath" for node in root.nodes), "callback source was not materialized")
    finally:
        _remove_group(root)






def test_trace_context_is_released_when_physical_lowering_raises(monkeypatch):
    """Physical lowering errors leave no active compilation frame or false cycle state."""
    from NodeForge.function_instances import FunctionCompilationTrace

    trace = FunctionCompilationTrace()
    observed = {}

    def fail_lowering(*_args, **_kwargs):
        frame = trace.current
        observed["active"] = frame is not None
        observed["owner"] = frame.owner_identity if frame is not None else None
        raise RuntimeError("trace-context-sentinel")

    monkeypatch.setattr(blender_group_assembly, "lower_body", fail_lowering)
    backend = compiler._new_group_backend()
    with pytest.raises(RuntimeError, match="trace-context-sentinel"):
        backend.compile_group_callback(
            'x = input_float("X", default=1.0)\noutput("X", x)\n',
            name="NFTest_trace_context_failure_cleanup",
            function_compilation_trace=trace,
            function_compilation_inputs={"kind": "trace-regression"},
        )

    check(observed.get("active") is True, "trace frame was not active during physical lowering")
    owner = observed.get("owner")
    check(bool(owner), "trace failure did not expose the active owner identity")
    check(trace.current is None, "trace frame leaked after physical lowering failure")
    with trace.group(owner, {"kind": "reopen-after-failure"}) as frame:
        check(trace.current is frame, "trace owner could not be reopened after failure cleanup")
    check(trace.current is None, "reopened trace frame leaked after normal exit")
