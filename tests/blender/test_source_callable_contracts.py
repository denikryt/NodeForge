"""Blender regressions for prepared prepared source-call materialization."""

import bpy
import pytest

from helpers import check, compile_group
from NodeForge import compiler, library
from NodeForge.errors import CompileError


def _remove_group(group):
    """Remove one live node group without assuming it still owns its original name."""
    if group is not None and any(candidate is group for candidate in bpy.data.node_groups):
        bpy.data.node_groups.remove(group, do_unlink=True)


def test_prepared_local_function_persists_helper_metadata():
    """Prepared local helpers retain the metadata used for physical ownership/reuse."""
    root = helper = None
    try:
        root = compile_group(
            """
def source_callable_metadata_probe(x):
    return x + 1.0

y = source_callable_metadata_probe(2.0)
output("Y", y)
""",
            "NFTest_source_callable_local_helper_metadata",
        )
        helper = next(
            node.node_tree
            for node in root.nodes
            if getattr(getattr(node, "node_tree", None), "get", lambda *args: None)(
                "nodeforge_local_function_name"
            )
            == "source_callable_metadata_probe"
        )
        check(
            helper.get("nodeforge_generated_kind") == "local_function_helper",
            "prepared local helper lost its generated-kind metadata",
        )
        check(
            helper.get("nodeforge_local_function_source"),
            "prepared local helper did not persist its source snapshot metadata",
        )
        check(
            helper.get("nodeforge_local_function_signature") is not None,
            "prepared local helper did not persist its signature metadata",
        )
    finally:
        _remove_group(root)
        _remove_group(helper)


def test_nested_prepared_local_helpers_keep_root_helper_namespace():
    """Nested local helpers retain the outer root namespace used for durable lookup metadata."""
    root = outer = inner = None
    try:
        root = compile_group(
            """
a = input_float("A", default=2.0)

def source_callable_inner(x):
    return x + a

def source_callable_outer(x):
    return source_callable_inner(x)

y = source_callable_outer(1.0)
output("Y", y)
""",
            "NFTest_source_callable_nested_helper_namespace",
        )
        helpers = [
            group
            for group in bpy.data.node_groups
            if group.get("nodeforge_generated_kind") == "local_function_helper"
            and group.get("nodeforge_local_function_namespace")
            == "NFTest_source_callable_nested_helper_namespace"
        ]
        by_name = {group.get("nodeforge_local_function_name"): group for group in helpers}
        outer = by_name.get("source_callable_outer")
        inner = by_name.get("source_callable_inner")
        check(outer is not None, "outer prepared local helper lost the root helper namespace")
        check(inner is not None, "nested prepared local helper lost the root helper namespace")
    finally:
        _remove_group(root)
        _remove_group(outer)
        _remove_group(inner)


def test_prepared_source_call_object_result_keeps_object_runtime_wrapper():
    """Typed source-call Object outputs retain ObjectValue property behavior in the caller."""
    root = helper = None
    try:
        root = compile_group(
            """
def source_callable_object_passthrough(source):
    return source

obj = input_object("Source")
returned = source_callable_object_passthrough(obj)
returned.info(transform_space="RELATIVE", as_instance=False)
output("Geometry", returned.geometry)
""",
            "NFTest_source_callable_object_source_call",
        )
        helper_node = next(
            node
            for node in root.nodes
            if node.bl_idname == "GeometryNodeGroup"
            and getattr(node.node_tree, "get", lambda *args: None)("nodeforge_local_function_name")
            == "source_callable_object_passthrough"
        )
        helper = helper_node.node_tree
        check(helper_node.outputs[0].bl_idname == "NodeSocketObject", "source-call Object output socket type changed")
        info_nodes = [node for node in root.nodes if node.bl_idname == "GeometryNodeObjectInfo"]
        check(len(info_nodes) == 1, "source-call Object output did not resolve through Object Info")
        check(info_nodes[0].transform_space == "RELATIVE", "source-call Object configuration was lost")
        check(info_nodes[0].inputs["As Instance"].default_value is False, "source-call Object as_instance was lost")
    finally:
        _remove_group(root)
        _remove_group(helper)


def test_local_array_argument_keeps_existing_controlled_diagnostic():
    """Permanent local-call analysis preserves the established array-argument rejection."""
    with pytest.raises(
        CompileError,
        match="Local function constant arguments must be numbers, booleans, strings or vectors",
    ):
        compiler.create_expression_group(
            """
def first(items):
    return items[0]

output("Result", first([1.0, 2.0]))
""",
            "NFTest_source_callable_local_array_argument",
        )


def test_selected_local_catalog_reload_reuses_direct_library_owner_policy():
    """Selected Local reload uses the same direct LIBRARY owner before and after preparation."""
    local_dir = library.ensure_local_catalog_dir()
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


