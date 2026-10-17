"""Blender 5.2 bulk-position I/O regression tests for Mesh Compute."""

from array import array

import bpy
import pytest

from NodeForge.blender.mesh_compute_runtime import (
    EvaluatedMeshCompute, mesh_inputs_from_evaluated,
)
from NodeForge.blender.mesh_compute_transports.direct_mesh_update import prepare_publication
from NodeForge.mesh_compute import normalize_mesh_result


def test_bulk_position_roundtrip_preserves_topology_uv_and_source():
    """New output positions are visible in Blender without modifying source Mesh."""
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=5, y_subdivisions=4, size=5)
    obj = bpy.context.object
    source = obj.data
    if not source.uv_layers:
        source.uv_layers.new(name="UV Source")
    source.attributes.new("water", "FLOAT", "POINT")
    count = len(source.vertices)
    before = mesh_inputs_from_evaluated(source, ("water",))
    changed = array("f", before.positions)
    for index in range(count):
        changed[index * 3 + 2] = float(index) * 0.25
    updated = None
    try:
        computed = normalize_mesh_result(
            {"positions": changed, "point_attributes": {"water": array("f", [0.5]) * count}},
            vertex_count=count, writes=("water",),
        )
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        updated = prepare_publication(
            evaluated, bpy.context.evaluated_depsgraph_get(), EvaluatedMeshCompute(before, computed),
        )
        result = mesh_inputs_from_evaluated(updated, ("water",))
        assert result.positions == changed
        assert len(updated.polygons) == len(source.polygons)
        assert len(updated.uv_layers) == len(source.uv_layers)
        assert list(result.point_attributes["water"]) == [0.5] * count
        assert mesh_inputs_from_evaluated(source, ("water",)).positions == before.positions
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        if updated is not None:
            bpy.data.meshes.remove(updated)


def test_missing_position_attribute_fails_at_input_boundary():
    """Malformed evaluated sources are rejected before entering Python callbacks."""
    class FakeAttributes(dict):
        """Minimal evaluated Mesh attributes without the required built-in position."""

    class FakeMesh:
        """Synthetic mesh shape emulating a broken Blender position invariant."""

        vertices = [object()]
        attributes = FakeAttributes()

    with pytest.raises(ValueError, match="position"):
        mesh_inputs_from_evaluated(FakeMesh(), ())
