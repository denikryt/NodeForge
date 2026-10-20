"""Real Blender integration tests for Mesh Compute's synchronous update hooks."""

from __future__ import annotations

from dataclasses import replace

import bpy
import pytest

from NodeForge.compiler import create_mesh_compute_modifier
from NodeForge.blender import mesh_compute_runtime as runtime
from NodeForge.tests.blender.test_mesh_compute_modifier_binding import (
    SOURCE, configured_local, mesh_object, _evaluated_positions,
)

pytestmark = pytest.mark.blender


def _amount(owner, group, value):
    """Set the Blender 5.2 RNA modifier input, not legacy ID properties."""
    socket = next(item for item in group.interface.items_tree
                  if item.item_type == 'SOCKET' and item.in_out == 'INPUT'
                  and item.name == 'Amount')
    getattr(owner.modifiers[0].properties.inputs, socket.identifier).value = value


def _kernel_counter(binding):
    """Wrap a committed kernel with a counter to distinguish cache hits."""
    original = binding.prepared.kernel
    invoked = []

    def counted(*args, **kwargs):
        invoked.append(dict(kwargs))
        return original(*args, **kwargs)

    binding.prepared = replace(binding.prepared, kernel=counted)
    return invoked


def test_registered_hook_auto_evaluates_changed_modifier_scalar(configured_local, mesh_object):
    """Dependency updates propagate scalar values without a manual evaluation call."""
    group = create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    assert runtime._on_depsgraph_update_pre in bpy.app.handlers.depsgraph_update_pre
    bpy.context.view_layer.update()
    assert binding.status == 'READY'
    assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    _amount(mesh_object, group, 5.)
    mesh_object.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()
    assert binding.status == 'READY'
    assert _evaluated_positions(mesh_object)[0] == (5., 6., 7.)


def test_topology_only_update_republishes_without_invoking_kernel(configured_local, mesh_object):
    """Numerical key ignores connectivity but published Mesh does not."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    invoked = _kernel_counter(binding)
    bpy.context.view_layer.update()
    assert len(invoked) == 1
    old_mesh = binding.last_published
    coordinates = [tuple(vertex.co) for vertex in mesh_object.data.vertices]
    mesh_object.data.clear_geometry()
    mesh_object.data.from_pydata(coordinates, [(0, 1), (1, 2)], [])
    mesh_object.data.update()
    bpy.context.view_layer.update()
    assert len(invoked) == 1
    assert binding.last_published is not old_mesh
    assert _evaluated_positions(mesh_object)[2] == 0
    assert len(binding.last_published.edges) == 2


def test_repeated_events_do_not_republish_or_recompute(configured_local, mesh_object):
    """The runtime ignores self-tagged updates and unchanged source generations."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    invoked = _kernel_counter(binding)
    bpy.context.view_layer.update()
    published = binding.last_published
    for _ in range(3):
        bpy.context.view_layer.update()
        runtime._on_depsgraph_update_pre(bpy.context.scene, None)
    assert len(invoked) == 1
    assert binding.last_published is published
    assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)


def test_invalid_callback_from_handler_fails_closed(configured_local, mesh_object):
    """A scheduler exception must never leave the last valid Mesh visible."""
    group = create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    bpy.context.view_layer.update()
    binding.prepared = replace(binding.prepared, kernel=lambda *a, **k: {'positions': [float('nan')] * 9})
    _amount(mesh_object, group, 5.)
    mesh_object.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()
    assert binding.status == 'ERROR'
    assert binding.last_error and 'finite' in binding.last_error
    assert len(binding.prepared.result_proxy.data.vertices) == 0


def test_frame_handler_ignores_private_source_scene(configured_local, mesh_object):
    """A private source frame_set never recursively schedules itself."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    invoked = _kernel_counter(binding)
    bpy.context.view_layer.update()
    runtime._on_frame_change_pre(binding.source_scene)
    assert len(invoked) == 1
    bpy.context.scene.frame_set(2)
    assert binding.status == 'READY'
    assert len(invoked) == 1
    bpy.context.scene.frame_set(1)
    assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)


def test_removed_python_source_invalidates_cached_stage_without_reexecution(configured_local, mesh_object):
    """A removed owner invalidates runtime even when numerical inputs are unchanged."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    invoked = _kernel_counter(binding)
    bpy.context.view_layer.update()
    assert len(invoked) == 1 and binding.status == 'READY'
    (configured_local / 'shift.py').unlink()
    mesh_object.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()
    assert len(invoked) == 1
    assert binding.status == 'ERROR'
    assert len(binding.prepared.result_proxy.data.vertices) == 0


def test_uv_only_change_republishes_without_invoking_kernel(configured_local, mesh_object):
    """Updating a native-backed UV layer refreshes publication but hits the kernel cache."""
    mesh = mesh_object.data
    uv = mesh.uv_layers.new(name='UVMap')
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    invoked = _kernel_counter(binding)
    runtime.evaluate_modifier_binding(mesh_object, skip_unchanged=True)
    assert len(invoked) == 1
    old = binding.last_published
    uv.data[0].uv = (0.7, 0.2)
    refreshed = runtime.evaluate_modifier_binding(mesh_object, skip_unchanged=True)
    assert len(invoked) == 1
    assert refreshed is not old
    assert tuple(refreshed.uv_layers['UVMap'].data[0].uv) == pytest.approx((0.7, 0.2))
    assert runtime.evaluate_modifier_binding(mesh_object, skip_unchanged=True) is refreshed


def test_face_material_index_change_republishes_without_invoking_kernel(configured_local, mesh_object):
    """A FACE INT material-index change invalidates only geometry publication."""
    mesh = mesh_object.data
    material0 = bpy.data.materials.new('MeshComputeHashSchedulerMat0')
    material1 = bpy.data.materials.new('MeshComputeHashSchedulerMat1')
    mesh.materials.append(material0)
    mesh.materials.append(material1)
    try:
        create_mesh_compute_modifier(SOURCE, mesh_object)
        binding = runtime.get_modifier_binding(mesh_object)
        invoked = _kernel_counter(binding)
        runtime.evaluate_modifier_binding(mesh_object, skip_unchanged=True)
        assert len(invoked) == 1
        previous = binding.last_published
        mesh.polygons[0].material_index = 1
        refreshed = runtime.evaluate_modifier_binding(mesh_object, skip_unchanged=True)
        assert len(invoked) == 1
        assert refreshed is not previous
        assert refreshed.polygons[0].material_index == 1
        assert runtime.evaluate_modifier_binding(mesh_object, skip_unchanged=True) is refreshed
    finally:
        bpy.data.materials.remove(material0, do_unlink=True)
        bpy.data.materials.remove(material1, do_unlink=True)
