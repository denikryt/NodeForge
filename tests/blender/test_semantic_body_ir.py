"""Blender integration coverage for stage-17 straight-line Semantic Body IR."""

from helpers import *

from NodeForge import statement_compiler


def _interface_sockets(group, in_out):
    """Return interface sockets in declaration order for one direction."""
    return [
        item
        for item in group.interface.items_tree
        if getattr(item, "item_type", "") == "SOCKET" and getattr(item, "in_out", "") == in_out
    ]


def _node_signature(group):
    """Return ordered node type, operation and position data for topology checks."""
    return [
        (
            getattr(node, "bl_idname", ""),
            getattr(node, "operation", None),
            tuple(round(float(v), 4) for v in node.location),
        )
        for node in group.nodes
    ]


def test_basic_assignment_chain_uses_one_body_lowering_session(monkeypatch):
    """Eligible straight-line assignments are analyzed as one IRBody before Blender lowering."""
    calls = []
    original = statement_compiler.lower_ir_body

    def wrapped(context, body, initial_runtime_bindings, base_depth=1, **kwargs):
        calls.append((body, dict(initial_runtime_bindings), base_depth))
        return original(context, body, initial_runtime_bindings, base_depth, **kwargs)

    monkeypatch.setattr(statement_compiler, "lower_ir_body", wrapped)
    group = compile_group(
        '''
x = a + b
x = x * 2
x
''',
        "NFTest_basic_body_chain",
    )
    check(len(calls) == 1, f"expected one IRBody lowering session, got {len(calls)}")
    check(calls[0][2] == 1, f"body expression base depth changed: {calls[0][2]}")
    outputs = _interface_sockets(group, "OUTPUT")
    check([item.name for item in outputs] == ["out"], f"unexpected final-expression output interface: {[item.name for item in outputs]}")
    math_nodes = [node for node in group.nodes if getattr(node, "bl_idname", "") == "ShaderNodeMath"]
    check([node.operation for node in math_nodes] == ["ADD", "MULTIPLY"], "assignment topology/order changed")
    check(tuple(math_nodes[0].location) == (240.0, -90.0), f"ADD placement changed: {tuple(math_nodes[0].location)}")
    check(tuple(math_nodes[1].location) == (240.0, -90.0), f"MULTIPLY placement changed: {tuple(math_nodes[1].location)}")
    bpy.data.node_groups.remove(group)


def test_basic_body_explicit_output_order_and_augassign_are_preserved():
    """Output order/name semantics and frontend-desugared augmented assignment remain unchanged."""
    group = compile_group(
        '''
x = a
x += b
output("First", x)
output("Second", b)
''',
        "NFTest_basic_body_outputs",
    )
    outputs = _interface_sockets(group, "OUTPUT")
    check([item.name for item in outputs] == ["First", "Second"], f"output order changed: {[item.name for item in outputs]}")
    adds = [
        node for node in group.nodes
        if getattr(node, "bl_idname", "") == "ShaderNodeMath" and getattr(node, "operation", None) == "ADD"
    ]
    check(len(adds) == 1, f"augassign did not lower to one ADD: {len(adds)}")
    bpy.data.node_groups.remove(group)


def test_duplicate_explicit_input_labels_create_distinct_sockets_and_defaults():
    """The 0.51 explicit-input contract separates binding identity from display labels."""
    group = compile_group(
        '''
x = input_float("Scale", default=1.0)
y = input_float("Scale", default=7.0)
output("X", x)
output("Y", y)
''',
        "NFTest_duplicate_explicit_inputs",
    )
    scale_items = [item for item in _interface_sockets(group, "INPUT") if item.name == "Scale"]
    check(len(scale_items) == 2, f"duplicate labels collapsed to {len(scale_items)} socket(s)")
    check(scale_items[0].identifier != scale_items[1].identifier, "duplicate labels lost physical socket identity")
    check(float(scale_items[0].default_value) == 1.0, f"first default changed: {scale_items[0].default_value}")
    check(float(scale_items[1].default_value) == 7.0, f"second default changed: {scale_items[1].default_value}")
    bpy.data.node_groups.remove(group)


