"""Real Node Editor operator checks for Rev5 selected-source/root eligibility."""

import bpy
import pytest

from NodeForge import ui
from NodeForge.blender import root_update


@pytest.fixture
def root_editor():
    """Provide a registered addon with a real UI area and editable root."""
    registered_here = not hasattr(bpy.types.Scene, 'gn_script_mvp')
    if registered_here:
        ui.register()
    area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
    previous = area.type
    obj = bpy.data.objects.new('NFR5UI_mesh_obj', bpy.data.meshes.new('NFR5UI_mesh'))
    bpy.context.collection.objects.link(obj)
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    root = bpy.data.node_groups.new('NFR5UI_root', 'GeometryNodeTree')
    modifier = obj.modifiers.new('Geometry', 'NODES')
    modifier.node_group = root
    area.type = 'NODE_EDITOR'
    area.ui_type = 'GeometryNodeTree'
    area.spaces.active.node_tree = root
    text = bpy.data.texts.new('NFR5UI_a.nf')
    bpy.context.scene.gn_script_mvp.text_block = text
    try:
        yield area, root, text
    finally:
        bpy.context.scene.gn_script_mvp.text_block = None
        area.type = previous
        bpy.data.objects.remove(obj, do_unlink=True)
        if root.name in bpy.data.node_groups:
            bpy.data.node_groups.remove(root, do_unlink=True)
        if text.name in bpy.data.texts:
            bpy.data.texts.remove(text)
        if registered_here:
            ui.unregister()


def test_operator_switches_selected_text_and_preserves_pinned_root(root_editor):
    """A→B Update reads the latest Text and targets pinned base datablock."""
    area, root, text_a = root_editor
    text_a.write('a = cube(1)\n')
    text_b = bpy.data.texts.new('NFR5UI_b.nf')
    try:
        pointer = root.as_pointer()
        area.spaces.active.pin = True
        with bpy.context.temp_override(area=area):
            assert bpy.ops.gn_script_mvp.compile_expression() == {'FINISHED'}
        assert sum(n.bl_idname == 'GeometryNodeMeshCube' for n in root.nodes) == 1
        bpy.context.scene.gn_script_mvp.text_block = text_b
        text_b.write('a = cube(3)\nb = cube(4)\n')
        with bpy.context.temp_override(area=area):
            assert bpy.ops.gn_script_mvp.compile_expression() == {'FINISHED'}
        assert root.as_pointer() == pointer
        assert sum(n.bl_idname == 'GeometryNodeMeshCube' for n in root.nodes) == 2
        assert 'REGISTER' in ui.GNSCRIPT_MVP_OT_compile_expression.bl_options
        assert 'UNDO' in ui.GNSCRIPT_MVP_OT_compile_expression.bl_options
        assert not any(key.startswith('nodeforge_root_text') for key in root.keys())
    finally:
        area.spaces.active.pin = False
        bpy.data.texts.remove(text_b)


def test_operator_rejects_missing_or_blank_text_without_editing_root(root_editor):
    """Neither missing nor blank Text creates root contents or an Undo step."""
    area, root, text = root_editor
    old = root.nodes.new('GeometryNodeMeshCube')
    pointer = old.as_pointer()
    for selected in (None, text):
        bpy.context.scene.gn_script_mvp.text_block = selected
        with bpy.context.temp_override(area=area):
            with pytest.raises(RuntimeError, match='Compile Script requires a nonblank selected Text source'):
                bpy.ops.gn_script_mvp.compile_expression()
        assert {node.as_pointer() for node in root.nodes} == {pointer}


def test_compile_and_update_both_reject_nested_edit_context(root_editor):
    """A nested editor target is never written even when base node_tree is valid."""
    from types import SimpleNamespace
    from NodeForge.errors import CompileError

    area, root, text = root_editor
    nested = bpy.data.node_groups.new('NFR5UI_nested', 'GeometryNodeTree')
    fake = SimpleNamespace(space_data=SimpleNamespace(type='NODE_EDITOR', node_tree=root, edit_tree=nested))
    with pytest.raises(CompileError, match='base Geometry Nodes tree'):
        ui._eligible_base_gn_tree(fake)
    fake.space_data.edit_tree = root
    assert ui._eligible_base_gn_tree(fake) is root


