"""Real-Blender generated-resource transaction coverage for extension backend context."""

from __future__ import annotations

import bpy
import pytest

from NodeForge import generated_resources
from NodeForge.extension_api import ExtensionBackendContext

pytestmark = pytest.mark.blender


def _context():
    """Create one detached candidate group/context/transaction fixture."""
    group = bpy.data.node_groups.new("NFTest_extension_resource_context", "GeometryNodeTree")
    tx = generated_resources.GeneratedResourceTransaction(owner_group_uuid="extension_v2-owner")
    return group, tx, ExtensionBackendContext(
        group=group,
        location=(0.0, 0.0),
        generated_resource_transaction=tx,
    )


def test_context_generated_ids_are_owned_and_metadata_published():
    """Mesh/Curve/Object creators return exact IDs already tracked by the outer transaction."""
    group, tx, context = _context()
    mesh = curve = obj = None
    try:
        mesh = context.new_generated_mesh(role="mesh", name_hint="demo")
        curve = context.new_generated_curve(role="curve")
        obj = context.new_generated_object(mesh, role="object", name_hint="demo")
        assert [ref.role for ref in tx.resources] == ["mesh", "curve", "object"]
        assert [item[0] for item in tx._owned_ids] == [mesh, curve, obj]
        assert generated_resources.read_id_metadata(mesh).role == "mesh"
        assert generated_resources.read_id_metadata(curve).role == "curve"
        assert generated_resources.read_id_metadata(obj).role == "object"
    finally:
        tx.rollback()
        if group.name in bpy.data.node_groups:
            bpy.data.node_groups.remove(group)


def test_metadata_failure_leaves_exact_id_for_outer_rollback(monkeypatch):
    """Context creator propagates metadata failure and does not initiate rollback itself."""
    group, tx, context = _context()
    monkeypatch.setattr(
        generated_resources,
        "_write_id_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("metadata sentinel")),
    )
    try:
        with pytest.raises(RuntimeError, match="metadata sentinel"):
            context.new_generated_mesh(role="mesh")
        assert len(tx._owned_ids) == 1
        created, kind = tx._owned_ids[0]
        created_name = created.name
        assert kind == "MESH"
        assert bpy.data.meshes.get(created_name) is created
        tx.rollback()
        assert bpy.data.meshes.get(created_name) is None
    finally:
        if tx._owned_ids:
            for id_obj, kind in tuple(tx._owned_ids):
                try:
                    generated_resources._remove_owned_id_exact(id_obj, kind)
                except Exception:
                    pass
        if group.name in bpy.data.node_groups:
            bpy.data.node_groups.remove(group)


def test_best_effort_rollback_continues_after_one_exact_removal_failure(monkeypatch):
    """One failed removal does not prevent cleanup attempts for the remaining owned IDs."""
    group, tx, context = _context()
    first = context.new_generated_mesh(role="first")
    second = context.new_generated_mesh(role="second")
    first_name = first.name
    second_name = second.name
    real_remove = generated_resources._remove_owned_id_exact
    attempted = []

    def flaky_remove(id_obj, kind):
        attempted.append(id_obj)
        if id_obj is second:
            raise RuntimeError("remove sentinel")
        return real_remove(id_obj, kind)

    monkeypatch.setattr(generated_resources, "_remove_owned_id_exact", flaky_remove)
    try:
        with pytest.raises(RuntimeError, match="rollback failed"):
            tx.rollback()
        assert attempted == [second, first]
        assert bpy.data.meshes.get(first_name) is None
        assert bpy.data.meshes.get(second_name) is second
        assert tx._owned_ids == [(second, "MESH")]
    finally:
        if bpy.data.meshes.get(second_name) is second:
            bpy.data.meshes.remove(second)
        tx._owned_ids.clear()
        if group.name in bpy.data.node_groups:
            bpy.data.node_groups.remove(group)