def test_display_label_does_not_alias_runtime_source_binding():
    """A display label equal to another variable name still creates a fresh explicit input."""
    group = compile_group(
        '''
a = 1.0
x = input_float("a", default=7.0)
output(x)
''',
        "NFTest_input_label_not_binding",
    )
    inputs = [item for item in _interface_sockets(group, "INPUT") if item.name == "a"]
    check(len(inputs) == 1, f"display-only label did not create exactly one socket: {len(inputs)}")
    check(float(inputs[0].default_value) == 7.0, f"display-only input default changed: {inputs[0].default_value}")
    bpy.data.node_groups.remove(group)


def test_implicit_final_assignment_output_keeps_assignment_name():
    """A body ending in an assignment retains the legacy assignment-name auto output."""
    group = compile_group(
        "x = a + b\n",
        "NFTest_basic_body_assignment_auto_output",
    )
    outputs = _interface_sockets(group, "OUTPUT")
    check([item.name for item in outputs] == ["x"], f"assignment auto-output changed: {[item.name for item in outputs]}")
    bpy.data.node_groups.remove(group)


def test_explicit_outputs_clear_auto_candidate_and_unique_duplicate_names():
    """Explicit outputs win over prior assignment auto candidates and preserve name uniquing."""
    group = compile_group(
        '''
x = a + b
output("Result", x)
output("Result", b)
''',
        "NFTest_basic_body_explicit_wins",
    )
    outputs = _interface_sockets(group, "OUTPUT")
    check([item.name for item in outputs] == ["Result", "Result_2"], f"explicit output semantics changed: {[item.name for item in outputs]}")
    bpy.data.node_groups.remove(group)


def test_body_local_backend_values_are_not_published_to_compiler_session(monkeypatch):
    """Migrated x/y/z body assignments stay in the body lowerer's private materialization map."""
    from NodeForge import compiler as compiler_module

    published_names = []
    original = compiler_module.Compiler.bind_runtime_value

    def wrapped(self, name, value):
        published_names.append(name)
        return original(self, name, value)

    monkeypatch.setattr(compiler_module.Compiler, "bind_runtime_value", wrapped)
    group = compile_group(
        '''
x = a + 1
y = x * 2
z = y + x
output(z)
''',
        "NFTest_body_local_backend_values",
    )
    check("a" in published_names, "implicit body-entry input was not seeded through Compiler")
    check(not ({"x", "y", "z"} & set(published_names)), f"body locals leaked into Compiler: {published_names}")
    bpy.data.node_groups.remove(group)


def test_duplicate_input_label_with_different_types_is_legal():
    """Display labels may repeat across physical socket types."""
    group = compile_group(
        '''
x = input_float("Same")
y = input_vector("Same")
output("X", x)
output("Y", y)
''',
        "NFTest_duplicate_input_label_types",
    )
    same = [item for item in _interface_sockets(group, "INPUT") if item.name == "Same"]
    check(len(same) == 2, f"same display label across types collapsed: {len(same)}")
    check(getattr(same[0], "socket_type", "") != getattr(same[1], "socket_type", ""), "different input types were not preserved")
    bpy.data.node_groups.remove(group)


def test_nested_input_calls_fail_before_interface_socket_creation():
    """Invalid input placement is rejected before arbitrary expression dispatch can publish sockets."""
    before = {group.as_pointer() for group in bpy.data.node_groups}
    try:
        compiler.create_expression_group(
            'x = input_float("Scale", default=1.0) + input_float("Scale", default=7.0)\noutput(x)',
            "NFTest_nested_input_rejected",
        )
    except CompileError as exc:
        check(
            str(exc) == "input_*() may only be used as the complete right-hand side of a simple assignment",
            f"unexpected nested-input diagnostic: {exc}",
        )
    else:
        raise AssertionError("nested input call unexpectedly compiled")
    leaked = [group.name for group in bpy.data.node_groups if group.as_pointer() not in before]
    check(not leaked, f"invalid nested input compilation leaked groups: {leaked}")
