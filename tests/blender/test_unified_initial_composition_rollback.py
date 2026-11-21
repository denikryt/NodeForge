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
    from NodeForge.blender.root_update import RootUpdateError
    with pytest.raises(RootUpdateError, match='second cube failure') as error:
        compile_root('a = cube(1)\nb = cube(2)\n', tree)
    assert not error.value.may_have_mutated
    _assert_user_graph(tree, before, endpoints)
    assert {group.as_pointer() for group in bpy.data.node_groups} == old_groups


def test_late_second_definition_entry_failure_cleans_nodes_and_nested_groups(monkeypatch):
    """Both already-placed Group Nodes and newly materialized callees roll back."""
    from NodeForge.blender.function_materializer import FunctionMaterializer
    tree, before, endpoints = _user_graph('NFTest_entry_initial_rollback')
    old_groups = {group.as_pointer() for group in bpy.data.node_groups}
    original = FunctionMaterializer.materialize_module

    def fail_after_first_entry(self, prepared, materialization, context, **kwargs):
        """Fail the second entry after earlier candidate group publication."""
        if prepared.contract.function_id.name == 'root_b':
            raise RuntimeError('injected second entry failure')
        return original(self, prepared, materialization, context, **kwargs)

    monkeypatch.setattr(FunctionMaterializer, 'materialize_module', fail_after_first_entry)
    with pytest.raises(RuntimeError, match='second entry failure'):
        compile_root('''def root_a(x: Float) -> Float:
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
    from NodeForge.blender.function_materializer import FunctionMaterializer
    context = 'NFTest_persisted_function_owner'
    old_target = bpy.data.node_groups.new('NFTest_existing_owner_target', 'GeometryNodeTree')
    initial = '''def ref(x: Float) -> Float:
    return x + 1
'''
    compile_root(initial, old_target, module_context=context)
    existing = next(g for g in bpy.data.node_groups if g.get('nodeforge_module_name') == 'ref'
                    and context in str(g.get('nodeforge_module_origin', '')))
    pointer = existing.as_pointer()
    previous = tuple((node.bl_idname, getattr(node, 'operation', '')) for node in existing.nodes)
    tree, before, endpoints = _user_graph('NFTest_failing_initial_new_target')
    original = FunctionMaterializer.materialize_module

    def fail_after_first(self, prepared, materialization, context, **kwargs):
        """Reject second group entry after mutating an existing authoritative group."""
        if prepared.contract.function_id.name == 'independent':
            raise RuntimeError('second entry failed after nested update')
        return original(self, prepared, materialization, context, **kwargs)

    monkeypatch.setattr(FunctionMaterializer, 'materialize_module', fail_after_first)
    with pytest.raises(RuntimeError, match='second entry failed'):
        compile_root('''def ref(x: Float) -> Float:
    return x * 4

def independent(x: Float) -> Float:
    return x + 1
''', tree, module_context=context)
    _assert_user_graph(tree, before, endpoints)
    assert existing.as_pointer() == pointer
    assert tuple((node.bl_idname, getattr(node, 'operation', '')) for node in existing.nodes) == previous
