"""Real Blender regression tests for generated resource provenance at reload boundaries."""

from __future__ import annotations

import warnings

import bpy
import pytest

from NodeForge.blender import generated_resources as resources

pytestmark = pytest.mark.blender


def _root(name):
    """Create a detached GN tree to be the authoritative owner."""
    return bpy.data.node_groups.new(name, "GeometryNodeTree")


def _generated_pair(root, owner):
    """Create two same-session owned IDs, physically referenced from Object Info."""
    tx = resources.GeneratedResourceTransaction(owner)
    mesh = bpy.data.meshes.new("NF_provenance_mesh")
    tx.add(mesh, "MESH", "input_snapshot")
    obj = bpy.data.objects.new("NF_provenance_object", mesh)
    tx.add(obj, "OBJECT", "proxy")
    source = root.nodes.new("GeometryNodeObjectInfo")
    source.inputs["Object"].default_value = obj
    old_manifest = tx.manifest()
    resources.write_group_manifest(root, old_manifest)
    tx.mark_committed()
    return tx, mesh, obj, source, old_manifest


def _remove_if_live(collection, id_obj):
    """Destroy only the test-owned physical ID when still live."""
    try:
        if any(candidate is id_obj for candidate in collection):
            collection.remove(id_obj, do_unlink=True)
    except ReferenceError:
        pass


def test_exact_owner_links_allow_retiring_original_resource_ids():
    """A real committed ObjectInfo relationship permits exact cleanup after cutover."""
    root = _root("NF_provenance_root_positive")
    mesh = obj = None
    try:
        tx, mesh, obj, source, old = _generated_pair(root, "NF-provenance-positive")
        captured = resources.capture_owned_resource_ids(root, old)
        assert {ref.kind for ref, _ in captured} == {"MESH", "OBJECT"}
        mesh_name, obj_name = mesh.name, obj.name
        root.nodes.remove(source)
        resources.cleanup_previous_after_commit(old, resources.build_manifest(tx.owner_group_uuid, []), captured_ids=captured)
        assert bpy.data.objects.get(obj_name) is None
        assert bpy.data.meshes.get(mesh_name) is None
    finally:
        if obj is not None:
            _remove_if_live(bpy.data.objects, obj)
        if mesh is not None:
            _remove_if_live(bpy.data.meshes, mesh)
        _remove_if_live(bpy.data.node_groups, root)


def test_copied_metadata_and_external_data_users_never_authorize_deletion():
    """Physical user copies survive both old-generation retirement and restart sweep."""
    root = _root("NF_provenance_root_negative")
    mesh = obj = duplicate_object = duplicate_mesh = None
    try:
        tx, mesh, obj, source, old = _generated_pair(root, "NF-provenance-negative")
        duplicate_object = obj.copy()
        duplicate_mesh = mesh.copy()
        assert resources.read_id_metadata(duplicate_object) == resources.read_id_metadata(obj)
        assert resources.read_id_metadata(duplicate_mesh) == resources.read_id_metadata(mesh)
        assert resources.delete_generated_id_object(duplicate_object) is False
        assert resources.delete_generated_ref(tx.resources[0]) is False
        captured = resources.capture_owned_resource_ids(root, old)
        mesh.name += ".renamed"
        obj.name += ".renamed"
        root.nodes.remove(source)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            resources.cleanup_previous_after_commit(old, resources.build_manifest(tx.owner_group_uuid, []), captured_ids=captured)
        assert not any(candidate is obj for candidate in bpy.data.objects)
        assert any(candidate is duplicate_object for candidate in bpy.data.objects)
        assert any(candidate is mesh for candidate in bpy.data.meshes)  # duplicated Object still uses original Mesh
        assert any(candidate is duplicate_mesh for candidate in bpy.data.meshes)
        resources.clear_group_manifest(root)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            resources.cleanup_restart_orphans()
        assert caught
        assert any(candidate is duplicate_object for candidate in bpy.data.objects)
        assert any(candidate is duplicate_mesh for candidate in bpy.data.meshes)
    finally:
        for id_obj, collection in ((duplicate_object, bpy.data.objects), (obj, bpy.data.objects),
                                   (duplicate_mesh, bpy.data.meshes), (mesh, bpy.data.meshes),
                                   (root, bpy.data.node_groups)):
            if id_obj is not None:
                _remove_if_live(collection, id_obj)


def test_unrecorded_duplicate_substitution_is_not_owner_proof():
    """An ObjectInfo pointer to a copied proxy cannot replace its original provenance."""
    root = _root("NF_provenance_substitution_root")
    mesh = obj = duplicate = None
    try:
        _, mesh, obj, node, old = _generated_pair(root, "NF-provenance-substitution")
        duplicate = obj.copy()
        bpy.data.objects.remove(obj, do_unlink=True)
        obj = None
        node.inputs["Object"].default_value = duplicate
        captured = resources.capture_owned_resource_ids(root, old)
        assert all(ref.kind != "OBJECT" for ref, _ in captured)
        resources.cleanup_previous_after_commit(old, resources.build_manifest("NF-provenance-substitution", []), captured_ids=captured)
        assert any(candidate is duplicate for candidate in bpy.data.objects)
    finally:
        for id_obj, collection in ((duplicate, bpy.data.objects), (obj, bpy.data.objects),
                                   (mesh, bpy.data.meshes), (root, bpy.data.node_groups)):
            if id_obj is not None:
                _remove_if_live(collection, id_obj)


def test_external_group_reference_blocks_physical_proxy_retirement():
    """Even the original ID remains when a separate user GN group references it."""
    root, consumer = _root("NF_provenance_shared_root"), _root("NF_provenance_consumer")
    mesh = obj = None
    try:
        tx, mesh, obj, node, old = _generated_pair(root, "NF-provenance-shared")
        other = consumer.nodes.new("GeometryNodeObjectInfo")
        other.inputs["Object"].default_value = obj
        captured = resources.capture_owned_resource_ids(root, old)
        root.nodes.remove(node)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            resources.cleanup_previous_after_commit(old, resources.build_manifest(tx.owner_group_uuid, []), captured_ids=captured)
        assert any(candidate is obj for candidate in bpy.data.objects)
        assert any(candidate is mesh for candidate in bpy.data.meshes)
    finally:
        _remove_if_live(bpy.data.node_groups, consumer)
        if obj is not None:
            _remove_if_live(bpy.data.objects, obj)
        if mesh is not None:
            _remove_if_live(bpy.data.meshes, mesh)
        _remove_if_live(bpy.data.node_groups, root)


def test_private_group_ownership_requires_physical_group_node_reference():
    """NODE_GROUP IDs have the same exact-reference protection as Mesh IDs."""
    root, child = _root("NF_provenance_parent"), _root("NF_provenance_child")
    try:
        tx = resources.GeneratedResourceTransaction("NF-provenance-nested")
        tx.add(child, "NODE_GROUP", "private_prefix")
        node = root.nodes.new("GeometryNodeGroup")
        node.node_tree = child
        tx.mark_committed()
        captured = resources.capture_owned_resource_ids(root, tx.manifest())
        assert len(captured) == 1 and captured[0][1] is child
        root.nodes.remove(node)
        resources.cleanup_previous_after_commit(tx.manifest(), resources.build_manifest(tx.owner_group_uuid, []), captured_ids=captured)
        assert bpy.data.node_groups.get(child.name) is None
    except ReferenceError:
        assert not any(group.name == "NF_provenance_child" for group in bpy.data.node_groups)
    finally:
        _remove_if_live(bpy.data.node_groups, child)
        _remove_if_live(bpy.data.node_groups, root)
