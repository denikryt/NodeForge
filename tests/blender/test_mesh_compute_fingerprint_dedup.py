"""Fingerprint regressions for Blender 5.2 duplicated UV/material data access."""

import bpy
import pytest

from NodeForge.blender.mesh_compute_runtime import _publication_fingerprint


@pytest.fixture
def grid():
    """Create an independent grid and release its temporary datablocks."""
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=5, y_subdivisions=5, size=3)
    obj = bpy.context.object
    materials = [bpy.data.materials.new(f"MeshComputeHashMat{idx}") for idx in (0, 1)]
    for material in materials:
        obj.data.materials.append(material)
    try:
        yield obj.data
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        for material in materials:
            bpy.data.materials.remove(material, do_unlink=True)


def test_native_uv_changes_and_multiple_maps_are_observed(grid):
    """Hash all UV maps once while detecting edits and namespace changes."""
    assert grid.uv_layers
    original = _publication_fingerprint(grid)
    assert original == _publication_fingerprint(grid)
    grid.uv_layers[0].data[0].uv = (0.31, 0.43)
    changed = _publication_fingerprint(grid)
    assert changed != original
    extra = grid.uv_layers.new(name="SecondUV")
    assert _publication_fingerprint(grid) != changed
    before = _publication_fingerprint(grid)
    extra.data[2].uv = (0.56, 0.71)
    assert _publication_fingerprint(grid) != before
    before = _publication_fingerprint(grid)
    extra.name = "RenamedUV"
    assert _publication_fingerprint(grid) != before


def test_material_indices_and_defaults_are_observed(grid):
    """Material edits invalidate the hash with and without the native layer."""
    assert grid.attributes.get("material_index") is None
    original = _publication_fingerprint(grid)
    assert original == _publication_fingerprint(grid)
    grid.polygons[1].material_index = 1
    native = grid.attributes.get("material_index")
    assert native is not None and native.domain == "FACE" and native.data_type == "INT"
    changed = _publication_fingerprint(grid)
    assert changed != original
    grid.polygons[2].material_index = 1
    assert _publication_fingerprint(grid) != changed
    changed = _publication_fingerprint(grid)
    grid.materials[1] = grid.materials[0]
    assert _publication_fingerprint(grid) != changed


class _WithoutNativeAttribute:
    """Simulate an evaluated mesh lacking one native-attribute lookup."""

    def __init__(self, mesh, name):
        self.mesh = mesh
        self.attributes = _MissingLookup(mesh.attributes, name)

    def __getattr__(self, name):
        return getattr(self.mesh, name)


class _MissingLookup:
    """Keep native enumeration while forcing one conservative RNA fallback."""

    def __init__(self, attributes, name):
        self.attributes = attributes
        self.name = name

    def __iter__(self):
        return iter(self.attributes)

    def get(self, name):
        if name == self.name:
            return None
        return self.attributes.get(name)


def test_uv_fallback_when_native_lookup_unavailable(grid):
    """A non-native UV layer must still invalidate publication freshness."""
    fallback = _WithoutNativeAttribute(grid, grid.uv_layers[0].name)
    original = _publication_fingerprint(fallback)
    grid.uv_layers[0].data[0].uv = (0.19, 0.87)
    assert _publication_fingerprint(fallback) != original


def test_material_fallback_when_native_lookup_unavailable(grid):
    """Preserve RNA material indices if a native layer cannot be resolved."""
    grid.polygons[1].material_index = 1
    fallback = _WithoutNativeAttribute(grid, "material_index")
    original = _publication_fingerprint(fallback)
    grid.polygons[1].material_index = 0
    assert _publication_fingerprint(fallback) != original


def test_topology_and_undeclared_layers_still_invalidate(grid):
    """Omitting duplicated reads must not alter other publication invalidation."""
    original = _publication_fingerprint(grid)
    grid.attributes.new("custom", "FLOAT", "POINT").data[0].value = 0.4
    assert _publication_fingerprint(grid) != original
    changed = _publication_fingerprint(grid)
    grid.edges[0].use_seam = True
    assert _publication_fingerprint(grid) != changed
    changed = _publication_fingerprint(grid)
    grid.polygons[0].use_smooth = True
    assert _publication_fingerprint(grid) != changed
