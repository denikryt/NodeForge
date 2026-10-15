"""Blender 5.2 boundary tests for a prepared Local runtime root modifier."""

from __future__ import annotations

from array import array

import bpy
import pytest

from NodeForge import local_sources
from NodeForge.compiler import create_mesh_compute_modifier
from NodeForge.blender import mesh_compute_runtime as runtime

pytestmark = pytest.mark.blender


@pytest.fixture
def configured_local(tmp_path, monkeypatch):
    """Authorize one isolated Local directory without editing user settings."""
    managed = tmp_path / "Local"
    managed.mkdir()
    monkeypatch.setattr(local_sources, "_default_local_catalog_dir", lambda: managed)
    monkeypatch.setattr(local_sources, "_local_sources_registry_path", lambda: tmp_path / "registry.json")
    local_sources.set_local_python_permission(managed, allowed=True)
    (managed / "shift.py").write_text(
        'from NodeForge import mesh_compute\n'
        '@mesh_compute(writes=("height",))\n'
        'def shift(mesh, amount: float = 2.0):\n'
        '    p = list(mesh.positions)\n'
        '    for n in range(2, len(p), 3):\n'
        '        p[n] += amount\n'
        '    return {"positions": p, "point_attributes": '
        '{"height": [amount] * mesh.vertex_count}}\n'
    )
    return managed


