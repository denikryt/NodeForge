"""An initial direct-composition failure may remove only attempt-created nodes."""

from helpers import *


def _user_graph(name):
    """Create a linked user graph whose original nodes must remain untouched."""
    tree = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    left = tree.nodes.new('GeometryNodeMeshCube')
    right = tree.nodes.new('GeometryNodeTransform')
    tree.links.new(left.outputs['Mesh'], right.inputs['Geometry'])
    return tree, {node.as_pointer() for node in tree.nodes}, (
        left.as_pointer(), right.as_pointer(),
        tree.links[0].from_socket.identifier,
        tree.links[0].to_socket.identifier,
    )


def _assert_user_graph(tree, before, endpoint_ids):
    """Assert exact original physical node identities and edge endpoints remain."""
    assert {node.as_pointer() for node in tree.nodes} == before
    assert len(tree.links) == 1
    link = tree.links[0]
    assert (link.from_node.as_pointer(), link.to_node.as_pointer(),
            link.from_socket.identifier, link.to_socket.identifier) == endpoint_ids


def test_late_mixed_statement_failure_removes_partially_created_level_zero_nodes(monkeypatch):
    """A node already created by a failing builtin belongs to the attempt journal."""
    from NodeForge.blender import ir_lowering
    tree, before, endpoints = _user_graph('NFTest_mixed_initial_rollback')
    old_groups = {group.as_pointer() for group in bpy.data.node_groups}
    original = ir_lowering._cube_geometry
    count = 0

    def fail_second_cube(*args, **kwargs):
        nonlocal count
        result = original(*args, **kwargs)
        count += 1
        if count == 2:
            raise RuntimeError('injected second cube failure')
        return result

    monkeypatch.setattr(ir_lowering, '_cube_geometry', fail_second_cube)
    with pytest.raises(RuntimeError, match='second cube failure'):
        compiler.compose_source_initial('a = cube(1)\nb = cube(2)\n', tree)
    _assert_user_graph(tree, before, endpoints)
    assert {group.as_pointer() for group in bpy.data.node_groups} == old_groups


def test_late_second_definition_entry_failure_cleans_nodes_and_nested_groups(monkeypatch):
    """Both already-placed Group Nodes and newly materialized callees roll back."""
    from NodeForge.blender import library_groups
    tree, before, endpoints = _user_graph('NFTest_entry_initial_rollback')
    old_groups = {group.as_pointer() for group in bpy.data.node_groups}
    original = library_groups.apply_function_node_display_name
    count = 0

    def fail_after_second_placement(node, function_group):
        nonlocal count
        result = original(node, function_group)
        count += 1
        if count == 2:
            raise RuntimeError('injected second entry failure')
        return result

    monkeypatch.setattr(library_groups, 'apply_function_node_display_name', fail_after_second_placement)
    with pytest.raises(RuntimeError, match='second entry failure'):
        compiler.compose_source_initial('''def root_a(x: Float) -> Float:
    return child(x)

def root_b(x: Float) -> Float:
    return x + 2

def child(x: Float) -> Float:
    return x + 3
''', tree)
    _assert_user_graph(tree, before, endpoints)
    assert {group.as_pointer() for group in bpy.data.node_groups} == old_groups


def test_failed_initial_composition_restores_previously_existing_nested_group(monkeypatch):
    """Nested in-place group updates roll back after level-zero node cleanup."""
    from NodeForge.blender import library_groups
    context = 'NFTest_persisted_function_owner'
    old_target = bpy.data.node_groups.new('NFTest_existing_owner_target', 'GeometryNodeTree')
    initial = '''def ref(x: Float) -> Float:
    return x + 1
'''
    compiler.compose_source_initial(initial, old_target, module_context=context)
    existing = next(node.node_tree for node in old_target.nodes if node.bl_idname == 'GeometryNodeGroup')
    pointer = existing.as_pointer()
    previous = tuple((node.bl_idname, getattr(node, 'operation', '')) for node in existing.nodes)
    tree, before, endpoints = _user_graph('NFTest_failing_initial_new_target')
    original = library_groups.apply_function_node_display_name
    count = 0

    def fail_after_second(node, function_group):
        nonlocal count
        result = original(node, function_group)
        count += 1
        if count == 2:
            raise RuntimeError('second entry failed after nested update')
        return result

    monkeypatch.setattr(library_groups, 'apply_function_node_display_name', fail_after_second)
    with pytest.raises(RuntimeError, match='second entry failed'):
        compiler.compose_source_initial('''def ref(x: Float) -> Float:
    return x * 4

def independent(x: Float) -> Float:
    return x + 1
''', tree, module_context=context)
    _assert_user_graph(tree, before, endpoints)
    assert existing.as_pointer() == pointer
    assert tuple((node.bl_idname, getattr(node, 'operation', '')) for node in existing.nodes) == previous
