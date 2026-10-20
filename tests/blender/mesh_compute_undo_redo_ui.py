"""Actual Blender UI-event-loop Undo/Redo regression for Mesh Compute.

Run with xvfb-run and Blender's normal (non-background) windowing mode:

    xvfb-run -a blender --factory-startup -noaudio --python \
      tests/blender/mesh_compute_undo_redo_ui.py

The test starts on a UI timer: Blender disallows `bpy.ops.ed.undo` while
loading the startup Python script. Write a JSON report when
`NODEFORGE_MESH_COMPUTE_HISTORY_REPORT` is set in the environment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from NodeForge import local_sources
from NodeForge.blender import mesh_compute_runtime as runtime
from NodeForge.compiler import create_mesh_compute_modifier, update_mesh_compute_modifier


SOURCE = (
    'from local import shift\n'
    'geo = input_geometry("Geometry")\n'
    'amount = input_float("Amount", default=2.0)\n'
    'geo = shift(geo, amount=amount)\n'
    'output("Geometry", geo)\n'
)


def _visible_vertex_count(owner) -> int:
    """Inspect the actual GN modifier output, never just the proxy pointer."""
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj = owner.evaluated_get(depsgraph)
    mesh = obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        return len(mesh.vertices)
    finally:
        obj.to_mesh_clear()


def _run_history_check() -> dict:
    """Verify genuine UI Undo/Redo fail closed, then explicit trusted recovery."""
    base = Path(tempfile.mkdtemp(prefix='nodeforge_history_ui_'))
    local_root = base / 'Local'
    local_root.mkdir()
    local_sources._default_local_catalog_dir = lambda: local_root
    local_sources._local_sources_registry_path = lambda: base / 'registry.json'

    (local_root / 'shift.py').write_text(
        'from NodeForge import mesh_compute\n'
        '@mesh_compute(writes=("height",))\n'
        'def shift(mesh, amount: float = 2.0):\n'
        '    positions = list(mesh.positions)\n'
        '    for i in range(2, len(positions), 3):\n'
        '        positions[i] += amount\n'
        '    return {"positions": positions, "point_attributes": '
        '{"height": [amount] * mesh.vertex_count}}\n', encoding='utf-8',
    )
    runtime.register_runtime_load_handler()
    assert runtime._on_history_restored in bpy.app.handlers.undo_post
    assert runtime._on_history_restored in bpy.app.handlers.redo_post

    mesh = bpy.data.meshes.new('NFHistoryProbeMesh')
    mesh.from_pydata([(0, 0, 0), (1, 0, 1), (0, 1, 2)], [], [(0, 1, 2)])
    owner = bpy.data.objects.new('NFHistoryProbeOwner', mesh)
    bpy.context.scene.collection.objects.link(owner)
    create_mesh_compute_modifier(SOURCE, owner)
    runtime.evaluate_modifier_binding(owner)
    assert _visible_vertex_count(owner) == 3
    assert runtime.get_modifier_binding(owner).status == 'READY'
    assert bpy.ops.ed.undo_push(message='Mesh Compute before parameter edit') == {'FINISHED'}
    modifier = owner.modifiers[0]
    socket = next(item for item in modifier.node_group.interface.items_tree
                  if item.item_type == 'SOCKET' and item.in_out == 'INPUT'
                  and item.name == 'Amount')
    getattr(modifier.properties.inputs, socket.identifier).value = 5.0
    owner.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()
    assert runtime.get_modifier_binding(owner).status == 'READY'
    assert bpy.ops.ed.undo_push(message='Mesh Compute after parameter edit') == {'FINISHED'}

    assert bpy.ops.ed.undo() == {'FINISHED'}
    restored = bpy.data.objects.get('NFHistoryProbeOwner')
    assert restored is not None
    assert runtime.saved_runtime_status(restored) == 'MISSING_RUNTIME'
    assert _visible_vertex_count(restored) == 0
    assert not runtime._ACTIVE_BINDINGS
    assert bpy.ops.ed.redo() == {'FINISHED'}
    restored = bpy.data.objects.get('NFHistoryProbeOwner')
    assert restored is not None
    assert runtime.saved_runtime_status(restored) == 'MISSING_RUNTIME'
    assert _visible_vertex_count(restored) == 0
    assert not runtime._ACTIVE_BINDINGS

    # An explicit trusted Reload is the supported recovery after undo/redo.
    assert len(restored.modifiers) == 1
    update_mesh_compute_modifier(SOURCE, restored)
    runtime.evaluate_modifier_binding(restored)
    assert runtime.get_modifier_binding(restored).status == 'READY'
    assert _visible_vertex_count(restored) == 3
    assert len(restored.modifiers) == 1
    return {'pass': True, 'undo': 'MISSING_RUNTIME/empty',
            'redo': 'MISSING_RUNTIME/empty', 'reload': 'READY/3 vertices'}


def _run_in_ui():
    """Execute after startup, persist the outcome and quit Blender."""
    try:
        report = _run_history_check()
        print('MESH_COMPUTE_UNDO_REDO_UI_PASS', flush=True)
    except BaseException as exc:
        report = {'pass': False, 'error': f'{type(exc).__name__}: {exc}',
                  'traceback': traceback.format_exc()}
        traceback.print_exc()
    report_path = os.environ.get('NODEFORGE_MESH_COMPUTE_HISTORY_REPORT')
    if report_path:
        Path(report_path).write_text(json.dumps(report, indent=2), encoding='utf-8')
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(_run_in_ui, first_interval=0.3)