def test_operator_late_error_retains_undo_compatible_finished(root_editor, monkeypatch):
    """A post-cutover exception returns FINISHED rather than unsafe CANCELLED."""
    area, root, text = root_editor
    text.write('a = cube(2)')
    root.nodes.new('GeometryNodeMeshCube')

    def fail(*args, **kwargs):
        """Force cutover failure after old node deletion."""
        raise RuntimeError('NFR5 UI late failure')

    monkeypatch.setattr(root_update, '_reconnect_boundary', fail)
    class OperatorReport:
        """Collect reports without Blender translating ERROR reports into Python exceptions."""
        def __init__(self):
            """Prepare an in-memory operator report ledger."""
            self.messages = []
            self.expression = ''

        def report(self, levels, message):
            """Record Blender-style error levels and messages."""
            self.messages.append((levels, message))

    operator = OperatorReport()
    with bpy.context.temp_override(area=area):
        assert ui.GNSCRIPT_MVP_OT_compile_expression.execute(operator, bpy.context) == {'FINISHED'}
    assert any('NFR5 UI late failure' in msg for levels, msg in operator.messages)
    assert sum(node.bl_idname == 'GeometryNodeMeshCube' for node in root.nodes) == 1


def test_operator_compile_is_append_only_and_updated_root_replaces(root_editor):
    """Compile and Update share target eligibility but keep separate semantics."""
    area, root, text = root_editor
    text.write('a = cube(2)\n')
    with bpy.context.temp_override(area=area):
        assert bpy.ops.gn_script_mvp.compile_expression() == {'FINISHED'}
        assert bpy.ops.gn_script_mvp.compile_expression() == {'FINISHED'}
    assert sum(n.bl_idname == 'GeometryNodeMeshCube' for n in root.nodes) == 1
    assert ui.NODEFORGE_OT_detach_selected_source_owner in ui.classes
    assert ui.NODEFORGE_OT_reload_selected_library_group in ui.classes
    assert ui.NODEFORGE_OT_reload_mesh_compute_modifier in ui.classes


def test_wrong_node_editor_type_is_not_eligible(root_editor):
    """A GeometryNodeTree cached in a Shader editor cannot be compiled as a base."""
    from types import SimpleNamespace
    from NodeForge.errors import CompileError

    _, root, _ = root_editor
    fake = SimpleNamespace(space_data=SimpleNamespace(
        type='NODE_EDITOR', tree_type='ShaderNodeTree', node_tree=root, edit_tree=root,
    ))
    with pytest.raises(CompileError, match='Open a base Geometry Nodes tree'):
        ui._eligible_base_gn_tree(fake)


def test_panel_does_not_show_detach_but_recovery_and_reload_are_registered():
    """Only the Detach panel row disappears; the recovery operator remains callable."""
    import inspect

    draw = inspect.getsource(ui.GNSCRIPT_MVP_PT_panel.draw)
    assert 'NODEFORGE_OT_detach_selected_source_owner.bl_idname' not in draw
    assert 'GNSCRIPT_MVP_OT_update_expression.bl_idname' not in draw
    assert 'GNSCRIPT_MVP_OT_compile_expression.bl_idname' in draw
    assert 'NODEFORGE_OT_reload_selected_library_group.bl_idname' in draw
    assert ui.NODEFORGE_OT_detach_selected_source_owner in ui.classes


def test_operator_invalid_semantics_does_not_leave_new_text_identity(root_editor):
    """A preflight-only cancellation must undo the Text source-identity stamp."""
    from NodeForge.compiler_identities import TEXT_MODULE_CONTEXT_PROP

    area, tree, text = root_editor
    text.write('x = not_defined()')
    old = tree.nodes.new('GeometryNodeMeshCube')
    assert TEXT_MODULE_CONTEXT_PROP not in text
    with bpy.context.temp_override(area=area):
        with pytest.raises(RuntimeError, match='not_defined'):
            bpy.ops.gn_script_mvp.compile_expression()
    assert TEXT_MODULE_CONTEXT_PROP not in text
    assert len(tree.nodes) == 1 and tree.nodes[0].as_pointer() == old.as_pointer()


def test_operator_predetach_unrestored_error_keeps_undo_result(root_editor, monkeypatch):
    """S10: failing after I/O detach cannot return unsafe CANCELLED."""
    area, root, text = root_editor
    text.write('a = cube(2)')
    frame = root.nodes.new('NodeFrame')
    output = root.nodes.new('NodeGroupOutput')
    output.parent = frame
    root.nodes.new('GeometryNodeMeshCube')

    def fail_position(*args):
        """Stop after I/O is detached, and fail to restore its position."""
        raise RuntimeError('NFR5 UI detach failure')

    monkeypatch.setattr(root_update, '_place_absolute', fail_position)

    class OperatorReport:
        """Capture error reports without Blender raising for bpy.ops ERROR."""

        def __init__(self):
            """Initialize report capture."""
            self.messages = []
            self.expression = ''

        def report(self, levels, message):
            """Record diagnostic and classification."""
            self.messages.append((levels, message))

    operator = OperatorReport()
    with bpy.context.temp_override(area=area):
        assert ui.GNSCRIPT_MVP_OT_compile_expression.execute(operator, bpy.context) == {'FINISHED'}
    assert output.parent.as_pointer() == frame.as_pointer()
    assert any('structural I/O restoration:' in message for _, message in operator.messages)
