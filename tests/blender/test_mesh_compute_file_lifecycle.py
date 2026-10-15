"""Real Blender lifecycle checks for persisted Mesh Compute results and teardown."""

from __future__ import annotations

import bpy
import pytest

from NodeForge.compiler import create_mesh_compute_modifier
from NodeForge.blender import mesh_compute_runtime as runtime
from NodeForge.tests.blender.test_mesh_compute_modifier_binding import (
    SOURCE, configured_local, mesh_object, _evaluated_positions,
)

pytestmark = pytest.mark.blender


def test_file_load_invalidates_only_verified_proxy_and_preserves_saved_mesh(configured_local, mesh_object):
    """A saved result cannot survive as a live Python output after callback loss."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    saved_result = binding.prepared.result_proxy.data
    saved_user_copy = saved_result.copy()
    proxy = binding.prepared.result_proxy
    private_scene = binding.source_scene
    private_source = binding.source_object
    try:
        assert len(saved_result.vertices) == 3
        runtime.on_file_load_post()
        assert runtime.saved_runtime_status(mesh_object) == 'MISSING_RUNTIME'
        assert len(proxy.data.vertices) == 0
        assert proxy.data is not saved_result
        assert len(saved_result.vertices) == 3
        assert len(saved_user_copy.vertices) == 3
        with pytest.raises(ValueError, match='active trusted'):
            runtime.get_modifier_binding(mesh_object)
        evaluated = mesh_object.evaluated_get(bpy.context.evaluated_depsgraph_get())
        out = evaluated.to_mesh()
        try:
            assert len(out.vertices) == 0
        finally:
            evaluated.to_mesh_clear()
    finally:
        # A genuine file reopen invalidates old ID pointers; the test fixture
        # only simulates handler lifecycle and therefore cleans old session IDs.
        if any(x is private_source for x in bpy.data.objects):
            bpy.data.objects.remove(private_source, do_unlink=True)
        if any(x is private_scene for x in bpy.data.scenes):
            bpy.data.scenes.remove(private_scene, do_unlink=True)
        if any(x is saved_user_copy for x in bpy.data.meshes):
            bpy.data.meshes.remove(saved_user_copy)
        runtime._MISSING_SAVED_RUNTIME.clear()
        runtime.unregister_runtime_handlers()


def test_file_load_preserves_ambiguous_duplicate_owner_metadata(configured_local, mesh_object):
    """Metadata copies must not authorize overwriting a user's live Mesh."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    private_scene, private_source = binding.source_scene, binding.source_object
    old = binding.prepared.result_proxy.data
    user_copy = binding.group.copy()  # Blender copies root owner ID and manifest.
    try:
        with pytest.warns(RuntimeWarning, match='ambiguous'):
            runtime.on_file_load_post()
        assert binding.prepared.result_proxy.data is old
        assert len(old.vertices) == 3
        assert runtime.saved_runtime_status(mesh_object) is None
    finally:
        bpy.data.node_groups.remove(user_copy, do_unlink=True)
        if any(x is private_source for x in bpy.data.objects):
            bpy.data.objects.remove(private_source, do_unlink=True)
        if any(x is private_scene for x in bpy.data.scenes):
            bpy.data.scenes.remove(private_scene, do_unlink=True)
        runtime.unregister_runtime_handlers()


def test_unregistration_clears_live_output_and_does_not_delete_source_mesh(configured_local, mesh_object):
    """Disabling addon stops Python callbacks while retaining user-owned IDs."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    source_mesh = mesh_object.data
    runtime.shutdown_runtime_bindings()
    assert not runtime._ACTIVE_BINDINGS
    assert len(binding.prepared.result_proxy.data.vertices) == 0
    assert mesh_object.data is source_mesh
    assert not any(scene is binding.source_scene for scene in bpy.data.scenes)
    assert runtime._on_depsgraph_update_pre not in bpy.app.handlers.depsgraph_update_pre
    with pytest.raises(ValueError, match='active trusted'):
        runtime.get_modifier_binding(mesh_object)


def test_registration_does_not_invalidate_active_binding(configured_local, mesh_object):
    """Re-registering the load handler must preserve currently active callbacks."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    old = binding.prepared.result_proxy.data
    runtime.register_runtime_load_handler()
    try:
        assert runtime.get_modifier_binding(mesh_object) is binding
        assert binding.prepared.result_proxy.data is old
        assert len(old.vertices) == 3
    finally:
        runtime.unregister_runtime_load_handler()


def test_saved_root_rebind_restores_output_without_recreating_modifier(configured_local, mesh_object):
    """After reopening, trusted Reload binds a new private root and recomputes."""
    from NodeForge.compiler import update_mesh_compute_modifier

    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    private_scene, private_source = binding.source_scene, binding.source_object
    original_modifier = mesh_object.modifiers[0].as_pointer()
    retained_mesh = binding.prepared.result_proxy.data
    try:
        runtime.on_file_load_post()
        assert runtime.saved_runtime_status(mesh_object) == 'MISSING_RUNTIME'
        assert len(binding.prepared.result_proxy.data.vertices) == 0
        group = update_mesh_compute_modifier(SOURCE, mesh_object)
        assert group is not binding.group
        assert mesh_object.modifiers[0].as_pointer() == original_modifier
        assert runtime.saved_runtime_status(mesh_object) is None
        runtime.evaluate_modifier_binding(mesh_object)
        assert runtime.get_modifier_binding(mesh_object).group is group
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
        assert len(retained_mesh.vertices) == 3
    finally:
        if any(x is private_source for x in bpy.data.objects):
            bpy.data.objects.remove(private_source, do_unlink=True)
        if any(x is private_scene for x in bpy.data.scenes):
            bpy.data.scenes.remove(private_scene, do_unlink=True)
        runtime._MISSING_SAVED_RUNTIME.clear()


def test_failed_saved_root_rebind_restores_empty_result_and_missing_status(configured_local, mesh_object, monkeypatch):
    """A failed trusted Reload cannot expose an untrusted persisted result."""
    from NodeForge.compiler import update_mesh_compute_modifier

    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    private_scene, private_source = binding.source_scene, binding.source_object
    runtime.on_file_load_post()
    modifier = mesh_object.modifiers[0]
    old_group, old_empty_mesh = modifier.node_group, binding.prepared.result_proxy.data
    assert runtime.saved_runtime_status(mesh_object) == 'MISSING_RUNTIME'

    def reject(*args, **kwargs):
        """Fail exactly after migration has swapped the modifier group."""
        raise RuntimeError('injected saved-root binding failure')

    monkeypatch.setattr(runtime, 'prepare_modifier_binding', reject)
    try:
        with pytest.raises(RuntimeError, match='injected saved-root binding failure'):
            update_mesh_compute_modifier(SOURCE, mesh_object)
        assert modifier.node_group is old_group
        assert binding.prepared.result_proxy.data is old_empty_mesh
        assert len(old_empty_mesh.vertices) == 0
        assert runtime.saved_runtime_status(mesh_object) == 'MISSING_RUNTIME'
        with pytest.raises(ValueError, match='active trusted'):
            runtime.get_modifier_binding(mesh_object)
    finally:
        if any(x is private_source for x in bpy.data.objects):
            bpy.data.objects.remove(private_source, do_unlink=True)
        if any(x is private_scene for x in bpy.data.scenes):
            bpy.data.scenes.remove(private_scene, do_unlink=True)
        runtime._MISSING_SAVED_RUNTIME.clear()
        runtime.unregister_runtime_handlers()
