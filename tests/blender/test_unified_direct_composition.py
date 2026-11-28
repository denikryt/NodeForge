"""Initial direct composition uses an existing user GeometryNodeTree target."""

from helpers import *


def test_direct_compose_mixed_forward_decl_into_existing_base_group():
    """Functions and ordinary level-zero geometry nodes coexist without root I/O."""
    target = bpy.data.node_groups.new('NFTest_direct_mixed', 'GeometryNodeTree')
    user = target.nodes.new('GeometryNodeMeshCube')
    before_groups = {group.as_pointer() for group in bpy.data.node_groups}
    result = compile_root('''a = cube(1)
b = raise_level(a)
def raise_level(geometry: Geometry) -> Geometry:
    return transform(geometry, translation=vector(0, 0, 1))
extra = cube(2)
''', target)
    assert result.outcome == 'ROOT_REPLACEMENT' and result.target is target
    assert all(node.as_pointer() != user.as_pointer() for node in target.nodes)
    assert not any(node.bl_idname in {'NodeGroupInput', 'NodeGroupOutput'} for node in target.nodes)
    groups = [node for node in target.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert len(groups) == 1
    assert groups[0].node_tree.get('nodeforge_module_name') == 'raise_level'
    assert len([node for node in target.nodes if node.bl_idname == 'GeometryNodeMeshCube']) == 2
    assert {group.as_pointer() for group in bpy.data.node_groups} - before_groups == {groups[0].node_tree.as_pointer()}


def test_direct_compose_preserves_preexisting_base_links_and_interface():
    """Direct lowering must not replace or clear user-owned interface or links."""
    group = bpy.data.node_groups.new('NFTest_direct_user_links', 'GeometryNodeTree')
    source = group.nodes.new('GeometryNodeMeshCube')
    sink = group.nodes.new('GeometryNodeTransform')
    group.links.new(source.outputs['Mesh'], sink.inputs['Geometry'])
    interface_before = tuple((socket.name, socket.in_out) for socket in group.interface.items_tree)
    compile_root('a = cube(1)\nb = cube(2)\n', group)
    assert tuple((socket.name, socket.in_out) for socket in group.interface.items_tree) == interface_before
    assert len(group.links) == 0
    assert all(node.as_pointer() not in (source.as_pointer(), sink.as_pointer())
               for node in group.nodes)


@pytest.mark.parametrize('source', ['output(value=1)', 'x = input_float("X")', 'x = ghost_variable'])
def test_direct_compose_rejects_old_root_contract_and_implicit_bindings(source):
    """Unsupported root-interface or undeclared parameters fail before mutation."""
    group = bpy.data.node_groups.new('NFTest_direct_invalid', 'GeometryNodeTree')
    user = group.nodes.new('GeometryNodeMeshCube')
    with pytest.raises(CompileError):
        compile_root(source, group)
    assert tuple(group.nodes) == (user,)


def test_direct_compose_requires_geometry_node_tree():
    """Invalid targets cannot produce a new generated root group."""
    with pytest.raises(CompileError, match='base Geometry Nodes tree'):
        compile_root('a = cube(1)', None)


def test_definition_only_entries_respect_module_compile_time_condition():
    """A compile-time-disabled reverse call does not prevent nested materialization."""
    target = bpy.data.node_groups.new('NFTest_module_ct_entry', 'GeometryNodeTree')
    compile_root('''FLAG = False

def a() -> Geometry:
    if FLAG:
        b()
    return cube(1)

def b() -> Geometry:
    return a()
''', target)
    placed, = target.nodes
    assert placed.node_tree == published_entry(target, 'b')


def test_explicit_int_runtime_range_count_entry_uses_int_socket():
    """A definition-only range(count) materializes as an Int Repeat input."""
    target = bpy.data.node_groups.new('NFTest_typed_range', 'GeometryNodeTree')
    compile_root('''def make_count(count: Int):
    x = 1.0
    for i in range(count):
        x = x + 1.0
    return x
''', target)
    placed, = target.nodes
    entry = published_entry(target, 'make_count')
    assert placed.node_tree == entry
    assert any(item.name == 'count' and item.socket_type == 'NodeSocketInt'
               for item in entry.interface.items_tree if item.item_type == 'SOCKET')
    assert any(node.bl_idname == 'GeometryNodeRepeatInput' for node in entry.nodes)


def test_explicit_int_runtime_range_count_standalone_add():
    """Standalone Add uses declared Int without inferring types from the loop."""
    from NodeForge.compiler_identities import ModuleId
    from NodeForge.semantic.source_modules import parse_source_module
    from NodeForge.compiler import _materialize_standalone_module_definition
    source = '''def count_and_add(count: Int):
    x = 1.0
    for i in range(count):
        x = x + 1.0
    return x
'''
    snapshot = parse_source_module(source, ModuleId('ROOT_TEXT', 'test-typed-standalone'))
    group = _materialize_standalone_module_definition(
        snapshot, snapshot.definition_ref('count_and_add'), compiler._new_group_backend()
    )
    assert any(item.name == 'count' and item.socket_type == 'NodeSocketInt'
               for item in group.interface.items_tree if item.item_type == 'SOCKET')
