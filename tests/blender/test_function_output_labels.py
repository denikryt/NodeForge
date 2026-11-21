"""Physical group interface coverage for named scalar function returns."""

from helpers import *


def _output_names(group):
    return [
        item.name for item in group.interface.items_tree
        if item.item_type == "SOCKET" and item.in_out == "OUTPUT"
    ]


def test_single_return_variable_names_physical_socket():
    group = compile_group('''
def named(geo: Geometry):
    final_geometry = transform(geo, translation=vector(1, 0, 0))
    return final_geometry
''', 'NFTest_ReturnAutoName')
    check(_output_names(group) == ['Final Geometry'], f'actual outputs: {_output_names(group)}')
    check('Final Geometry' in [socket.name for socket in group.nodes.get('Group Output').inputs],
          'Group Output should use the inferred label')


def test_explicit_output_label_names_physical_socket():
    group = compile_group('''
def named(geo: Geometry) -> Geometry["Transformed Shape"]:
    return transform(geo, translation=vector(1, 0, 0))
''', 'NFTest_ReturnExplicitName')
    check(_output_names(group) == ['Transformed Shape'], f'actual outputs: {_output_names(group)}')


def test_single_return_expression_uses_value_fallback():
    group = compile_group('''
def unnamed(geo: Geometry) -> Geometry:
    return transform(geo, translation=vector(1, 0, 0))
''', 'NFTest_ReturnExpressionFallback')
    check(_output_names(group) == ['Value'], f'actual outputs: {_output_names(group)}')


def test_nested_function_calls_preserve_both_output_naming_styles():
    root = bpy.data.node_groups.new('NFTest_ReturnNamesRoot', 'GeometryNodeTree')
    compile_root('''
def move_x(geometry: Geometry, x_shift: Float["X Shift"] = 0.0) -> Geometry["Geometry"]:
    return transform(geometry, translation=vector(x_shift, 0, 0))

def move():
    left = move_x(cube(1.0), -2.0)
    right = move_x(cube(1.0), 2.0)
    combined = join([left, right])
    return combined
''', root)
    check(not list(root.nodes), 'group-only Compile inserted root instances')
    move = published_entry(root, 'move')
    check(_output_names(move) == ['Combined'], f'outer group outputs: {_output_names(move)}')
    shift_calls = [node for node in move.nodes if node.bl_idname == 'GeometryNodeGroup']
    check(len(shift_calls) == 2, 'expected two nested move_x calls')
    check(all([socket.name for socket in node.outputs] == ['Geometry'] for node in shift_calls),
          f'nested outputs: {[[socket.name for socket in node.outputs] for node in shift_calls]}')
    check(shift_calls[0].node_tree is shift_calls[1].node_tree, 'same function should reuse one group')


@pytest.mark.parametrize("label", ["L" * 64, "Г" * 32])
def test_overlong_output_label_fails_preflight_without_blender_keyerror(label):
    source = f"def named() -> Float[{label!r}]:\n    return 1.0\n"
    with pytest.raises(CompileError, match="63-byte UTF-8"):
        compile_group(source, "NFTest_ReturnOverlong")


def test_63_byte_explicit_output_label_is_physical_node_socket_name():
    label = "Г" * 31 + "X"
    group = compile_group(f"def named() -> Float[{label!r}]:\n    return 1.0\n", "NFTest_Return63Bytes")
    check(_output_names(group) == [label], f"incorrect interface output name: {_output_names(group)}")
    output = next(node for node in group.nodes if node.bl_idname == "NodeGroupOutput")
    check(output.inputs[0].name == label, "physical socket name differs from interface label")


def test_overlong_auto_output_name_preserves_old_value_fallback():
    identifier = "geometry_reconstructed_from_multiple_complex_transformation_stages_exceeding_limit"
    source = f"def named():\n    {identifier} = cube(1)\n    return {identifier}\n"
    group = compile_group(source, "NFTest_ReturnOverlongAutomatic")
    check(_output_names(group) == ["Value"], "overlong inferred name should use existing Value fallback")
