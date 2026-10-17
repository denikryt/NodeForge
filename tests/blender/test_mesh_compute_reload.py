"""Positive and failure-atomic Blender tests for fresh-private-root Mesh Compute Reload."""

from __future__ import annotations

import bpy
import pytest

from NodeForge.compiler import create_mesh_compute_modifier, update_mesh_compute_modifier
from NodeForge.blender import mesh_compute_runtime as runtime
from NodeForge.tests.blender.test_mesh_compute_modifier_binding import (
    SOURCE, configured_local, mesh_object, _evaluated_positions,
)

pytestmark = pytest.mark.blender


def _new_kernel_source() -> str:
    """Return a revised Local implementation with the same declared callable."""
    return (
        'from NodeForge import mesh_compute\n'
        '@mesh_compute(writes=("height",))\n'
        'def shift(mesh, amount: float = 2.0):\n'
        '    p = list(mesh.positions)\n'
        '    for n in range(2, len(p), 3):\n'
        '        p[n] += amount * 2\n'
        '    return {"positions": p, "point_attributes": '
        '{"height": [amount * 2] * mesh.vertex_count}}\n'
    )


def test_reload_updates_python_kernel_preserving_gn_group_and_modifier(configured_local, mesh_object):
    """Fresh Reload preserves logical owner and modifier while replacing the callback."""
    group = create_mesh_compute_modifier(SOURCE, mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    original = runtime.get_modifier_binding(mesh_object)
    original_group = group.as_pointer()
    original_modifier = mesh_object.modifiers[0].as_pointer()
    assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    (configured_local / 'shift.py').write_text(_new_kernel_source())
    updated = update_mesh_compute_modifier(SOURCE, mesh_object)
    assert updated.as_pointer() != original_group
    from NodeForge.function_instances import FUNCTION_ROOT_OWNER_ID_PROP
    assert updated.get(FUNCTION_ROOT_OWNER_ID_PROP)
    assert mesh_object.modifiers[0].as_pointer() == original_modifier
    binding = runtime.get_modifier_binding(mesh_object)
    assert binding is not original
    assert binding.status == 'READY_PENDING_EVALUATION'
    runtime.evaluate_modifier_binding(mesh_object)
    assert _evaluated_positions(mesh_object) == ((4., 5., 6.), (4., 4., 4.), 1)


def test_reload_precommit_error_restores_previous_group_callback_and_output(configured_local, mesh_object, monkeypatch):
    """Failure after group mutation rolls back nodes, resources and runtime mapping."""
    group = create_mesh_compute_modifier(SOURCE, mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    original = runtime.get_modifier_binding(mesh_object)
    old_output = _evaluated_positions(mesh_object)
    old_nodes = len(group.nodes)
    old_group = group.as_pointer()
    previous_scenes = {s.as_pointer() for s in bpy.data.scenes}
    (configured_local / 'shift.py').write_text(_new_kernel_source())

    def rejected(*args, **kwargs):
        raise RuntimeError('injected Reload binding failure')

    monkeypatch.setattr(runtime, 'prepare_modifier_binding', rejected)
    # Compiler imported this function by symbol, so monkeypatch its owner too.
    from NodeForge import compiler
    monkeypatch.setattr(compiler, 'prepare_modifier_binding', rejected, raising=False)
    with pytest.raises(RuntimeError, match='injected Reload'):
        update_mesh_compute_modifier(SOURCE, mesh_object)
    assert group.as_pointer() == old_group
    assert len(group.nodes) == old_nodes
    assert runtime.get_modifier_binding(mesh_object) is original
    assert _evaluated_positions(mesh_object) == old_output
    assert {s.as_pointer() for s in bpy.data.scenes} == previous_scenes
    assert runtime._modifier_identity(original.modifier) not in runtime._GUARDED_MODIFIERS
    runtime.evaluate_modifier_binding(mesh_object)
    assert _evaluated_positions(mesh_object) == old_output


def test_reload_keeps_animated_modifier_input_path(configured_local, mesh_object):
    """Reload preserves the original GN input identifier and frame FCurve values."""
    group = create_mesh_compute_modifier(SOURCE, mesh_object)
    modifier = mesh_object.modifiers[0]
    identifier = next(item.identifier for item in group.interface.items_tree
                      if item.item_type == 'SOCKET' and item.in_out == 'INPUT'
                      and item.name == 'Amount')
    path = f'modifiers["{modifier.name}"].properties.inputs.{identifier}.value'
    for frame, value in ((1, 2.), (2, 5.)):
        getattr(modifier.properties.inputs, identifier).value = value
        mesh_object.keyframe_insert(data_path=path, frame=frame)
    bpy.context.scene.frame_set(2)
    assert getattr(modifier.properties.inputs, identifier).value == pytest.approx(5.)
    (configured_local / 'shift.py').write_text(_new_kernel_source())
    replacement = update_mesh_compute_modifier(SOURCE, mesh_object)
    id_after = next(item.identifier for item in replacement.interface.items_tree
                    if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == 'Amount')
    assert id_after
    bpy.context.scene.frame_set(1)
    runtime.evaluate_modifier_binding(mesh_object)
    assert _evaluated_positions(mesh_object)[0] == (4., 5., 6.)
    bpy.context.scene.frame_set(2)
    runtime.evaluate_modifier_binding(mesh_object)
    assert _evaluated_positions(mesh_object)[0] == (10., 11., 12.)


def test_reload_partial_migration_raises_and_restores_exact_old_output(configured_local, mesh_object, monkeypatch):
    """A failure after a real modifier pointer/FCurve edit must restore all outputs."""
    from NodeForge import compiler
    group = create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    before = _evaluated_positions(mesh_object)
    old_proxy_mesh = binding.prepared.result_proxy.data
    old_group_pointer = group.as_pointer()
    old_modifier_pointer = mesh_object.modifiers[0].as_pointer()
    old_scene_ids = {s.as_pointer() for s in bpy.data.scenes}
    (configured_local / 'shift.py').write_text(_new_kernel_source())

    original_install = runtime.ModifierInputMigration.install

    def failing_install(journal):
        original_install(journal)
        assert journal.modifier.node_group is journal.new_group
        raise RuntimeError('injected failure after socket migration')

    monkeypatch.setattr(runtime.ModifierInputMigration, 'install', failing_install)
    with pytest.raises(RuntimeError, match='injected failure after socket migration'):
        compiler.update_mesh_compute_modifier(SOURCE, mesh_object)
    assert mesh_object.modifiers[0].as_pointer() == old_modifier_pointer
    assert mesh_object.modifiers[0].node_group.as_pointer() == old_group_pointer
    assert runtime.get_modifier_binding(mesh_object) is binding
    assert binding.prepared.result_proxy.data is old_proxy_mesh
    assert _evaluated_positions(mesh_object) == before
    assert {s.as_pointer() for s in bpy.data.scenes} == old_scene_ids
    assert runtime._modifier_identity(binding.modifier) not in runtime._GUARDED_MODIFIERS


def test_reload_rejects_removed_scalar_declaration_without_touching_geometry(configured_local, mesh_object):
    """Removed input identity is rejected at migration, without updating live GN."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    before = _evaluated_positions(mesh_object)
    original = runtime.get_modifier_binding(mesh_object)
    replacement_source = ('from local import shift\n'
                          'geo = input_geometry("Geometry")\n'
                          'geo = shift(geo, amount=2.0)\n'
                          'output("Geometry", geo)\n')
    with pytest.raises(Exception, match='removed or changed a declared scalar'):
        update_mesh_compute_modifier(replacement_source, mesh_object)
    assert runtime.get_modifier_binding(mesh_object) is original
    assert _evaluated_positions(mesh_object) == before


def test_reload_rejects_external_group_user_without_touching_state(configured_local, mesh_object):
    """Private root sharing prevents cutover and any implicit foreign-user edits."""
    group = create_mesh_compute_modifier(SOURCE, mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    before = _evaluated_positions(mesh_object)
    other_tree = bpy.data.node_groups.new("MeshComputeExternalGroupUser", "GeometryNodeTree")
    try:
        group_node = other_tree.nodes.new("GeometryNodeGroup")
        group_node.node_tree = group
        assert group.users > 1
        with pytest.raises(Exception, match='exclusively owned'):
            update_mesh_compute_modifier(SOURCE, mesh_object)
        assert _evaluated_positions(mesh_object) == before
        assert runtime.get_modifier_binding(mesh_object).group is group
    finally:
        bpy.data.node_groups.remove(other_tree, do_unlink=True)


def test_reload_migrates_animated_parameters_and_restores_paths_after_precommit_failure(configured_local, mesh_object, monkeypatch):
    """Post-migration binding failure restores keyed frame values and GN geometry."""
    from NodeForge import compiler
    group = create_mesh_compute_modifier(SOURCE, mesh_object)
    mod = mesh_object.modifiers[0]
    amount_id = next(x.identifier for x in group.interface.items_tree
                     if x.item_type == 'SOCKET' and x.name == 'Amount' and x.in_out == 'INPUT')
    path = f'modifiers["{mod.name}"].properties.inputs.{amount_id}.value'
    for frame, value in ((1, 2.0), (2, 5.0)):
        getattr(mod.properties.inputs, amount_id).value = value
        mesh_object.keyframe_insert(data_path=path, frame=frame)
    bpy.context.scene.frame_set(2)
    runtime.evaluate_modifier_binding(mesh_object)
    original = runtime.get_modifier_binding(mesh_object)
    old_mesh = original.prepared.result_proxy.data
    assert _evaluated_positions(mesh_object)[0] == (5., 6., 7.)
    old_paths = [c.data_path for layer in mesh_object.animation_data.action.layers
                 for strip in layer.strips for bag in strip.channelbags for c in bag.fcurves]
    (configured_local / 'shift.py').write_text(_new_kernel_source())
    def fail(*args, **kwargs):
        raise RuntimeError('injected failure after animated migration')
    monkeypatch.setattr(runtime, 'prepare_modifier_binding', fail)
    with pytest.raises(RuntimeError, match='after animated migration'):
        update_mesh_compute_modifier(SOURCE, mesh_object)
    assert runtime.get_modifier_binding(mesh_object) is original
    assert mod.node_group.as_pointer() == group.as_pointer()
    assert original.prepared.result_proxy.data is old_mesh
    current_paths = [c.data_path for layer in mesh_object.animation_data.action.layers
                     for strip in layer.strips for bag in strip.channelbags for c in bag.fcurves]
    assert current_paths == old_paths
    for frame, value, expected in ((1, 2., (2., 3., 4.)), (2, 5., (5., 6., 7.)),
                                   (1, 2., (2., 3., 4.))):
        bpy.context.scene.frame_set(frame)
        assert getattr(mod.properties.inputs, amount_id).value == pytest.approx(value)
        runtime.evaluate_modifier_binding(mesh_object)
        assert _evaluated_positions(mesh_object)[0] == expected
