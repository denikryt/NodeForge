"""Validation of the opt-in live runtime benchmark and its invariants."""

from __future__ import annotations

import pytest

from NodeForge.tests.blender import benchmark_mesh_compute_live_scheduler as benchmark


def test_live_benchmark_rejects_empty_and_nonfinite_measurements():
    """Invalid timing samples cannot produce a misleading benchmark summary."""
    with pytest.raises(ValueError, match='at least one'):
        benchmark.summarize([])
    with pytest.raises(ValueError, match='finite nonnegative'):
        benchmark.summarize([0.1, float('nan')])
    with pytest.raises(ValueError, match='finite nonnegative'):
        benchmark.summarize([-1.0])


def test_live_benchmark_summary_keeps_raw_values_and_median():
    """Reported median and sample data preserve all recorded timing runs."""
    result = benchmark.summarize([1.0, 4.0, 2.0])
    assert result['median_ms'] == 2.0
    assert result['mean_ms'] == pytest.approx(7.0 / 3.0, abs=0.0001)
    assert result['samples_ms'] == [1.0, 4.0, 2.0]


def test_live_benchmark_rejects_bad_grid_before_blender_mutation():
    """Malformed configuration must not allocate any Blender datablocks."""
    import bpy
    before = {obj.as_pointer() for obj in bpy.data.objects}
    with pytest.raises(ValueError, match='side'):
        benchmark.run(2, 5, 1)
    with pytest.raises(ValueError, match='repeats'):
        benchmark.run(8, 0, 1)
    assert {obj.as_pointer() for obj in bpy.data.objects} == before