def test_source_backed_production_routes_do_not_reenter_legacy_compilers(monkeypatch):
    """Supported local and imported source calls compile with legacy entry points forbidden."""
    from NodeForge import statement_compiler

    def forbidden(*_args, **_kwargs):
        raise AssertionError("source-backed production routing re-entered a legacy compiler")

    monkeypatch.setattr(compiler.Compiler, "compile", forbidden)
    monkeypatch.setattr(statement_compiler, "compile_statement", forbidden)

    local_dir = library.ensure_local_catalog_dir()
    source_path = local_dir / "source_callable_route_tripwire.nf"
    roots = []
    helpers = []
    try:
        source_path.write_text(
            'x = input_float("X", default=1.0)\noutput("Value", x + 1.0)\n',
            encoding="utf-8",
        )
        local_root = compiler.create_expression_group(
            """
def source_callable_route_local(x):
    return x + 1.0

value = source_callable_route_local(2.0)
output("Value", value)
""",
            "NFTest_source_callable_route_local",
        )
        roots.append(local_root)
        helpers.extend(
            node.node_tree
            for node in local_root.nodes
            if node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None
        )

        imported_root = compiler.create_expression_group(
            """
from local import source_callable_route_tripwire
value = source_callable_route_tripwire(2.0)
output("Value", value)
""",
            "NFTest_source_callable_route_imported",
        )
        roots.append(imported_root)
        helpers.extend(
            node.node_tree
            for node in imported_root.nodes
            if node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None
        )
    finally:
        source_path.unlink(missing_ok=True)
        for root in reversed(roots):
            _remove_group(root)
        for helper in reversed(helpers):
            _remove_group(helper)


def test_imported_punctuation_only_label_remains_positionally_callable():
    """Public labels without a keyword alias remain addressable by final input position."""
    local_dir = library.ensure_local_catalog_dir()
    source_path = local_dir / "punctuation_only_label.nf"
    root = helper = None
    try:
        source_path.write_text(
            'value = input_float("!!!", default=1.0)\noutput("Value", value)\n',
            encoding="utf-8",
        )
        root = compile_group(
            """
from local import punctuation_only_label
value = punctuation_only_label(2.0)
output("Value", value)
""",
            "NFTest_punctuation_only_source_label",
        )
        call_node = next(
            node
            for node in root.nodes
            if node.bl_idname == "GeometryNodeGroup"
            and node.node_tree is not None
            and any(socket.name == "!!!" for socket in node.inputs)
        )
        helper = call_node.node_tree
        check(any(socket.name == "!!!" for socket in call_node.inputs), "punctuation-only public input was not realized")
        punctuation_input = next(socket for socket in call_node.inputs if socket.name == "!!!")
        check(punctuation_input.default_value == 2.0, "positional argument did not bind to punctuation-only input")
    finally:
        source_path.unlink(missing_ok=True)
        _remove_group(root)
        _remove_group(helper)


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

    monkeypatch.setattr(compiler, "lower_body", fail_lowering)
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


def test_local_hidden_argument_positions_follow_final_interface_tuple_order():
    """Local helper public/runtime/static captures retain their physical input topology."""
    root = helper = None
    try:
        root = compile_group(
            """
scale_max = input_float("Scale Max", default=2.0)
bias = 1.5

def scale0(g):
    return scale_max * g + bias

result = scale0(3.0)
output("Result", result)
""",
            "NFTest_local_hidden_argument_positions",
        )
        call_node = next(
            node
            for node in root.nodes
            if node.bl_idname == "GeometryNodeGroup"
            and getattr(node.node_tree, "get", lambda *args: None)("nodeforge_local_function_name") == "scale0"
        )
        helper = call_node.node_tree
        input_names = [socket.name for socket in call_node.inputs]
        check(input_names[:3] == ["g", "scale_max", "bias"], f"local helper input order changed: {input_names}")

        g_socket = call_node.inputs["g"]
        scale_socket = call_node.inputs["scale_max"]
        bias_socket = call_node.inputs["bias"]
        check(not g_socket.is_linked and g_socket.default_value == 3.0, "public static argument did not bind to g")
        check(not bias_socket.is_linked and bias_socket.default_value == 1.5, "hidden static capture did not bind to bias")
        check(scale_socket.is_linked, "hidden runtime capture scale_max was not linked")
        scale_link = next(
            link
            for link in root.links
            if link.to_node.name == call_node.name and link.to_socket.name == "scale_max"
        )
        check(scale_link.from_node.bl_idname == "NodeGroupInput", "scale_max capture did not come from parent Group Input")
        check(scale_link.from_socket.name == "Scale Max", "scale_max capture linked from the wrong parent socket")
    finally:
        _remove_group(root)
        _remove_group(helper)
