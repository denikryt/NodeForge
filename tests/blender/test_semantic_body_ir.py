"""Blender integration coverage for compiler-owned Semantic Body IR."""

from helpers import *

from NodeForge import compiler as compiler_module


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
    original = compiler_module.lower_body

    def wrapped(context, body, initial_runtime_bindings, base_depth=1, **kwargs):
        calls.append((body, dict(initial_runtime_bindings), base_depth))
        return original(context, body, initial_runtime_bindings, base_depth, **kwargs)

    monkeypatch.setattr(compiler_module, "lower_body", wrapped)
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



def test_known_numeric_assignment_is_not_erased_without_runtime_fold_proof():
    """Known numeric CTFE does not erase the permanent runtime DIVIDE graph."""
    group = compile_group(
        """
d = 1 / 2
output("D", d)
""",
        "NFTest_ctfe_known_numeric_keeps_runtime_divide",
    )
    divides = [
        node
        for node in group.nodes
        if getattr(node, "bl_idname", "") == "ShaderNodeMath"
        and getattr(node, "operation", None) == "DIVIDE"
    ]
    check(len(divides) == 1, f"known numeric assignment lost runtime DIVIDE topology: {len(divides)}")
    bpy.data.node_groups.remove(group)


def test_fold_safe_literal_assignment_creates_no_math_node():
    """A fold-safe scalar literal does not create an unnecessary Math node."""
    group = compile_group(
        """
x = 1.0
output("X", x)
""",
        "NFTest_ctfe_literal_fold_no_math",
    )
    math_nodes = [node for node in group.nodes if getattr(node, "bl_idname", "") == "ShaderNodeMath"]
    check(not math_nodes, f"literal assignment unexpectedly created Math nodes: {len(math_nodes)}")
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


def test_body_local_backend_values_materialize_only_declared_entry_input():
    """Body-local assignments stay internal while the declared entry input remains observable."""
    group = compile_group(
        """
x = a + 1
y = x * 2
z = y + x
output(z)
""",
        "NFTest_body_local_backend_values",
    )
    inputs = _interface_sockets(group, "INPUT")
    check([item.name for item in inputs] == ["a"], "prepared implicit body-entry input was not materialized")
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


def test_fixed_tuple_storage_and_unpack_stay_in_one_irbody_and_one_producer(monkeypatch):
    """Stored/projected and unpacked capture_attribute tuples avoid whole-body legacy lowering."""
    calls = []
    original = compiler_module.lower_body

    def wrapped(context, body, initial_runtime_bindings, base_depth=1, **kwargs):
        calls.append(body)
        return original(context, body, initial_runtime_bindings, base_depth, **kwargs)

    monkeypatch.setattr(compiler_module, "lower_body", wrapped)
    group = compile_group(
        '''
geo = input_geometry("Geometry")
pair = capture_attribute(geo, position().x)
output("Stored", pair[-1])
a, b = capture_attribute(geo, position().y)
output("Unpacked", b)
''',
        "NFTest_structural_tuple_body_ir",
    )
    check(len(calls) == 1, f"expected one IRBody lowering session, got {len(calls)}")
    captures = [node for node in group.nodes if node.bl_idname == "GeometryNodeCaptureAttribute"]
    check(len(captures) == 2, f"tuple projection duplicated capture producers: {len(captures)}")
    check([item.name for item in _interface_sockets(group, "OUTPUT")] == ["Stored", "Unpacked"], "tuple output order changed")
    bpy.data.node_groups.remove(group)


def test_flat_list_target_unpack_uses_structural_body_ir():
    """List-target unpack has the same fixed-tuple leaf binding semantics as tuple-target unpack."""
    group = compile_group(
        '''
geo = input_geometry("Geometry")
[a, b] = capture_attribute(geo, position().x)
output("Value", b)
''',
        "NFTest_structural_list_unpack_body_ir",
    )
    check(len([node for node in group.nodes if node.bl_idname == "GeometryNodeCaptureAttribute"]) == 1, "list unpack duplicated producer")
    bpy.data.node_groups.remove(group)


