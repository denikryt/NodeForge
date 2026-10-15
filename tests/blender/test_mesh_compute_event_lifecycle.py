"""Real Blender lifecycle regression gates for one-stage Mesh Compute roots.

These tests cover independent owners, animated input frames, render preparation,
invalid evaluation recovery, and stale bindings after host datablock deletion.
"""

from __future__ import annotations

from dataclasses import replace

import bpy
import pytest

from NodeForge.blender import mesh_compute_runtime as runtime
from NodeForge.compiler import create_mesh_compute_modifier
from NodeForge.tests.blender.test_mesh_compute_modifier_binding import (
    SOURCE,
    configured_local,
    mesh_object,
    _evaluated_positions,
)

pytestmark = pytest.mark.blender


def _set_amount(owner, value):
    """Set a public scalar using Blender 5.2's typed modifier inputs."""
    group = owner.modifiers[0].node_group
    socket = next(item for item in group.interface.items_tree
                  if item.item_type == 'SOCKET' and item.in_out == 'INPUT'
                  and item.name == 'Amount')
    getattr(owner.modifiers[0].properties.inputs, socket.identifier).value = value
    return socket


def _release_extra_owner(owner):
    """Remove test-owned runtime IDs before deleting their host object."""
    from NodeForge.blender import generated_resources

    for modifier in list(owner.modifiers):
        binding = runtime._ACTIVE_BINDINGS.pop(runtime._modifier_identity(modifier), None)
        if binding is not None:
            runtime.release_modifier_binding(binding)
            last = binding.last_published
            if last is not None and last.users == 0 and last in bpy.data.meshes[:]:
                bpy.data.meshes.remove(last)
        group = modifier.node_group if modifier.type == 'NODES' else None
        owner.modifiers.remove(modifier)
        if group is not None and group in bpy.data.node_groups[:]:
            manifest = generated_resources.read_group_manifest(group)
            if manifest:
                for ref in generated_resources.manifest_resources(manifest):
                    collection = generated_resources._id_collection(ref.kind)
                    exact = collection.get(ref.name)
                    if exact is not None and exact in collection[:]:
                        collection.remove(exact, do_unlink=True)
            bpy.data.node_groups.remove(group, do_unlink=True)
    if owner in bpy.data.objects[:]:
        bpy.data.objects.remove(owner, do_unlink=True)


def test_multiple_independent_owners_and_parameter_changes(configured_local, mesh_object):
    """Two private roots with a common Mesh and Local file never cross-publish."""
    second = bpy.data.objects.new('MeshComputeSecondOwner', mesh_object.data)
    bpy.context.scene.collection.objects.link(second)
    try:
        first_group = create_mesh_compute_modifier(SOURCE, mesh_object)
        second_group = create_mesh_compute_modifier(SOURCE, second)
        _set_amount(second, 5.0)
        second.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
        first = runtime.get_modifier_binding(mesh_object)
        other = runtime.get_modifier_binding(second)
        assert first.group is first_group and other.group is second_group
        assert first.group is not other.group
        assert first.prepared.result_proxy is not other.prepared.result_proxy
        assert _evaluated_positions(mesh_object)[0] == (2.0, 3.0, 4.0)
        assert _evaluated_positions(second)[0] == (5.0, 6.0, 7.0)
        old_first = first.last_published
        _set_amount(second, 7.0)
        second.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
        assert _evaluated_positions(second)[0] == (7.0, 8.0, 9.0)
        assert _evaluated_positions(mesh_object)[0] == (2.0, 3.0, 4.0)
        assert first.last_published is old_first
    finally:
        _release_extra_owner(second)


