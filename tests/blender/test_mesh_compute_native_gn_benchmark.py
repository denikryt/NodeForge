"""Real Blender equivalence checks for native GN versus the NumPy stencil."""

from array import array

import bpy
import numpy as np
import pytest

from NodeForge.mesh_compute import MeshInputs, normalize_mesh_result
from NodeForge.tests.blender.benchmark_mesh_compute_transport import _kernel, _stock_mesh
from NodeForge.tests.blender.benchmark_native_gn_mesh_compute import (
    build_native_stencil_group, validate_native_output,
)


def _reference(mesh, steps):
    """Normalize the same starting mesh through the production float32 contract."""
    coords = array('f', [0]) * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get('co', coords)
    heights = array('f', (coords[i] for i in range(2, len(coords), 3)))
    return normalize_mesh_result(_kernel(MeshInputs(coords, {'height': heights}, len(mesh.vertices)), steps),
                                 vertex_count=len(mesh.vertices), writes=('height','water'))


def test_native_repeat_stencil_matches_numpy_with_periodic_neighbors():
    """GN Repeat output agrees with NumPy height, water, and XYZ at five steps."""
    obj, mesh = _stock_mesh(12)
    group = build_native_stencil_group(12, 5)
    obj.modifiers.new('native', 'NODES').node_group = group
    try:
        errors = validate_native_output(obj, _reference(mesh, 5))
        assert max(errors.values()) < 2e-4
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.node_groups.remove(group, do_unlink=True)
        bpy.data.meshes.remove(mesh, do_unlink=True)


def test_native_gn_validator_rejects_different_algorithm_iteration_count():
    """A different stencil iteration count must not pass numeric equivalence."""
    obj, mesh = _stock_mesh(12)
    group = build_native_stencil_group(12, 40)
    obj.modifiers.new('native', 'NODES').node_group = group
    try:
        with pytest.raises(AssertionError, match='error'):
            validate_native_output(obj, _reference(mesh, 0), tolerance=1e-6)
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.node_groups.remove(group, do_unlink=True)
        bpy.data.meshes.remove(mesh, do_unlink=True)