def test_runtime_if_uses_structured_body_ir_and_distinct_branch_input_declarations():
    """Runtime-if keeps structured body IR and distinct branch declaration identities."""
    from NodeForge import interface

    group = compile_group(
        '''
flag = input_bool("Flag")
if flag:
    x = input_float("A", default=1.0)
else:
    x = input_float("B", default=2.0)
output("X", x)
''',
        "NFTest_runtime_if_body_ir_inputs",
    )
    switches = [node for node in group.nodes if getattr(node, "bl_idname", "") == "GeometryNodeSwitch"]
    check(len(switches) == 1, f"expected one runtime-if Switch, found {len(switches)}")
    records = list(interface._get_group_input_declarations(group).values())
    x_records = [record for record in records if record["declaration_id"].target_name == "x"]
    check(len(x_records) == 2, f"expected two branch declaration records for x, found {len(x_records)}")
    ordinals = [record["declaration_id"].declaration_ordinal for record in x_records]
    check(ordinals == [0, 1], f"branch declaration ordinals changed: {ordinals}")
    bpy.data.node_groups.remove(group)


def test_literal_condition_if_materializes_both_branches_and_switch():
    """Literal conditions use the same runtime topology as every ordinary statement if."""
    group = compile_group(
        '''
x = input_float("X", default=1.0)
if True:
    x = x + 1
else:
    x = x + 100
output("X", x)
''',
        "NFTest_literal_if_runtime_switch",
    )
    adds = [
        node for node in group.nodes
        if getattr(node, "bl_idname", "") == "ShaderNodeMath" and getattr(node, "operation", None) == "ADD"
    ]
    switches = [node for node in group.nodes if getattr(node, "bl_idname", "") == "GeometryNodeSwitch"]
    check(len(adds) == 2, f"expected both literal-if branch ADD nodes, found {len(adds)}")
    check(len(switches) == 1, f"expected one literal-if runtime Switch, found {len(switches)}")
    outputs = [node for node in group.nodes if getattr(node, "bl_idname", "") == "NodeGroupOutput"]
    result_links = [
        link for link in group.links
        if link.to_node in outputs and link.to_socket.name == "X"
    ]
    check(len(result_links) == 1, f"expected one X output link, found {len(result_links)}")
    check(result_links[0].from_node == switches[0], "literal-if output is not driven by the runtime Switch")
    bpy.data.node_groups.remove(group)


def test_literal_condition_if_discovers_inputs_from_both_branches():
    """Both literal-condition branches contribute implicit runtime input dependencies."""
    group = compile_group(
        '''
x = 0.0
if True:
    x = a
else:
    x = b
output("X", x)
''',
        "NFTest_literal_if_both_branch_inputs",
    )
    input_names = [item.name for item in _interface_sockets(group, "INPUT")]
    check(input_names == ["a", "b"], f"literal-if branch input discovery changed: {input_names}")
    switches = [node for node in group.nodes if getattr(node, "bl_idname", "") == "GeometryNodeSwitch"]
    check(len(switches) == 1, f"expected one literal-if Switch, found {len(switches)}")
    bpy.data.node_groups.remove(group)

