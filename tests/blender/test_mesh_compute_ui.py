"""Exercise the actual Blender UI operators for Local Python and Mesh Compute."""

from __future__ import annotations

import bpy
import pytest

from NodeForge import local_sources, ui
from NodeForge.blender import mesh_compute_runtime as runtime
from NodeForge.tests.blender.test_mesh_compute_modifier_binding import (
    SOURCE, configured_local, mesh_object, _evaluated_positions,
)

pytestmark = pytest.mark.blender


@pytest.fixture
def registered_ui():
    """Provide operator and Scene property registration without double-registering."""
    owned = not hasattr(bpy.types.Scene, "gn_script_mvp")
    if owned:
        ui.register()
    yield
    if owned:
        ui.unregister()


def _select(owner):
    """Set a deterministic active Object in headless Blender."""
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    owner.select_set(True)
    bpy.context.view_layer.objects.active = owner


def test_compile_to_modifier_operator_evaluates_real_gn(registered_ui, configured_local, mesh_object):
    """UI compiles an explicitly trusted script and produces updated GN geometry."""
    text = bpy.data.texts.new("MeshComputeUI_positive.nf")
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        assert bpy.ops.nodeforge.compile_mesh_compute_modifier() == {'FINISHED'}
        assert len(mesh_object.modifiers) == 1
        binding = runtime.get_modifier_binding(mesh_object)
        assert binding.status == "READY"
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    finally:
        bpy.data.texts.remove(text)


def test_compile_to_modifier_operator_denies_untrusted_source(registered_ui, configured_local, mesh_object):
    """No group, scene or modifier can be published when Local Python is denied."""
    text = bpy.data.texts.new("MeshComputeUI_blocked.nf")
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        local_sources.set_local_python_permission(configured_local, allowed=False)
        before_groups = {x.as_pointer() for x in bpy.data.node_groups}
        before_scenes = {x.as_pointer() for x in bpy.data.scenes}
        with pytest.raises(RuntimeError, match='not allowed'):
            bpy.ops.nodeforge.compile_mesh_compute_modifier()
        assert not mesh_object.modifiers
        assert {x.as_pointer() for x in bpy.data.node_groups} == before_groups
        assert {x.as_pointer() for x in bpy.data.scenes} == before_scenes
    finally:
        bpy.data.texts.remove(text)


def test_local_permission_operator_grants_only_registered_root(registered_ui, configured_local, tmp_path):
    """UI toggles the durable Local grant and rejects an unregistered directory."""
    source = configured_local / 'shift.py'
    assert local_sources.local_python_permission(source)
    assert bpy.ops.nodeforge.set_local_python_permission(path=str(configured_local), allowed=False) == {'FINISHED'}
    assert not local_sources.local_python_permission(source)
    assert bpy.ops.nodeforge.set_local_python_permission(path=str(configured_local), allowed=True) == {'FINISHED'}
    assert local_sources.local_python_permission(source)
    foreign = tmp_path / 'unregistered'
    foreign.mkdir()
    with pytest.raises(RuntimeError, match='registered root'):
        bpy.ops.nodeforge.set_local_python_permission(path=str(foreign), allowed=True)
    assert local_sources.local_python_permission(source)


def test_mesh_compute_ui_poll_rejects_nonmesh_or_existing_modifier(registered_ui, configured_local, mesh_object):
    """UI never installs runtime onto a user-owned existing modifier stack."""
    text = bpy.data.texts.new('MeshComputeUI_disabled.nf')
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        modifier = mesh_object.modifiers.new('User Bevel', 'BEVEL')
        assert not ui.NODEFORGE_OT_compile_mesh_compute_modifier.poll(bpy.context)
        assert mesh_object.modifiers[0].as_pointer() == modifier.as_pointer()
        mesh_object.modifiers.remove(modifier)
        assert ui.NODEFORGE_OT_compile_mesh_compute_modifier.poll(bpy.context)
    finally:
        bpy.data.texts.remove(text)


def test_reload_compute_operator_evaluates_changed_local_source(registered_ui, configured_local, mesh_object):
    """UI Reload replaces callback on the same modifier and evaluates its output."""
    from NodeForge.tests.blender.test_mesh_compute_reload import _new_kernel_source
    text = bpy.data.texts.new("MeshComputeUI_Reload.nf")
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        assert bpy.ops.nodeforge.compile_mesh_compute_modifier() == {'FINISHED'}
        original_modifier = mesh_object.modifiers[0].as_pointer()
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
        (configured_local / 'shift.py').write_text(_new_kernel_source())
        assert ui.NODEFORGE_OT_reload_mesh_compute_modifier.poll(bpy.context)
        assert bpy.ops.nodeforge.reload_mesh_compute_modifier() == {'FINISHED'}
        assert mesh_object.modifiers[0].as_pointer() == original_modifier
        assert runtime.get_modifier_binding(mesh_object).status == "READY"
        assert _evaluated_positions(mesh_object)[0] == (4., 5., 6.)
    finally:
        bpy.data.texts.remove(text)


def test_reload_compute_operator_denies_unsupported_root_and_no_changes(registered_ui, configured_local, mesh_object):
    """Reload is unavailable on a non-runtime modifier or absent Text Block."""
    text = bpy.data.texts.new("MeshComputeUI_ReloadDenied.nf")
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        mesh_object.modifiers.new("User", "BEVEL")
        assert not ui.NODEFORGE_OT_reload_mesh_compute_modifier.poll(bpy.context)
        assert len(mesh_object.modifiers) == 1
        mesh_object.modifiers.clear()
        assert bpy.ops.nodeforge.compile_mesh_compute_modifier() == {'FINISHED'}
        bpy.context.scene.gn_script_mvp.text_block = None
        assert not ui.NODEFORGE_OT_reload_mesh_compute_modifier.poll(bpy.context)
    finally:
        bpy.data.texts.remove(text)


def test_reload_operator_recovers_missing_saved_runtime(registered_ui, configured_local, mesh_object):
    """Reload button is available after load invalidates the persisted GN output."""
    text = bpy.data.texts.new('MeshComputeUI_SavedReload.nf')
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        assert bpy.ops.nodeforge.compile_mesh_compute_modifier() == {'FINISHED'}
        previous = runtime.get_modifier_binding(mesh_object)
        old_source, old_scene = previous.source_object, previous.source_scene
        runtime.on_file_load_post()
        assert runtime.saved_runtime_status(mesh_object) == 'MISSING_RUNTIME'
        assert ui.NODEFORGE_OT_reload_mesh_compute_modifier.poll(bpy.context)
        assert bpy.ops.nodeforge.reload_mesh_compute_modifier() == {'FINISHED'}
        assert runtime.saved_runtime_status(mesh_object) is None
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    finally:
        if 'old_source' in locals() and any(obj is old_source for obj in bpy.data.objects):
            bpy.data.objects.remove(old_source, do_unlink=True)
        if 'old_scene' in locals() and any(scene is old_scene for scene in bpy.data.scenes):
            bpy.data.scenes.remove(old_scene, do_unlink=True)
        bpy.data.texts.remove(text)
