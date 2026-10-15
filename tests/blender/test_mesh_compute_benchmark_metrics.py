"""Regression boundaries for experimental Mesh Compute timing instrumentation."""

from __future__ import annotations

from array import array

import pytest

from NodeForge.tests.blender import benchmark_mesh_compute_transport as benchmark


def test_physical_stage_accounting_accepts_complete_partition():
    """A complete measured source→consumer pass counts each operation once."""
    valid = {name: 2.0 for name in benchmark._PHASE_KEYS}
    valid['total_ms'] = 2.0 * len(benchmark._PHASE_KEYS)
    valid['unchanged_suffix_ms'] = 100.0  # Deliberately excluded from total.
    benchmark.assert_phase_accounting(valid)


def test_physical_stage_accounting_rejects_double_counting():
    """A missing or duplicated segment must fail before JSON publication."""
    invalid = {name: 2.0 for name in benchmark._PHASE_KEYS}
    invalid['total_ms'] = 2.0 * len(benchmark._PHASE_KEYS) - 2.0
    with pytest.raises(AssertionError, match='phase accounting'):
        benchmark.assert_phase_accounting(invalid)


def test_output_timing_restores_normalizer_on_failure():
    """Test-only monkeypatch must not affect later runtime validation on exceptions."""
    import importlib
    module = importlib.import_module('NodeForge.mesh_compute')
    before = module._normalized_floats
    with pytest.raises(ValueError, match='probe failure'):
        benchmark._time_normalization_outputs(lambda: (_ for _ in ()).throw(ValueError('probe failure')))
    assert module._normalized_floats is before


def test_output_timing_records_declared_array():
    """Profile wrapper observes output fields without changing returned buffers."""
    import importlib
    module = importlib.import_module('NodeForge.mesh_compute')
    before = module._normalized_floats
    normalized, fields, diagnostics = benchmark._time_normalization_outputs(
        lambda: module._normalized_floats(array('f', [2.0, 3.0]), 2, 'height'))
    assert normalized == array('f', [2.0, 3.0])
    assert fields['height'] >= 0
    assert diagnostics is None
    assert module._normalized_floats is before


def test_grid_side_means_exact_number_of_vertices():
    """Operator subdivisions differ from desired point count by one per axis."""
    source, mesh = benchmark._stock_mesh(8)
    try:
        assert len(mesh.vertices) == 64
    finally:
        import bpy
        benchmark._remove_id(bpy.data.objects, source)
        benchmark._remove_id(bpy.data.meshes, mesh)


def test_invalid_grid_size_rejected_before_blender_mutation():
    """Too-small grids must be rejected before allocating a source Object."""
    import bpy
    before = {obj.as_pointer() for obj in bpy.data.objects}
    with pytest.raises(ValueError, match='at least 3'):
        benchmark._stock_mesh(2)
    assert {obj.as_pointer() for obj in bpy.data.objects} == before
