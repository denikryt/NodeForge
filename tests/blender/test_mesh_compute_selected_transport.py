"""Blender regressions for the selected single-proxy Mesh Compute publication."""

from __future__ import annotations

import importlib.util

import bpy
import pytest

from NodeForge.compiler import create_mesh_compute_modifier
from NodeForge.blender import mesh_compute_runtime as runtime
from NodeForge.blender.mesh_compute_transports import direct_mesh_update
from test_mesh_compute_modifier_binding import SOURCE, configured_local, mesh_object


def test_direct_boundary_has_one_proxy_and_no_indexed_carrier(configured_local, mesh_object):
    """A root publishes one evaluated Mesh without carrier sampling nodes."""
    group = create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    refs = [node for node in group.nodes if node.bl_idname == 'GeometryNodeObjectInfo'
            and node.inputs['Object'].default_value is binding.prepared.result_proxy]
    assert len(refs) == 1
    assert not any(node.bl_idname == 'GeometryNodeSampleIndex' for node in group.nodes)
    assert binding.status == 'READY'
    assert len(binding.prepared.result_proxy.data.vertices) == len(mesh_object.data.vertices)


def test_single_proxy_prepare_failure_clears_output_without_partial_pointer(configured_local, mesh_object, monkeypatch):
    """Preparation exceptions leave the source untouched and GN output empty."""
    create_mesh_compute_modifier(SOURCE, mesh_object)
    binding = runtime.get_modifier_binding(mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    source_id = mesh_object.data.as_pointer()
    original_z = tuple(vertex.co.z for vertex in mesh_object.data.vertices)
    live_mesh_count = len(bpy.data.meshes)
    calls = []

    def fail_before_publication(*args, **kwargs):
        """Simulate failure after validation but before the proxy pointer assignment."""
        calls.append(True)
        raise RuntimeError('injected direct Mesh preparation failure')

    monkeypatch.setattr(direct_mesh_update, 'prepare_publication', fail_before_publication)
    with pytest.raises(RuntimeError, match='injected direct Mesh preparation failure'):
        runtime.evaluate_modifier_binding(mesh_object)
    assert calls == [True]
    assert binding.status == 'ERROR'
    assert len(binding.prepared.result_proxy.data.vertices) == 0
    assert mesh_object.data.as_pointer() == source_id
    assert tuple(vertex.co.z for vertex in mesh_object.data.vertices) == original_z
    assert len(bpy.data.meshes) <= live_mesh_count + 1  # One owned empty failure gate; never a half-built proxy.


def test_no_experimental_carrier_backends_remain():
    """Selected publication is importable; removed benchmark candidates are not."""
    parent = 'NodeForge.blender.mesh_compute_transports'
    assert importlib.util.find_spec(parent + '.direct_mesh_update') is not None
    assert importlib.util.find_spec(parent + '.attribute_transfer') is None
    assert importlib.util.find_spec(parent + '.cached_attribute_transfer') is None
    assert importlib.util.find_spec(parent + '._carrier') is None
