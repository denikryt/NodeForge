"""Real UI render-cancellation lifecycle regression for Mesh Compute.

Run in non-background Blender through Xvfb. Cycles starts a modal render; an
X11 Escape event cancels the job. This tests an actual `render_cancel` event,
not an invocation of the handler list by a test.

Write JSON to NODEFORGE_MESH_COMPUTE_RENDER_CANCEL_REPORT when configured.
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
import warnings

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from NodeForge import local_sources
from NodeForge.blender import mesh_compute_runtime as runtime
from NodeForge.compiler import create_mesh_compute_modifier


_STATE = {'cancelled': False, 'escape_sent_during_render': False}


def _on_render_cancel(_scene):
    """Record Blender's real cancellation event."""
    _STATE['cancelled'] = True


def _write_result(passed: bool, **details):
    """Persist the run outcome, including failure conditions, for automation."""
    result = {'pass': passed, **details}
    path = os.environ.get('NODEFORGE_MESH_COMPUTE_RENDER_CANCEL_REPORT')
    if path:
        Path(path).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('MESH_COMPUTE_RENDER_CANCEL_UI_' + ('PASS' if passed else 'FAIL'), flush=True)


def _send_escape():
    """Send the real render-window Escape shortcut via the XTest extension."""
    try:
        _STATE['escape_sent_during_render'] = bpy.app.is_job_running('RENDER')
        x11 = ctypes.CDLL('libX11.so.6')
        xtst = ctypes.CDLL('libXtst.so.6')
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        display = x11.XOpenDisplay(None)
        if not display:
            raise RuntimeError('Cannot access X11 display for render cancellation')
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        escape_keycode = x11.XKeysymToKeycode(display, 0xff1b)
        xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeKeyEvent(display, escape_keycode, 1, 0)
        xtst.XTestFakeKeyEvent(display, escape_keycode, 0, 0)
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XFlush(display)
    except BaseException:
        _STATE['failure'] = traceback.format_exc()
    bpy.app.timers.register(_verify, first_interval=2.0)
    return None


def _verify():
    """Check the cancelled render left one valid binding and no leaked guard."""
    try:
        owner = _STATE['owner']
        binding = runtime.get_modifier_binding(owner)
        assert _STATE['escape_sent_during_render'], 'render finished before Escape'
        assert _STATE['cancelled'], 'render_cancel handler never fired'
        assert not bpy.app.is_job_running('RENDER')
        assert binding is _STATE['binding'] and binding.status == 'READY'
        assert not runtime._SCHEDULER_RUNNING
        assert binding.last_published is _STATE['published']
        assert sum(cb is runtime._on_render_pre for cb in bpy.app.handlers.render_pre) == 1
        runtime._on_render_pre(bpy.context.scene)
        assert binding.status == 'READY' and binding.last_published is _STATE['published']
        bpy.context.scene.render.use_lock_interface = False
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter('always')
            runtime._on_render_pre(bpy.context.scene)
        assert any('requires Lock Interface' in str(w.message) for w in emitted)
        assert binding.last_published is _STATE['published']
        _write_result(True, render_cancel_handler=True,
                      stage_after_cancel='READY', callback_reused=True)
    except BaseException:
        _write_result(False, traceback=traceback.format_exc(),
                      cancel_fired=_STATE['cancelled'],
                      during_render=_STATE['escape_sent_during_render'])
    bpy.ops.wm.quit_blender()
    return None


def _start_render():
    """Create a private Mesh Compute modifier and begin a modal Cycles render."""
    try:
        base = Path(tempfile.mkdtemp(prefix='nodeforge_render_cancel_'))
        root = base / 'Local'
        root.mkdir()
        local_sources._default_local_catalog_dir = lambda: root
        local_sources._local_sources_registry_path = lambda: base / 'registry.json'
        local_sources.set_local_python_permission(root, allowed=True)
        (root / 'shift.py').write_text(
            'from NodeForge import mesh_compute\n'
            '@mesh_compute(writes=("height",))\n'
            'def shift(mesh, amount: float = 2.):\n'
            '    p = list(mesh.positions)\n'
            '    for i in range(2, len(p), 3):\n'
            '        p[i] += amount\n'
            '    return {"positions": p, "point_attributes": {"height": [amount] * mesh.vertex_count}}\n',
            encoding='utf-8',
        )
        mesh = bpy.data.meshes.new('NFRenderCancelMesh')
        mesh.from_pydata([(0, 0, 0), (1, 0, 1), (0, 1, 2)], [], [(0, 1, 2)])
        owner = bpy.data.objects.new('NFRenderCancelOwner', mesh)
        bpy.context.scene.collection.objects.link(owner)
        source = ('from local import shift\n'
                  'geo = input_geometry("Geometry")\n'
                  'geo = shift(geo, amount=2.)\n'
                  'output("Geometry", geo)\n')
        create_mesh_compute_modifier(source, owner)
        runtime.evaluate_modifier_binding(owner)
        binding = runtime.get_modifier_binding(owner)
        assert binding.status == 'READY'
        _STATE.update(owner=owner, binding=binding, published=binding.last_published)
        scene = bpy.context.scene
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = 2048
        scene.render.resolution_x = 256
        scene.render.resolution_y = 256
        scene.render.resolution_percentage = 100
        scene.render.use_lock_interface = True
        bpy.app.handlers.render_cancel.append(_on_render_cancel)
        assert bpy.ops.render.render('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
        bpy.app.timers.register(_send_escape, first_interval=0.6)
    except BaseException:
        _write_result(False, traceback=traceback.format_exc())
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(_start_render, first_interval=0.3)