@pytest.fixture
def mesh_object():
    """Provide a simple owned mesh Object whose topology is test-controlled."""
    mesh = bpy.data.meshes.new("MeshComputeTestInput")
    mesh.from_pydata([(0, 0, 0), (1, 0, 1), (0, 1, 2)], [], [(0, 1, 2)])
    mesh.update()
    obj = bpy.data.objects.new("MeshComputeTestOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)
    yield obj
    for binding in list(runtime._ACTIVE_BINDINGS.values()):
        if binding.owner is obj:
            runtime._ACTIVE_BINDINGS.pop(runtime._modifier_identity(binding.modifier), None)
            runtime.release_modifier_binding(binding)
            if binding.last_published is not None and any(m is binding.last_published for m in bpy.data.meshes):
                bpy.data.meshes.remove(binding.last_published, do_unlink=True)
    for modifier in list(obj.modifiers):
        group = modifier.node_group if modifier.type == "NODES" else None
        obj.modifiers.remove(modifier)
        if group is not None and any(g is group for g in bpy.data.node_groups):
            from NodeForge.blender import generated_resources
            manifest = generated_resources.read_group_manifest(group)
            if manifest:
                for ref in generated_resources.manifest_resources(manifest):
                    # Tests explicitly own all compiled candidate resources.
                    collection = generated_resources._id_collection(ref.kind)
                    exact = collection.get(ref.name)
                    if exact is not None and any(x is exact for x in collection):
                        collection.remove(exact, do_unlink=True)
            bpy.data.node_groups.remove(group, do_unlink=True)
    if any(o is obj for o in bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    if any(m is mesh for m in bpy.data.meshes):
        bpy.data.meshes.remove(mesh, do_unlink=True)


SOURCE = ('from local import shift\n'
          'geo = input_geometry("Geometry")\n'
          'amount = input_float("Amount", default=2.0)\n'
          'geo = shift(geo, amount=amount)\n'
          'output("Geometry", geo)\n')


def _evaluated_positions(obj):
    """Read final modifier output rather than the transport proxy alone."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(dg)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
    try:
        height = array("f", [0]) * len(mesh.vertices)
        mesh.attributes["height"].data.foreach_get("value", height)
        return tuple(round(v.co.z, 5) for v in mesh.vertices), tuple(height), len(mesh.polygons)
    finally:
        evaluated.to_mesh_clear()


def test_private_modifier_compile_and_explicit_evaluation(configured_local, mesh_object):
    """Full source→EXTENSION→modifier→GN prefix→callback→GN output roundtrip."""
    group = create_mesh_compute_modifier(SOURCE, mesh_object)
    assert len(mesh_object.modifiers) == 1
    binding = runtime.get_modifier_binding(mesh_object)
    assert binding.group is group and binding.status == "READY_PENDING_EVALUATION"
    assert len(binding.prepared.result_proxy.data.vertices) == 0
    output = runtime.evaluate_modifier_binding(mesh_object)
    assert output is binding.prepared.result_proxy.data
    assert binding.status == "READY"
    assert _evaluated_positions(mesh_object) == ((2.0, 3.0, 4.0), (2.0, 2.0, 2.0), 1)
    assert tuple(round(v.co.z, 4) for v in mesh_object.data.vertices) == (0, 1, 2)
    amount_socket = next(x for x in group.interface.items_tree if x.item_type == "SOCKET" and x.name == "Amount")
    getattr(mesh_object.modifiers[0].properties.inputs, amount_socket.identifier).value = 5.0
    runtime.evaluate_modifier_binding(mesh_object)
    assert _evaluated_positions(mesh_object) == ((5.0, 6.0, 7.0), (5.0, 5.0, 5.0), 1)
    assert runtime.get_modifier_binding(mesh_object).group is group


def test_modifier_binding_failure_rolls_back_before_commit(configured_local, mesh_object, monkeypatch):
    """A failing isolated source preparation leaves no published group/modifier."""
    from NodeForge import compiler
    before_groups = {g.as_pointer() for g in bpy.data.node_groups}
    before_scenes = {s.as_pointer() for s in bpy.data.scenes}

    def reject(*args, **kwargs):
        raise RuntimeError("injected binding failure")

    monkeypatch.setattr(runtime, "prepare_modifier_binding", reject)
    with pytest.raises(RuntimeError, match="injected binding failure"):
        compiler.create_mesh_compute_modifier(SOURCE, mesh_object)
    assert not mesh_object.modifiers
    assert {g.as_pointer() for g in bpy.data.node_groups} == before_groups
    assert {s.as_pointer() for s in bpy.data.scenes} == before_scenes


def test_existing_modifier_rejected_before_compilation(configured_local, mesh_object):
    """Never replace a user-owned modifier as an implicit Mesh Compute compile."""
    mod = mesh_object.modifiers.new("User Mod", "BEVEL")
    with pytest.raises(Exception, match="previous modifiers"):
        create_mesh_compute_modifier(SOURCE, mesh_object)
    assert len(mesh_object.modifiers) == 1 and mesh_object.modifiers[0].as_pointer() == mod.as_pointer()


def test_invalid_callback_result_clears_previous_published_mesh(configured_local, mesh_object):
    """A callback failure empties the GN-visible mesh instead of showing old data."""
    from dataclasses import replace

    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    assert len(binding.prepared.result_proxy.data.vertices) == 3
    binding.prepared = replace(binding.prepared, kernel=lambda *_a, **_k: {"positions": [float("nan")] * 9})
    # Numerical input remains unchanged; resetting the key models Reload.
    binding.numerical_key = None
    with pytest.raises(ValueError, match="finite"):
        runtime.evaluate_modifier_binding(mesh_object)
    assert binding.status == "ERROR" and binding.last_error
    assert len(binding.prepared.result_proxy.data.vertices) == 0


def test_duplicate_root_group_clears_original_result(configured_local, mesh_object):
    """Sharing a private runtime group cannot expose another object's output."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    other_mesh = bpy.data.meshes.new("MeshComputeDuplicatedMesh")
    other = bpy.data.objects.new("MeshComputeDuplicatedOwner", other_mesh)
    bpy.context.scene.collection.objects.link(other)
    try:
        other.modifiers.new("Shared", "NODES").node_group = binding.group
        with pytest.raises(ValueError, match="shared"):
            runtime.evaluate_modifier_binding(mesh_object)
        assert binding.status == "ERROR"
        assert len(binding.prepared.result_proxy.data.vertices) == 0
    finally:
        bpy.data.objects.remove(other, do_unlink=True)
        bpy.data.meshes.remove(other_mesh, do_unlink=True)


def test_cutover_side_effects_rollback_with_group_savepoint():
    """Modifier-install rollback actions run before an aborted outer commit."""
    from NodeForge.blender.group_backend import BlenderGroupBuildTransaction

    tx = BlenderGroupBuildTransaction()
    state = {"live": False, "commit": False}
    checkpoint = tx.savepoint()
    state["live"] = True
    tx.register_external_cutover(
        rollback=lambda: state.update(live=False),
        commit=lambda: state.update(commit=True),
    )
    tx.rollback_to_savepoint(checkpoint)
    assert state == {"live": False, "commit": False}
    tx.commit()
    assert state == {"live": False, "commit": False}
    next_tx = BlenderGroupBuildTransaction()
    next_tx.register_external_cutover(
        rollback=lambda: state.update(live=False),
        commit=lambda: state.update(commit=True),
    )
    next_tx.commit()
    assert state["commit"]
