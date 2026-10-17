"""Blender 5.2 evidence for choosing a Mesh Compute root-group Reload strategy."""

from __future__ import annotations

import bpy
import pytest

pytestmark = pytest.mark.blender


def _group(name, *, reversed_inputs=False):
    """Return a minimal functional GN root with floating and boolean controls."""
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    inputs = (("Amount", "NodeSocketFloat"), ("Steps", "NodeSocketInt"),
              ("Enabled", "NodeSocketBool"))
    for label, socket_type in reversed(inputs) if reversed_inputs else inputs:
        group.interface.new_socket(name=label, in_out="INPUT", socket_type=socket_type)
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    inp = group.nodes.new("NodeGroupInput")
    out = group.nodes.new("NodeGroupOutput")
    group.links.new(inp.outputs["Geometry"], out.inputs["Geometry"])
    return group


def _identifier(group, name):
    """Resolve a modifier input by its group interface declaration name."""
    return next(item.identifier for item in group.interface.items_tree
                if item.item_type == "SOCKET" and item.name == name
                and item.in_out == "INPUT")


def test_blender_52_modifier_input_is_rna_not_id_property():
    """Valid RNA value updates work; legacy dict assignment is rejected."""
    old = _group("NF_MeshCompute52_RNA")
    mesh = bpy.data.meshes.new("NF_MeshCompute52_Base")
    host = bpy.data.objects.new("NF_MeshCompute52_Host", mesh)
    try:
        mod = host.modifiers.new("Mesh Compute", "NODES")
        mod.node_group = old
        identifier = _identifier(old, "Amount")
        getattr(mod.properties.inputs, identifier).value = 4.25
        assert getattr(mod.properties.inputs, identifier).value == pytest.approx(4.25)
        with pytest.raises(TypeError, match="id properties not supported"):
            mod[identifier] = 1.0
    finally:
        bpy.data.objects.remove(host, do_unlink=True)
        bpy.data.meshes.remove(mesh, do_unlink=True)
        bpy.data.node_groups.remove(old)


def test_fresh_group_swap_requires_animation_and_input_migration():
    """Naive fresh-group assignment reuses a socket ID for a different input."""
    old = _group("NF_MeshCompute52_Old")
    replacement = _group("NF_MeshCompute52_New", reversed_inputs=True)
    mesh = bpy.data.meshes.new("NF_MeshCompute52_BaseAnimated")
    host = bpy.data.objects.new("NF_MeshCompute52_HostAnimated", mesh)
    try:
        mod = host.modifiers.new("Mesh Compute", "NODES")
        mod.node_group = old
        amount_id = _identifier(old, "Amount")
        assert amount_id != _identifier(replacement, "Amount")
        path = f'modifiers["{mod.name}"].properties.inputs.{amount_id}.value'
        for frame, value in ((1, 1.25), (2, 2.5)):
            getattr(mod.properties.inputs, amount_id).value = value
            host.keyframe_insert(data_path=path, frame=frame)
        bpy.context.scene.frame_set(2)
        assert getattr(mod.properties.inputs, amount_id).value == pytest.approx(2.5)

        mod.node_group = replacement
        new_amount_id = _identifier(replacement, "Amount")
        assert getattr(mod.properties.inputs, new_amount_id).value != pytest.approx(2.5)
        action = host.animation_data.action
        curves = [curve.data_path for layer in action.layers
                  for strip in layer.strips for bag in strip.channelbags
                  for curve in bag.fcurves]
        assert path in curves
        assert not any(new_amount_id in curve for curve in curves)
    finally:
        bpy.context.scene.frame_set(1)
        bpy.data.objects.remove(host, do_unlink=True)
        bpy.data.meshes.remove(mesh, do_unlink=True)
        bpy.data.node_groups.remove(replacement)
        bpy.data.node_groups.remove(old)


def test_fresh_group_explicit_rna_value_and_fcurve_migration_can_preserve_animation():
    """Blender 5.2 permits reversible explicit migration when identifiers change."""
    old = _group("NF_MeshCompute52_OldForMigration")
    replacement = _group("NF_MeshCompute52_NewForMigration", reversed_inputs=True)
    mesh = bpy.data.meshes.new("NF_MeshCompute52_MigrationBase")
    owner = bpy.data.objects.new("NF_MeshCompute52_MigrationHost", mesh)
    bpy.context.scene.collection.objects.link(owner)
    old_frame = bpy.context.scene.frame_current
    try:
        modifier = owner.modifiers.new("Mesh Compute", "NODES")
        modifier.node_group = old
        old_id = _identifier(old, "Amount")
        new_id = _identifier(replacement, "Amount")
        assert old_id != new_id
        source_path = f'modifiers["{modifier.name}"].properties.inputs.{old_id}.value'
        dest_path = f'modifiers["{modifier.name}"].properties.inputs.{new_id}.value'
        for frame, value in ((1, 1.25), (2, 2.50)):
            getattr(modifier.properties.inputs, old_id).value = value
            owner.keyframe_insert(data_path=source_path, frame=frame)
        bpy.context.scene.frame_set(2)
        saved = getattr(modifier.properties.inputs, old_id).value
        action = owner.animation_data.action
        curves = [curve for layer in action.layers for strip in layer.strips
                  for bag in strip.channelbags for curve in bag.fcurves]
        affected = [curve for curve in curves if curve.data_path == source_path]
        assert len(affected) == 1

        # Prototype one reversible precommit pointer/property/action edit.
        modifier.node_group = replacement
        getattr(modifier.properties.inputs, new_id).value = saved
        for curve in affected:
            curve.data_path = dest_path
        for frame, value in ((1, 1.25), (2, 2.50), (1, 1.25)):
            bpy.context.scene.frame_set(frame)
            assert getattr(modifier.properties.inputs, new_id).value == pytest.approx(value)

        # A pre-PONR failure must undo action and pointer edits in this order.
        for curve in affected:
            curve.data_path = source_path
        modifier.node_group = old
        for frame, value in ((1, 1.25), (2, 2.50), (1, 1.25)):
            bpy.context.scene.frame_set(frame)
            assert getattr(modifier.properties.inputs, old_id).value == pytest.approx(value)
        assert all(curve.data_path == source_path for curve in affected)
    finally:
        bpy.context.scene.frame_set(old_frame)
        bpy.data.objects.remove(owner, do_unlink=True)
        bpy.data.meshes.remove(mesh, do_unlink=True)
        bpy.data.node_groups.remove(replacement)
        bpy.data.node_groups.remove(old)
