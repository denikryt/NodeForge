"""Exercise unified source composition and copy detachment through Blender operators."""

import bpy
import pytest

from NodeForge import ui
from NodeForge import compiler

pytestmark = pytest.mark.blender


@pytest.fixture
def editor_context():
    """Configure a real Geometry Nodes editor and selected owner in headless Blender."""
    registered_here = not hasattr(bpy.types.Scene, 'gn_script_mvp')
    if registered_here:
        ui.register()
    area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
    obj = bpy.data.objects.new('NFUI_owner', bpy.data.meshes.new('NFUI_mesh'))
    bpy.context.collection.objects.link(obj)
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    group = bpy.data.node_groups.new('NFUI_base', 'GeometryNodeTree')
    modifier = obj.modifiers.new('NFUI_nodes', 'NODES')
    modifier.node_group = group
    previous_type = area.type
    area.type = 'NODE_EDITOR'
    area.ui_type = 'GeometryNodeTree'
    area.spaces.active.node_tree = group
    text = bpy.data.texts.new('NFUI_source.nf')
    bpy.context.scene.gn_script_mvp.text_block = text
    try:
        yield area, group, text
    finally:
        bpy.context.scene.gn_script_mvp.text_block = None
        bpy.data.texts.remove(text)
        area.type = previous_type
        bpy.data.objects.remove(obj, do_unlink=True)
        if group.name in bpy.data.node_groups:
            bpy.data.node_groups.remove(group, do_unlink=True)
        if registered_here:
            ui.unregister()


def test_compile_operator_inserts_in_existing_base_without_new_root(editor_context):
    """Registered Compile Script preserves user links and embeds a function call."""
    area, group, text = editor_context
    a = group.nodes.new('GeometryNodeMeshCube')
    b = group.nodes.new('GeometryNodeTransform')
    group.links.new(a.outputs['Mesh'], b.inputs['Geometry'])
    text.write('''before = cube(1)
result = shift(before)
def shift(g: Geometry) -> Geometry:
    return transform(g, translation=vector(0, 0, 1))
after = cube(2)
''')
    group_pointer = group.as_pointer()
    with bpy.context.temp_override(area=area):
        assert bpy.ops.gn_script_mvp.compile_expression() == {'FINISHED'}
    assert group.as_pointer() == group_pointer
    assert len(group.links) >= 1
    assert any(node.as_pointer() == a.as_pointer() for node in group.nodes)
    assert any(node.as_pointer() == b.as_pointer() for node in group.nodes)
    assert sum(node.bl_idname == 'GeometryNodeGroup' for node in group.nodes) == 1
    assert sum(node.bl_idname == 'GeometryNodeMeshCube' for node in group.nodes) >= 3


def test_compile_operator_rejects_invalid_target_without_publication(editor_context):
    """The registered operator cannot silently create a generated root group."""
    area, group, text = editor_context
    text.write('x = cube(1)')
    area.spaces.active.node_tree = None
    before = {item.as_pointer() for item in bpy.data.node_groups}
    with bpy.context.temp_override(area=area):
        with pytest.raises(RuntimeError, match='Open a base Geometry Nodes tree'):
            bpy.ops.gn_script_mvp.compile_expression()
    assert {item.as_pointer() for item in bpy.data.node_groups} == before
    assert len(group.nodes) == 0


def test_detach_selected_copy_operator_preserves_canonical_group(editor_context):
    """Detachment is explicit, copy-directed and does not touch the original."""
    area, tree, text = editor_context
    compiler.compose_source_initial('''def shape(size: Float = 1.0) -> Geometry:
    return cube(size)
''', tree)
    original_node = next(node for node in tree.nodes if node.bl_idname == 'GeometryNodeGroup')
    canonical = original_node.node_tree
    duplicate = canonical.copy()
    other = tree.nodes.new('GeometryNodeGroup')
    other.node_tree = duplicate
    for node in tree.nodes:
        node.select = False
    other.select = True
    tree.nodes.active = other
    with bpy.context.temp_override(area=area):
        assert bpy.ops.nodeforge.detach_selected_source_owner() == {'FINISHED'}
    assert original_node.node_tree is canonical
    assert other.node_tree is duplicate
    assert 'nodeforge_function_definition_owner' not in duplicate
    assert 'nodeforge_function_definition_owner' in canonical


def test_selected_module_reload_operator_from_bound_text(editor_context):
    """Actual Compile Script Text binding survives an in-place selected group Reload."""
    area, group, text = editor_context
    text.write('''def bump(x: Float = 1.0) -> Float:
    return x + 1
''')
    with bpy.context.temp_override(area=area):
        assert bpy.ops.gn_script_mvp.compile_expression() == {'FINISHED'}
    node = next(node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup')
    for candidate in group.nodes:
        candidate.select = False
    node.select = True
    group.nodes.active = node
    pointer = node.node_tree.as_pointer()
    assert node.node_tree.get('nodeforge_module_definition')
    text.clear()
    text.write('''def bump(x: Float = 1.0) -> Float:
    return x + 6
''')
    with bpy.context.temp_override(area=area):
        assert bpy.ops.nodeforge.reload_selected_library_group.poll()
        assert bpy.ops.nodeforge.reload_selected_library_group() == {'FINISHED'}
    assert node.node_tree.as_pointer() == pointer
    assert 'x + 6' in str(node.node_tree['nodeforge_library_source'])