@pytest.mark.parametrize(
    "source,name",
    [
        (
            'items = []\nitems.append(cube(1))\noutput("Geometry", join(items))',
            "empty_append_join",
        ),
        (
            'size = input_float("Size", default=1.0)\n'
            'items = [cube(size), cube(2)]\n'
            'output("Geometry", join(items))',
            "mixed_literal_runtime",
        ),
        (
            'items = [cube(1)]\nalias = items\nalias.append(cube(2))\n'
            'output("Geometry", join(items))',
            "alias_append",
        ),
        (
            'inner = [cube(1)]\nouter = [inner]\n'
            'output("Geometry", outer[0][0])',
            "nested_index",
        ),
        (
            'items = [cube(1), cube(2)]\nparts = []\n'
            'for item in items:\n    parts.append(item)\n'
            'output("Geometry", join(parts))',
            "named_array_for",
        ),
        (
            'COUNT = 3\nparts = []\n'
            'for i in range(COUNT):\n'
            '    part = transform(cube(1), translation=vector(i, 0, 0))\n'
            '    parts.append(part)\n'
            'output("Geometry", join(parts))',
            "range_constant_runtime_body",
        ),
        (
            'flag = input_bool("Flag")\nx = 0.0\nitems = [1.0, 2.0]\n'
            'if flag:\n'
            '    for item in items:\n'
            '        x = x + item\n'
            'else:\n'
            '    x = x + 10.0\n'
            'output("X", x)',
            "ordinary_for_in_runtime_if",
        ),
    ],
)
def test_structural_arrays_structural_array_bodies_use_permanent_body_lowering(source, name):
    """Structural arrays and ordinary compile-time loops use permanent Semantic Body routing."""
    group = compile_group(source, f"NFTest_structural_arrays_{name}")
    check(
        not [node for node in group.nodes if node.bl_idname in {"GeometryNodeRepeatInput", "GeometryNodeRepeatOutput"}],
        f"{name} unexpectedly created a Repeat Zone",
    )
    bpy.data.node_groups.remove(group)


def test_structural_arrays_alias_append_preserves_join_topology_without_repeat_zone():
    """Alias-visible append keeps the existing two-cube/one-Join graph shape on the Semantic Body path."""
    group = compile_group(
        'items = [cube(1)]\nalias = items\nalias.append(cube(2))\n'
        'output("Geometry", join(items))',
        "NFTest_structural_arrays_alias_append_topology",
    )
    check(len([node for node in group.nodes if node.bl_idname == "GeometryNodeMeshCube"]) == 2, "cube count changed")
    check(len([node for node in group.nodes if node.bl_idname == "GeometryNodeJoinGeometry"]) == 1, "Join Geometry count changed")
    check(not [node for node in group.nodes if node.bl_idname == "GeometryNodeRepeatOutput"], "ordinary array flow created Repeat")
    bpy.data.node_groups.remove(group)


def test_structural_arrays_ordinary_for_inside_repeat_range_fails_before_blender_effects():
    """The ordinary-for frontend preserves Repeat grammar without leaked groups."""
    before = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(
        CompileError,
        match="repeat_range body supports assignments, builder methods, if blocks, and nested repeat_range loops",
    ):
        compiler.create_expression_group(
            "x = 0\n"
            "for i in repeat_range(2):\n"
            "    for j in [1, 2]:\n"
            "        x = x + j\n"
            'output("X", x)',
            "NFTest_structural_arrays_repeat_nested_ordinary_rejected",
        )
    leaked = [group.name for group in bpy.data.node_groups if group.as_pointer() not in before]
    check(not leaked, f"invalid nested ordinary for leaked Blender groups: {leaked}")


def test_input_default_uses_compile_time_fact_without_erasing_runtime_expression():
    """Compile-time default consumption does not remove the same expression's runtime graph."""
    group = compile_group(
        '''
d = 1 / 2
x = input_float("X", default=d)
y = d * position().x
output("Y", y)
''',
        "NFTest_evaluation_modes_input_default_and_runtime_use",
    )
    inputs = [item for item in _interface_sockets(group, "INPUT") if item.name == "X"]
    check(len(inputs) == 1, "expected one explicit X input")
    check(float(inputs[0].default_value) == 0.5, f"compile-time default changed: {inputs[0].default_value}")
    divide_nodes = [
        node for node in group.nodes
        if getattr(node, "bl_idname", "") == "ShaderNodeMath"
        and getattr(node, "operation", None) == "DIVIDE"
    ]
    check(len(divide_nodes) == 1, "compile-time default acquisition erased runtime DIVIDE")
    bpy.data.node_groups.remove(group)
