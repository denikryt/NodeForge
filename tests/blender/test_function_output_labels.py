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
    compiler.compose_source_initial('''
def move_x(geometry: Geometry, x_shift: Float["X Shift"] = 0.0) -> Geometry["Geometry"]:
    return transform(geometry, translation=vector(x_shift, 0, 0))

def move():
    left = move_x(cube(1.0), -2.0)
    right = move_x(cube(1.0), 2.0)
    combined = join([left, right])
    return combined
''', root)
    top_calls = [node for node in root.nodes if node.bl_idname == 'GeometryNodeGroup']
    check(len(top_calls) == 1, f'expected one Move entry, got {len(top_calls)}')
    move = top_calls[0]
    check([socket.name for socket in move.outputs] == ['Combined'],
          f'outer group outputs: {[socket.name for socket in move.outputs]}')
    shift_calls = [node for node in move.node_tree.nodes if node.bl_idname == 'GeometryNodeGroup']
    check(len(shift_calls) == 2, 'expected two nested move_x calls')
    check(all([socket.name for socket in node.outputs] == ['Geometry'] for node in shift_calls),
          f'nested outputs: {[[socket.name for socket in node.outputs] for node in shift_calls]}')
    check(shift_calls[0].node_tree is shift_calls[1].node_tree, 'same function should reuse one group')
