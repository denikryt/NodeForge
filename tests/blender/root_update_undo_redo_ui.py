"""Live UI-event-loop acceptance for root Update, Undo/Redo and partial-error Undo.

Run with xvfb-run and Blender's normal window mode; Undo requires a UI timer.
Set NODEFORGE_ROOT_UPDATE_HISTORY_REPORT to collect a JSON report.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import traceback

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from NodeForge import ui
from NodeForge.blender import root_update


def _types(name):
    """Inspect ordinary physical node families after restoring undo history."""
    root = bpy.data.node_groups[name]
    return [node.bl_idname for node in root.nodes
            if node.bl_idname not in {'NodeGroupInput', 'NodeGroupOutput'}]


def _run_history():
    """Exercise registered operators via the live Blender editor and Undo stack."""
    ui.register()
    bpy.ops.ed.undo_push(message='Initial UI history anchor')
    name = 'NF_Root_Update_History'
    area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
    obj = bpy.data.objects.new('NF_Root_Update_History_Owner', bpy.data.meshes.new('NF_Root_Update_History_Mesh'))
    bpy.context.collection.objects.link(obj)
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    root = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    root.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    root.nodes.new('NodeGroupOutput')
    root.nodes.new('GeometryNodeMeshCube')
    mod = obj.modifiers.new('Nodes', 'NODES')
    mod.node_group = root
    text = bpy.data.texts.new('NF_Root_Update_History.nf')
    text.from_string('a = cube(2)\nb = cube(3)')
    bpy.context.scene.gn_script_mvp.text_block = text
    area.type = 'NODE_EDITOR'
    area.ui_type = 'GeometryNodeTree'
    area.spaces.active.node_tree = root
    assert _types(name) == ['GeometryNodeMeshCube']
    with bpy.context.temp_override(area=area):
        assert bpy.ops.ed.undo_push(message='Before root Update') == {'FINISHED'}
        assert bpy.ops.gn_script_mvp.update_expression() == {'FINISHED'}
        assert _types(name) == ['GeometryNodeMeshCube', 'GeometryNodeMeshCube']
        assert bpy.ops.ed.undo_push(message='After root Update') == {'FINISHED'}
        assert bpy.ops.ed.undo() == {'FINISHED'}
        assert _types(name) == ['GeometryNodeMeshCube'], _types(name)
        assert bpy.ops.ed.redo() == {'FINISHED'}
        assert _types(name) == ['GeometryNodeMeshCube', 'GeometryNodeMeshCube']

    # Trigger a partial error after destructive cutover, disallow root restore.
    def fail_reconnect(*_args, **_kwargs):
        """Inject failure immediately after destructive cutover."""
        raise RuntimeError('injected late UI root failure')

    def fail_restore(*_args, **_kwargs):
        """Leave the root partially changed for Undo verification."""
        raise RuntimeError('injected incomplete root restore')

    previous_reconnect = root_update._reconnect_boundary
    previous_restore = root_update._copy_missing_old_nodes
    root_update._reconnect_boundary = fail_reconnect
    root_update._copy_missing_old_nodes = fail_restore
    try:
        with bpy.context.temp_override(area=area):
            assert bpy.ops.ed.undo_push(message='Before late root failure') == {'FINISHED'}
            try:
                bpy.ops.gn_script_mvp.update_expression()
            except RuntimeError as error:
                assert 'injected late UI root failure' in str(error)
            else:
                raise AssertionError('An ERROR report must propagate through bpy.ops')
            assert _types(name) == [], _types(name)
            assert bpy.ops.ed.undo() == {'FINISHED'}
            assert _types(name) == ['GeometryNodeMeshCube', 'GeometryNodeMeshCube'], _types(name)
    finally:
        root_update._reconnect_boundary = previous_reconnect
        root_update._copy_missing_old_nodes = previous_restore
    return {'pass': True, 'normal_undo': True, 'normal_redo': True,
            'partial_error_undo': True}


def _on_timer():
    """Run after the window manager loads, write results and close Blender."""
    try:
        report = _run_history()
        print('ROOT_UPDATE_UNDO_REDO_UI_PASS', flush=True)
    except BaseException as exc:
        report = {'pass': False, 'error': f'{type(exc).__name__}: {exc}',
                  'traceback': traceback.format_exc()}
        traceback.print_exc()
    filename = os.environ.get('NODEFORGE_ROOT_UPDATE_HISTORY_REPORT')
    if filename:
        Path(filename).write_text(json.dumps(report, indent=2), encoding='utf-8')
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(_on_timer, first_interval=0.3)
