"""Initial direct composition uses an existing user GeometryNodeTree target."""

from helpers import *


def test_direct_compose_mixed_forward_decl_into_existing_base_group():
    """Functions and ordinary level-zero geometry nodes coexist without root I/O."""
    target = bpy.data.node_groups.new('NFTest_direct_mixed', 'GeometryNodeTree')
    user = target.nodes.new('GeometryNodeMeshCube')
    before_groups = {group.as_pointer() for group in bpy.data.node_groups}
    assert compiler.compose_source_initial('''a = cube(1)
b = raise_level(a)
def raise_level(geometry: Geometry) -> Geometry:
    return transform(geometry, translation=vector(0, 0, 1))
extra = cube(2)
''', target) is target
    assert any(node.as_pointer() == user.as_pointer() for node in target.nodes)
    assert not any(node.bl_idname in {'NodeGroupInput', 'NodeGroupOutput'} for node in target.nodes)
    groups = [node for node in target.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert len(groups) == 1
    assert groups[0].node_tree.get('nodeforge_module_name') == 'raise_level'
    assert len([node for node in target.nodes if node.bl_idname == 'GeometryNodeMeshCube']) >= 3
    assert {group.as_pointer() for group in bpy.data.node_groups} - before_groups == {groups[0].node_tree.as_pointer()}


def test_direct_compose_preserves_preexisting_base_links_and_interface():
    """Direct lowering must not replace or clear user-owned interface or links."""
    group = bpy.data.node_groups.new('NFTest_direct_user_links', 'GeometryNodeTree')
    source = group.nodes.new('GeometryNodeMeshCube')
    sink = group.nodes.new('GeometryNodeTransform')
    group.links.new(source.outputs['Mesh'], sink.inputs['Geometry'])
    interface_before = tuple((socket.name, socket.in_out) for socket in group.interface.items_tree)
    compiler.compose_source_initial('a = cube(1)\nb = cube(2)\n', group)
    assert tuple((socket.name, socket.in_out) for socket in group.interface.items_tree) == interface_before
    assert len(group.links) == 1
    assert group.links[0].from_node.as_pointer() == source.as_pointer()
    assert group.links[0].to_node.as_pointer() == sink.as_pointer()


@pytest.mark.parametrize('source', ['output(value=1)', 'x = input_float("X")', 'x = ghost_variable'])
def test_direct_compose_rejects_old_root_contract_and_implicit_bindings(source):
    """Unsupported root-interface or undeclared parameters fail before mutation."""
    group = bpy.data.node_groups.new('NFTest_direct_invalid', 'GeometryNodeTree')
    user = group.nodes.new('GeometryNodeMeshCube')
    with pytest.raises(CompileError):
        compiler.compose_source_initial(source, group)
    assert tuple(group.nodes) == (user,)


def test_direct_compose_requires_geometry_node_tree():
    """Invalid targets cannot produce a new generated root group."""
    with pytest.raises(CompileError, match='existing base GeometryNodeTree'):
        compiler.compose_source_initial('a = cube(1)', None)