def test_frame_1_2_1_animated_scalar_updates_and_caches(configured_local, mesh_object):
    """Frame updates re-evaluate keyed scalar inputs without creating another root."""
    original_frame = bpy.context.scene.frame_current
    try:
        group = create_mesh_compute_modifier(SOURCE, mesh_object)
        bpy.context.scene.frame_set(1)
        socket = _set_amount(mesh_object, 2.0)
        path = f'modifiers["{mesh_object.modifiers[0].name}"].properties.inputs.{socket.identifier}.value'
        mesh_object.keyframe_insert(data_path=path, frame=1)
        bpy.context.scene.frame_set(2)
        _set_amount(mesh_object, 5.0)
        mesh_object.keyframe_insert(data_path=path, frame=2)
        for frame, expected in ((1, (2., 3., 4.)), (2, (5., 6., 7.)), (1, (2., 3., 4.))):
            bpy.context.scene.frame_set(frame)
            assert _evaluated_positions(mesh_object)[0] == expected
            assert mesh_object.modifiers[0].node_group is group
            assert runtime.get_modifier_binding(mesh_object).status == 'READY'
    finally:
        bpy.context.scene.frame_set(original_frame)


def test_render_pre_updates_and_repeated_cancel_cleanup(configured_local, mesh_object):
    """Render preparation uses the current parameter; cancel leaves one live handler set."""
    group = create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    _set_amount(mesh_object, 6.0)
    runtime._on_render_pre(bpy.context.scene)
    assert binding.status == 'READY'
    assert _evaluated_positions(mesh_object)[0] == (6., 7., 8.)
    assert binding.group is group
    before = binding.last_published
    # Blender may cancel a render without subsequent render_pre. Registered hooks
    # must remain unique; a render-cancel event cannot retire an active stage.
    assert runtime._on_render_pre in bpy.app.handlers.render_pre
    for callback in tuple(bpy.app.handlers.render_cancel):
        if callback.__module__ == runtime.__name__:
            callback(bpy.context.scene)
    runtime._on_render_pre(bpy.context.scene)
    assert binding.last_published is before
    assert sum(cb is runtime._on_render_pre for cb in bpy.app.handlers.render_pre) == 1


def test_frame_callback_failure_clears_output_and_recovers(configured_local, mesh_object):
    """A frame-specific callback error fails closed and a valid frame recovers."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    original = binding.prepared.kernel

    def fail_at_5(mesh, amount):
        """Raise only when the animation drives Amount to five."""
        if amount == 5.0:
            raise ValueError('frame five rejected')
        return original(mesh, amount=amount)

    binding.prepared = replace(binding.prepared, kernel=fail_at_5)
    original_frame = bpy.context.scene.frame_current
    try:
        bpy.context.scene.frame_set(1)
        socket = _set_amount(mesh_object, 2.0)
        path = f'modifiers["{mesh_object.modifiers[0].name}"].properties.inputs.{socket.identifier}.value'
        mesh_object.keyframe_insert(data_path=path, frame=1)
        bpy.context.scene.frame_set(2)
        _set_amount(mesh_object, 5.0)
        mesh_object.keyframe_insert(data_path=path, frame=2)
        bpy.context.scene.frame_set(1)
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
        bpy.context.scene.frame_set(2)
        assert binding.status == 'ERROR'
        assert 'frame five rejected' in binding.last_error
        assert len(binding.prepared.result_proxy.data.vertices) == 0
        bpy.context.scene.frame_set(1)
        assert binding.status == 'READY'
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    finally:
        bpy.context.scene.frame_set(original_frame)


def test_stale_deleted_host_does_not_crash_other_handlers(configured_local, mesh_object):
    """A stale removed secondary owner must not interrupt another active stage."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    secondary = bpy.data.objects.new('MeshComputeRemovedOwner', mesh_object.data)
    bpy.context.scene.collection.objects.link(secondary)
    create_mesh_compute_modifier(SOURCE, secondary)
    binding = runtime.get_modifier_binding(secondary)
    key = runtime._modifier_identity(binding.modifier)
    assert _evaluated_positions(secondary)[0] == (2., 3., 4.)
    bpy.data.objects.remove(secondary, do_unlink=True)
    # Undo and deletion invalidate bpy RNA wrappers. Stale stages must not
    # abort Blender callbacks or block other still-live owners.
    try:
        runtime._on_depsgraph_update_pre(bpy.context.scene, None)
        runtime._on_frame_change_pre(bpy.context.scene, None)
        runtime._on_render_pre(bpy.context.scene, None)
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    finally:
        runtime._ACTIVE_BINDINGS.pop(key, None)
        runtime.release_modifier_binding(binding)
        # Owner was deleted intentionally, but the test still owns the exact
        # orphaned candidate IDs; discard them without name-only matching.
        group = binding.group
        from NodeForge.blender import generated_resources
        manifest = generated_resources.read_group_manifest(group)
        if manifest:
            for ref in generated_resources.manifest_resources(manifest):
                collection = generated_resources._id_collection(ref.kind)
                candidate = collection.get(ref.name)
                if candidate is not None and candidate in collection[:]:
                    collection.remove(candidate, do_unlink=True)
        if group in bpy.data.node_groups[:]:
            bpy.data.node_groups.remove(group, do_unlink=True)


