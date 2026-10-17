"""Blender 5.2 regressions for Direct publication and exact physical ID retirement."""

from __future__ import annotations

import bpy
import pytest

from NodeForge.compiler import create_mesh_compute_modifier
from NodeForge.blender import mesh_compute_runtime as runtime
from test_mesh_compute_modifier_binding import SOURCE, configured_local, mesh_object, _evaluated_positions

pytestmark = pytest.mark.blender


def test_publication_tag_failure_discards_installed_candidate_and_clears_geometry(
        configured_local, mesh_object, monkeypatch):
    """Failure after Object.data swap must not leak an unreferenced candidate Mesh."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    original_source = mesh_object.data
    before = {id_obj.as_pointer() for id_obj in bpy.data.meshes}
    new_candidate = []
    from NodeForge.blender.mesh_compute_transports import direct_mesh_update
    original_prepare = direct_mesh_update.prepare_publication

    def capture_candidate(*args, **kwargs):
        """Remember the physical Mesh created by the normal direct transport."""
        result = original_prepare(*args, **kwargs)
        new_candidate.append(result)
        return result

    def fail_after_swap(_group):
        """Simulate a Blender tagging exception after the proxy receives the Mesh."""
        raise RuntimeError('injected after Mesh pointer swap')

    monkeypatch.setattr(direct_mesh_update, 'prepare_publication', capture_candidate)
    monkeypatch.setattr(runtime, '_tag_published_group', fail_after_swap)
    with pytest.raises(RuntimeError, match='after Mesh pointer swap'):
        runtime.evaluate_modifier_binding(mesh_object)
    assert len(new_candidate) == 1
    assert binding.prepared.result_proxy.data is not new_candidate[0]
    assert not any(mesh is new_candidate[0] for mesh in bpy.data.meshes)
    assert len(binding.prepared.result_proxy.data.vertices) == 0
    assert binding.status == 'ERROR'
    assert mesh_object.data is original_source
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = mesh_object.evaluated_get(depsgraph)
    rendered = evaluated.to_mesh()
    try:
        assert len(rendered.vertices) == 0
    finally:
        evaluated.to_mesh_clear()
    assert len({mesh.as_pointer() for mesh in bpy.data.meshes} - before) <= 1


def test_retirement_error_preserves_new_committed_result(configured_local, mesh_object, monkeypatch):
    """Old-ID retirement failures after publication are warnings, never rollback."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    old_result = binding.last_published

    def fail_retire(_binding, _mesh):
        """Simulate a failed, nonauthoritative retirement cleanup."""
        raise RuntimeError('injected retirement failure')
    monkeypatch.setattr(runtime, '_retire_previous_published', fail_retire)
    with pytest.warns(RuntimeWarning, match='retained previous Mesh'):
        updated = runtime.evaluate_modifier_binding(mesh_object)
    assert updated is binding.prepared.result_proxy.data
    assert updated is binding.last_published and updated is not old_result
    assert binding.status == 'READY'
    assert any(mesh is old_result for mesh in bpy.data.meshes)
    assert _evaluated_positions(mesh_object) == ((2.0, 3.0, 4.0), (2.0, 2.0, 2.0), 1)


def test_previous_mesh_with_external_user_survives_publication(configured_local, mesh_object):
    """An old Mesh reused by a user Object is not owned exclusively by the runtime."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    old = binding.last_published
    other = bpy.data.objects.new('NodeForgeExternalMeshUser', old)
    bpy.context.scene.collection.objects.link(other)
    try:
        runtime.evaluate_modifier_binding(mesh_object)
        assert binding.last_published is not old
        assert other.data is old
        assert len(other.data.vertices) == 3
        assert _evaluated_positions(mesh_object)[0] == (2.0, 3.0, 4.0)
    finally:
        bpy.data.objects.remove(other, do_unlink=True)
        if any(mesh is old for mesh in bpy.data.meshes):
            bpy.data.meshes.remove(old)


def test_repeated_load_invalidation_does_not_allocate_more_empty_meshes(configured_local, mesh_object):
    """Repeated handler calls must keep the already-empty saved output gate."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    private_scene, private_source = binding.source_scene, binding.source_object
    try:
        runtime.on_file_load_post()
        first_empty = binding.prepared.result_proxy.data
        before = {mesh.as_pointer() for mesh in bpy.data.meshes}
        runtime.on_file_load_post()
        assert runtime.saved_runtime_status(mesh_object) == 'MISSING_RUNTIME'
        assert binding.prepared.result_proxy.data is first_empty
        assert {mesh.as_pointer() for mesh in bpy.data.meshes} == before
        assert len(first_empty.vertices) == 0
    finally:
        if any(obj is private_source for obj in bpy.data.objects):
            bpy.data.objects.remove(private_source, do_unlink=True)
        if any(scene is private_scene for scene in bpy.data.scenes):
            bpy.data.scenes.remove(private_scene, do_unlink=True)
        runtime._MISSING_SAVED_RUNTIME.clear()
        runtime.unregister_runtime_handlers()


def test_copied_resource_ref_cannot_be_retired_by_reused_physical_key(monkeypatch):
    """A replacement object at a recycled pointer cannot inherit prior ID authority."""
    from NodeForge.blender import generated_resources as resources

    original_mesh = bpy.data.meshes.new('NodeForgeProvenanceOriginal')
    duplicate = None
    try:
        tx = resources.GeneratedResourceTransaction('nodeforge-positive-owner')
        ref = tx.add(original_mesh, 'MESH', 'test-result')
        tx.mark_committed()
        duplicate = original_mesh.copy()
        duplicate.name = ref.name + '.copy'
        # Force metadata into the same serialized state while retaining a new ID.
        duplicate[resources.ID_METADATA_PROP] = original_mesh[resources.ID_METADATA_PROP]
        monkeypatch.setattr(resources, '_physical_identity',
                            lambda id_obj: original_mesh.as_pointer())
        captured = ((ref, duplicate),)
        assert resources._delete_captured_ref(ref, duplicate) is False
        resources.cleanup_previous_after_commit(tx.manifest(),
                                                resources.build_manifest(tx.owner_group_uuid, []),
                                                captured_ids=captured)
        assert any(mesh is duplicate for mesh in bpy.data.meshes)
    finally:
        if duplicate is not None and any(mesh is duplicate for mesh in bpy.data.meshes):
            bpy.data.meshes.remove(duplicate)
        resources._SESSION_COMMITTED_IDS.pop(original_mesh.as_pointer(), None)
        if any(mesh is original_mesh for mesh in bpy.data.meshes):
            bpy.data.meshes.remove(original_mesh)
