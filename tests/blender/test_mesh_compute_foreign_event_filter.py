"""Conservative scheduler gate: Blender ID identity, dependencies and fail-closed paths."""

from __future__ import annotations

import bpy
import pytest

from NodeForge import local_sources
from NodeForge.blender import mesh_compute_runtime as runtime
from NodeForge.compiler import create_mesh_compute_modifier
from NodeForge.tests.blender.test_mesh_compute_modifier_binding import (
    SOURCE, configured_local, mesh_object, _evaluated_positions,
)

pytestmark = pytest.mark.blender


def _foreign_object():
    """Allocate an ordinary unrelated scene Object with private source Mesh."""
    mesh = bpy.data.meshes.new('GateForeignMesh')
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new('GateForeignObject', mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj, mesh


def _init(owner):
    """Install one trusted source and settle initial mesh publication."""
    create_mesh_compute_modifier(SOURCE, owner)
    bpy.context.view_layer.update()
    binding = runtime.get_modifier_binding(owner)
    assert binding.status == 'READY'
    return binding


def _measured_gate(monkeypatch):
    """Collect real PRE decisions without changing filter implementation."""
    called = []
    original = runtime._can_skip_foreign_depsgraph_updates

    def probe(scene):
        decision = original(scene)
        called.append(decision)
        return decision

    monkeypatch.setattr(runtime, '_can_skip_foreign_depsgraph_updates', probe)
    return called


def test_unrelated_object_and_mesh_events_skip_full_runtime(configured_local, mesh_object, monkeypatch):
    """Both foreign transforms and foreign Mesh tags leave committed stage untouched."""
    binding = _init(mesh_object)
    foreign, data = _foreign_object()
    try:
        bpy.context.view_layer.update()
        decisions = _measured_gate(monkeypatch)
        old = binding.last_published
        for edited in (foreign, data):
            decisions.clear()
            if edited is foreign:
                edited.update_tag(refresh={'OBJECT'})
            else:
                edited.vertices[0].co.z += 1.0
                edited.update()
            bpy.context.view_layer.update()
            assert True in decisions, ('foreign event was not recognized', decisions)
            assert binding.last_published is old
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    finally:
        bpy.data.objects.remove(foreign, do_unlink=True)
        bpy.data.meshes.remove(data, do_unlink=True)


def test_scalar_and_geometry_updates_never_skip(configured_local, mesh_object, monkeypatch):
    """Relevant edits propagate through the original callback and geometry gate."""
    binding = _init(mesh_object)
    decisions = _measured_gate(monkeypatch)
    socket = next(s for s in binding.group.interface.items_tree
                  if s.item_type == 'SOCKET' and s.in_out == 'INPUT' and s.name == 'Amount')
    getattr(binding.modifier.properties.inputs, socket.identifier).value = 6.0
    mesh_object.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()
    assert decisions and decisions[-1] is False
    assert _evaluated_positions(mesh_object)[0] == (6., 7., 8.)
    old = binding.last_published
    decisions.clear()
    mesh_object.data.vertices[0].co.z = 3.0
    mesh_object.data.update()
    bpy.context.view_layer.update()
    assert decisions and not all(decisions)
    assert binding.last_published is not old
    assert _evaluated_positions(mesh_object)[0] == (9., 7., 8.)


def test_foreign_event_does_not_hide_source_owner_removal(configured_local, mesh_object, monkeypatch):
    """Owner availability is outside depsgraph; a foreign event must invalidate output."""
    binding = _init(mesh_object)
    foreign, data = _foreign_object()
    try:
        bpy.context.view_layer.update()
        decisions = _measured_gate(monkeypatch)
        old = binding.last_published
        (configured_local / 'shift.py').unlink()
        foreign.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
        assert decisions and False in decisions
        assert binding.status == 'ERROR'
        assert binding.last_published is not old
        assert len(binding.prepared.result_proxy.data.vertices) == 0
    finally:
        bpy.data.objects.remove(foreign, do_unlink=True)
        bpy.data.meshes.remove(data, do_unlink=True)


def test_parent_transform_and_shared_source_are_not_foreign(configured_local, mesh_object, monkeypatch):
    """Parent transforms and other Objects with the same Mesh must use full validation."""
    binding = _init(mesh_object)
    foreign, data = _foreign_object()
    sibling = bpy.data.objects.new('GateSharedMesh', mesh_object.data)
    bpy.context.scene.collection.objects.link(sibling)
    try:
        bpy.context.view_layer.update()
        decisions = _measured_gate(monkeypatch)
        mesh_object.parent = foreign
        bpy.context.view_layer.update()
        decisions.clear()
        foreign.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
        assert decisions and False in decisions
        mesh_object.parent = None
        bpy.context.view_layer.update()
        decisions.clear()
        sibling.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
        assert decisions and False in decisions
        assert binding.status == 'READY'
    finally:
        mesh_object.parent = None
        bpy.data.objects.remove(sibling, do_unlink=True)
        bpy.data.objects.remove(foreign, do_unlink=True)
        bpy.data.meshes.remove(data, do_unlink=True)


def test_undeclared_prefix_dependency_forces_fallback(configured_local, mesh_object, monkeypatch):
    """An edited private GN prefix with Object Info is not proved independent."""
    binding = _init(mesh_object)
    foreign, data = _foreign_object()
    try:
        node = binding.prepared.prefix.nodes.new('GeometryNodeObjectInfo')
        node.inputs['Object'].default_value = foreign
        bpy.context.view_layer.update()
        decisions = _measured_gate(monkeypatch)
        foreign.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
        assert decisions and False in decisions
    finally:
        bpy.data.objects.remove(foreign, do_unlink=True)
        bpy.data.meshes.remove(data, do_unlink=True)


def test_unknown_scene_and_empty_pending_updates_fall_back(configured_local, mesh_object):
    """No assumptions are made about another scene's ViewLayer or empty ID set."""
    _init(mesh_object)
    bpy.context.view_layer.update()
    assert runtime._can_skip_foreign_depsgraph_updates(bpy.context.scene) is False
    scene = bpy.data.scenes.new('GateOtherScene')
    try:
        assert runtime._can_skip_foreign_depsgraph_updates(scene) is False
    finally:
        bpy.data.scenes.remove(scene)


def test_two_stages_skip_only_when_every_owner_is_unrelated(configured_local, mesh_object, monkeypatch):
    """A foreign event skips both stages, but an event for either owner is processed."""
    from NodeForge.tests.blender.test_mesh_compute_event_lifecycle import _release_extra_owner

    first = _init(mesh_object)
    data = mesh_object.data.copy()
    second = bpy.data.objects.new('GateSecondStage', data)
    bpy.context.scene.collection.objects.link(second)
    foreign, foreign_data = _foreign_object()
    try:
        other = _init(second)
        bpy.context.view_layer.update()
        decisions = _measured_gate(monkeypatch)
        first_mesh, second_mesh = first.last_published, other.last_published
        foreign.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
        assert decisions and True in decisions
        assert first.last_published is first_mesh and other.last_published is second_mesh
        decisions.clear()
        socket = next(s for s in other.group.interface.items_tree
                      if s.item_type == 'SOCKET' and s.in_out == 'INPUT' and s.name == 'Amount')
        getattr(other.modifier.properties.inputs, socket.identifier).value = 7.0
        second.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
        assert decisions and False in decisions
        assert other.last_published is not second_mesh
        assert first.last_published is first_mesh
        assert _evaluated_positions(second)[0] == (7., 8., 9.)
    finally:
        _release_extra_owner(second)
        if any(m is data for m in bpy.data.meshes):
            bpy.data.meshes.remove(data, do_unlink=True)
        bpy.data.objects.remove(foreign, do_unlink=True)
        bpy.data.meshes.remove(foreign_data, do_unlink=True)


def test_root_gn_tree_edit_triggers_full_check(configured_local, mesh_object, monkeypatch):
    """A GN root tree update must not be mistaken for a foreign Object update."""
    binding = _init(mesh_object)
    decisions = _measured_gate(monkeypatch)
    binding.group.nodes.new('NodeReroute')
    binding.group.update_tag()
    bpy.context.view_layer.update()
    assert decisions and False in decisions
    assert binding.status == 'READY'
    assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