def test_background_render_preserves_runtime_and_published_result(configured_local, mesh_object):
    """A real Workbench render uses the committed result and keeps hooks stable."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    scene = bpy.context.scene
    render = scene.render
    previous = (render.engine, render.resolution_x, render.resolution_y,
                render.resolution_percentage, render.use_lock_interface)
    try:
        render.engine = 'BLENDER_WORKBENCH'
        render.resolution_x = 32
        render.resolution_y = 32
        render.resolution_percentage = 100
        render.use_lock_interface = True
        _set_amount(mesh_object, 4.0)
        mesh_object.update_tag(refresh={'OBJECT'})
        assert bpy.ops.render.render(write_still=False) == {'FINISHED'}
        assert binding.status == 'READY'
        assert _evaluated_positions(mesh_object)[0] == (4., 5., 6.)
        previous_mesh = binding.last_published
        runtime._on_render_pre(scene)
        assert binding.last_published is previous_mesh
        assert runtime._SCHEDULER_RUNNING is False
    finally:
        (render.engine, render.resolution_x, render.resolution_y,
         render.resolution_percentage, render.use_lock_interface) = previous


def test_frame_pre_resolves_animated_integer_and_boolean_inputs(configured_local, mesh_object):
    """FCurve floats are converted to the original INT/BOOL modifier socket types."""
    (configured_local / 'toggle.py').write_text(
        'from NodeForge import mesh_compute\n'
        '@mesh_compute(writes=("height",))\n'
        'def toggle(mesh, amount: int = 2, enabled: bool = True):\n'
        '    value = amount if enabled else 0\n'
        '    positions = list(mesh.positions)\n'
        '    for i in range(2, len(positions), 3): positions[i] += value\n'
        '    return {"positions": positions, "point_attributes": '
        '{"height": [value] * mesh.vertex_count}}\n', encoding='utf-8',
    )
    script = ('from local import toggle\n'
              'geo = input_geometry("Geometry")\n'
              'amount = input_int("Amount", default=2)\n'
              'enabled = input_bool("Enabled", default=True)\n'
              'geo = toggle(geo, amount=amount, enabled=enabled)\n'
              'output("Geometry", geo)\n')
    current = bpy.context.scene.frame_current
    try:
        group = create_mesh_compute_modifier(script, mesh_object)
        bpy.context.scene.frame_set(1)
        for label, values in (('Amount', (2, 5)), ('Enabled', (True, False))):
            socket = next(item for item in group.interface.items_tree
                          if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == label)
            path = f'modifiers["{mesh_object.modifiers[0].name}"].properties.inputs.{socket.identifier}.value'
            for frame, value in ((1, values[0]), (2, values[1])):
                bpy.context.scene.frame_set(frame)
                getattr(mesh_object.modifiers[0].properties.inputs, socket.identifier).value = value
                mesh_object.keyframe_insert(data_path=path, frame=frame)
        for frame, expected in ((1, (2., 3., 4.)), (2, (0., 1., 2.)), (1, (2., 3., 4.))):
            bpy.context.scene.frame_set(frame)
            assert _evaluated_positions(mesh_object)[0] == expected
            assert runtime.get_modifier_binding(mesh_object).status == 'READY'
    finally:
        bpy.context.scene.frame_set(current)
